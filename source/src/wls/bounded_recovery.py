from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .experiments import DEFAULT_ACCEPTANCE, IsolatedToolHarness, write_json_atomic
from .learning import LearningSystem
from .schemas import CandidateStatus, RiskLevel, digest_json, new_id, utc_now


DEFAULT_RECOVERY_TOOLS = [
    "noop",
    "read_file",
    "list_directory",
    "emit_note",
]


class FailureRecoveryEngine:
    """Compare bounded corrective candidates against frozen failure baselines."""

    SUPPORTED_STRATEGIES = {"contract_recovery", "fixture_recovery"}

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        config: RuntimeConfig,
        learning: LearningSystem,
    ) -> None:
        self.db = db
        self.ledger = ledger
        self.config = config
        self.learning = learning

    @property
    def minimum_occurrences(self) -> int:
        return int(getattr(self.config, "recovery_validation_min_occurrences", 3))

    @property
    def allowed_tools(self) -> list[str]:
        return list(
            getattr(
                self.config,
                "recovery_validation_allowed_tools",
                DEFAULT_RECOVERY_TOOLS,
            )
        )

    def run(
        self, candidate_id: str, strategy: str = "contract_recovery"
    ) -> dict[str, Any]:
        if strategy not in self.SUPPORTED_STRATEGIES:
            raise ValueError(f"unsupported recovery strategy: {strategy}")
        candidate = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?", (candidate_id,)
        )
        if candidate is None:
            raise KeyError(candidate_id)
        if candidate["candidate_type"] not in {"failure_repair", "failure_recovery"}:
            raise ValueError("candidate is not a repeated-failure candidate")
        current = CandidateStatus(str(candidate["status"]))
        if current not in {CandidateStatus.PROPOSED, CandidateStatus.SANDBOXED}:
            raise ValueError(f"recovery experiment cannot start from {current.value}")
        source_ids = [str(v) for v in json.loads(candidate["source_ids_json"])]
        actions = self._source_actions(source_ids)
        if len(actions) < self.minimum_occurrences:
            raise ValueError("insufficient repeated failures")
        unsupported = sorted(
            {str(row["tool"]) for row in actions} - set(self.allowed_tools)
        )
        if unsupported:
            raise ValueError(f"unsupported tools: {unsupported}")

        experiment_id = new_id("recovery_exp")
        artifact_dir = (
            self.config.sandbox_path / "recovery-experiments" / experiment_id
        ).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=False)
        frozen = [self._freeze_action(row) for row in actions]
        baseline = {
            "occurrences": len(frozen),
            "observed_failed": len(frozen),
            "observed_success_rate": 0.0,
            "source": "DIRECT",
        }
        manifest = {
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "strategy": strategy,
            "created_at": utc_now(),
            "source_action_ids": source_ids,
            "source_digests": [digest_json(item) for item in frozen],
            "baseline": baseline,
            "claim": "bounded declarative comparison only",
        }
        manifest_sha256 = digest_json(manifest)
        write_json_atomic(artifact_dir / "manifest.json", manifest)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO recovery_experiments(
                    experiment_id,candidate_id,strategy,status,manifest_json,
                    baseline_json,result_json,artifact_path,started_at,finished_at
                ) VALUES (?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    experiment_id,
                    candidate_id,
                    strategy,
                    "RUNNING",
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                    None,
                    str(artifact_dir),
                    utc_now(),
                ),
            )
            self.ledger.append(
                "recovery_experiment_started",
                {
                    "experiment_id": experiment_id,
                    "candidate_id": candidate_id,
                    "strategy": strategy,
                    "manifest_sha256": manifest_sha256,
                },
                connection,
            )
        if current == CandidateStatus.PROPOSED:
            self.learning.transition_candidate(
                candidate_id,
                CandidateStatus.SANDBOXED,
                {
                    "experiment_id": experiment_id,
                    "manifest_sha256": manifest_sha256,
                },
            )

        cases = [
            self._run_case(action, strategy, artifact_dir / f"case-{i + 1:03d}")
            for i, action in enumerate(frozen)
        ]
        baseline_passes = sum(bool(item["baseline"]["passed"]) for item in cases)
        candidate_passes = sum(bool(item["candidate"]["passed"]) for item in cases)
        improved = sum(bool(item["improved"]) for item in cases)
        regressions = sum(
            bool(item["baseline"]["passed"])
            and not bool(item["candidate"]["passed"])
            for item in cases
        )
        total = len(cases)
        baseline_rate = baseline_passes / total if total else 0.0
        candidate_rate = candidate_passes / total if total else 0.0
        passed = (
            total >= self.minimum_occurrences
            and candidate_passes == total
            and candidate_rate > baseline_rate
            and improved > 0
            and regressions == 0
        )
        result = {
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "strategy": strategy,
            "finished_at": utc_now(),
            "passed": passed,
            "cases": total,
            "baseline_pass_rate": baseline_rate,
            "candidate_pass_rate": candidate_rate,
            "improved_cases": improved,
            "regressions": regressions,
            "case_results": cases,
        }
        result_sha256 = digest_json(result)
        write_json_atomic(artifact_dir / "result.json", result)
        status = "PASSED" if passed else "FAILED"
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE recovery_experiments SET status=?,result_json=?,finished_at=? WHERE experiment_id=?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    result["finished_at"],
                    experiment_id,
                ),
            )
            evidence_id = self.ledger.append(
                "recovery_experiment_completed",
                {
                    "experiment_id": experiment_id,
                    "candidate_id": candidate_id,
                    "status": status,
                    "result_sha256": result_sha256,
                    "baseline_pass_rate": baseline_rate,
                    "candidate_pass_rate": candidate_rate,
                },
                connection,
            )
        if passed:
            self.learning.transition_candidate(
                candidate_id,
                CandidateStatus.VALIDATED,
                {
                    "experiment_id": experiment_id,
                    "result_sha256": result_sha256,
                    "evidence_id": evidence_id,
                    "improved_cases": improved,
                    "regressions": regressions,
                },
            )
        return {
            **result,
            "status": status,
            "manifest_sha256": manifest_sha256,
            "result_sha256": result_sha256,
            "artifact_path": str(artifact_dir),
            "evidence_id": evidence_id,
        }

    def list_experiments(
        self, candidate_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        if candidate_id:
            rows = self.db.query_all(
                "SELECT * FROM recovery_experiments WHERE candidate_id=? ORDER BY started_at DESC LIMIT ?",
                (candidate_id, limit),
            )
        else:
            rows = self.db.query_all(
                "SELECT * FROM recovery_experiments ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        return [
            {
                "experiment_id": row["experiment_id"],
                "candidate_id": row["candidate_id"],
                "strategy": row["strategy"],
                "status": row["status"],
                "result": json.loads(row["result_json"])
                if row["result_json"]
                else None,
                "artifact_path": row["artifact_path"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
            }
            for row in rows
        ]

    def _run_case(
        self, action: dict[str, Any], strategy: str, case_root: Path
    ) -> dict[str, Any]:
        baseline = IsolatedToolHarness(
            case_root / "baseline", self.allowed_tools
        ).execute(
            action["tool"],
            action["arguments"],
            action["purpose"],
            action["acceptance"],
            RiskLevel(action["risk"]),
            [],
            False,
        )
        recovered = self._recover(action, strategy)
        candidate = IsolatedToolHarness(
            case_root / "candidate", self.allowed_tools
        ).execute(
            recovered["tool"],
            recovered["arguments"],
            recovered["purpose"],
            recovered["acceptance"],
            RiskLevel(recovered["risk"]),
            recovered.get("fixtures", []),
        )
        result = {
            "source_action_id": action["action_id"],
            "baseline": baseline,
            "candidate": candidate,
            "recovery": recovered["recovery_summary"],
            "improved": not baseline["passed"] and bool(candidate["passed"]),
        }
        write_json_atomic(case_root / "result.json", result)
        return result

    def _source_actions(self, source_ids: list[str]) -> list[Any]:
        rows: list[Any] = []
        for source_id in source_ids:
            row = self.db.query_one(
                "SELECT * FROM actions WHERE action_id=? AND status IN ('FAILED','UNKNOWN_SIDE_EFFECT')",
                (source_id,),
            )
            if row is not None:
                rows.append(row)
        return rows

    @staticmethod
    def _freeze_action(row: Any) -> dict[str, Any]:
        return {
            "action_id": str(row["action_id"]),
            "tool": str(row["tool"]),
            "arguments": json.loads(row["arguments_json"]),
            "purpose": str(row["purpose"]),
            "acceptance": json.loads(row["acceptance_json"]),
            "risk": str(row["risk"]),
            "error": str(row["error"] or ""),
        }

    @staticmethod
    def _recover(action: dict[str, Any], strategy: str) -> dict[str, Any]:
        recovered = dict(action)
        recovered["arguments"] = dict(action["arguments"])
        recovered["fixtures"] = []
        if strategy == "contract_recovery":
            corrected = list(DEFAULT_ACCEPTANCE.get(action["tool"], []))
            if not corrected:
                raise ValueError(f"no deterministic contract for {action['tool']}")
            recovered["acceptance"] = corrected
            recovered["recovery_summary"] = {
                "type": strategy,
                "before": action["acceptance"],
                "after": corrected,
            }
        else:
            path = str(action["arguments"].get("path", ""))
            if action["tool"] == "read_file":
                recovered["fixtures"] = [
                    {"path": path, "content": "WLS recovery fixture\n"}
                ]
            elif action["tool"] == "list_directory":
                recovered["fixtures"] = [{"path": path, "directory": True}]
            else:
                raise ValueError("fixture recovery supports read/list only")
            recovered["acceptance"] = list(
                action["acceptance"] or DEFAULT_ACCEPTANCE[action["tool"]]
            )
            recovered["recovery_summary"] = {
                "type": strategy,
                "fixture_count": len(recovered["fixtures"]),
            }
        return recovered
