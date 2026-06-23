from __future__ import annotations

from collections import defaultdict
from typing import Any
import json

from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .experiments import IsolatedToolHarness, write_json_atomic
from .schemas import CandidateStatus, RiskLevel, digest_json, new_id, utc_now
from .skills import SkillLibrary


class SkillExperimentRunner:
    """Replay learned declarative workflows in an isolated local harness."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        skills: SkillLibrary,
        config: RuntimeConfig,
    ) -> None:
        self.db = db
        self.ledger = ledger
        self.skills = skills
        self.config = config

    @property
    def allowed_tools(self) -> list[str]:
        return list(
            getattr(
                self.config,
                "skill_validation_allowed_tools",
                ["noop", "read_file", "list_directory", "emit_note"],
            )
        )

    def run(self, skill_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM skills WHERE skill_id=?", (skill_id,))
        if row is None:
            raise KeyError(skill_id)
        current = CandidateStatus(str(row["status"]))
        if current not in {CandidateStatus.PROPOSED, CandidateStatus.SANDBOXED}:
            raise ValueError(f"skill experiment cannot start from {current.value}")
        definition = json.loads(row["definition_json"])
        cases, source_rows = self._cases(definition)
        if not cases:
            cases = [
                {
                    "name": "default-contract-replay",
                    "source_plan_id": None,
                    "step_arguments": [
                        dict(step.get("arguments", {}))
                        for step in definition.get("steps", [])
                    ],
                }
            ]
        maximum = int(getattr(self.config, "skill_validation_max_cases", 20))
        minimum = int(getattr(self.config, "skill_validation_min_cases", 3))
        cases = cases[:maximum]
        source_successes = sum(
            1 for item in source_rows if item["status"] == "SUCCEEDED"
        )
        baseline_rate = source_successes / len(source_rows) if source_rows else 1.0
        experiment_id = new_id("skill_exp")
        artifact_dir = (
            self.config.sandbox_path / "skill-experiments" / experiment_id
        ).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "experiment_id": experiment_id,
            "skill_id": skill_id,
            "skill_version": int(row["version"]),
            "definition_sha256": digest_json(definition),
            "created_at": utc_now(),
            "allowed_tools": sorted(self.allowed_tools),
            "minimum_cases": minimum,
            "cases": cases,
            "baseline": {
                "source_actions": len(source_rows),
                "source_plans": len({str(item["plan_id"]) for item in source_rows}),
                "historical_success_rate": baseline_rate,
                "source": "DIRECT" if source_rows else "INFERENCE",
            },
        }
        manifest_sha256 = digest_json(manifest)
        write_json_atomic(artifact_dir / "manifest.json", manifest)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO skill_experiments(
                    experiment_id,skill_id,skill_version,status,manifest_json,
                    baseline_json,result_json,artifact_path,started_at,finished_at
                ) VALUES (?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    experiment_id,
                    skill_id,
                    int(row["version"]),
                    "RUNNING",
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    json.dumps(manifest["baseline"], ensure_ascii=False, sort_keys=True),
                    None,
                    str(artifact_dir),
                    utc_now(),
                ),
            )
            self.ledger.append(
                "skill_experiment_started",
                {
                    "experiment_id": experiment_id,
                    "skill_id": skill_id,
                    "manifest_sha256": manifest_sha256,
                },
                connection,
            )
        if current == CandidateStatus.PROPOSED:
            self.skills.transition(
                skill_id,
                CandidateStatus.SANDBOXED,
                {
                    "experiment_id": experiment_id,
                    "manifest_sha256": manifest_sha256,
                },
            )
        results = [
            self._run_case(definition, case, artifact_dir / f"case-{index + 1:03d}")
            for index, case in enumerate(cases)
        ]
        passed_cases = sum(bool(item["passed"]) for item in results)
        total = len(results)
        candidate_rate = passed_cases / total if total else 0.0
        regressions = max(0, int(round(baseline_rate * total)) - passed_cases)
        passed = (
            total >= minimum
            and passed_cases == total
            and candidate_rate >= baseline_rate
            and regressions == 0
        )
        result = {
            "experiment_id": experiment_id,
            "skill_id": skill_id,
            "finished_at": utc_now(),
            "passed": passed,
            "passed_cases": passed_cases,
            "candidate_cases": total,
            "candidate_pass_rate": candidate_rate,
            "baseline_pass_rate": baseline_rate,
            "regressions": regressions,
            "case_results": results,
        }
        result_sha256 = digest_json(result)
        write_json_atomic(artifact_dir / "result.json", result)
        status = "PASSED" if passed else "FAILED"
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE skill_experiments SET status=?,result_json=?,finished_at=? WHERE experiment_id=?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    result["finished_at"],
                    experiment_id,
                ),
            )
            evidence_id = self.ledger.append(
                "skill_experiment_completed",
                {
                    "experiment_id": experiment_id,
                    "skill_id": skill_id,
                    "status": status,
                    "result_sha256": result_sha256,
                },
                connection,
            )
        if passed:
            self.skills.transition(
                skill_id,
                CandidateStatus.VALIDATED,
                {
                    "experiment_id": experiment_id,
                    "result_sha256": result_sha256,
                    "evidence_id": evidence_id,
                },
            )
        return {
            **result,
            "status": status,
            "artifact_path": str(artifact_dir),
            "manifest_sha256": manifest_sha256,
            "result_sha256": result_sha256,
            "evidence_id": evidence_id,
        }

    def list_experiments(
        self, skill_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        if skill_id:
            rows = self.db.query_all(
                "SELECT * FROM skill_experiments WHERE skill_id=? ORDER BY started_at DESC LIMIT ?",
                (skill_id, limit),
            )
        else:
            rows = self.db.query_all(
                "SELECT * FROM skill_experiments ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        return [
            {
                "experiment_id": row["experiment_id"],
                "skill_id": row["skill_id"],
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
        self, definition: dict[str, Any], case: dict[str, Any], case_root
    ) -> dict[str, Any]:
        harness = IsolatedToolHarness(case_root / "fs", self.allowed_tools)
        overrides = list(case.get("step_arguments", []))
        steps = []
        for index, step in enumerate(definition.get("steps", [])):
            arguments = dict(step.get("arguments", {}))
            if index < len(overrides) and isinstance(overrides[index], dict):
                arguments = dict(overrides[index])
            steps.append(
                harness.execute(
                    tool=str(step.get("tool", "")),
                    arguments=arguments,
                    purpose=str(
                        step.get("purpose", definition.get("description", "skill"))
                    ),
                    acceptance=[str(value) for value in step.get("acceptance", [])],
                    risk=RiskLevel(
                        str(definition.get("risk", RiskLevel.READ.value))
                    ),
                )
            )
        result = {
            "name": str(case.get("name", case_root.name)),
            "source_plan_id": case.get("source_plan_id"),
            "passed": bool(steps) and all(item.get("passed") for item in steps),
            "steps": steps,
        }
        write_json_atomic(case_root / "result.json", result)
        return result

    def _cases(
        self, definition: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[Any]]:
        rows = []
        for action_id in definition.get("source_episode_ids", []):
            row = self.db.query_one(
                "SELECT * FROM actions WHERE action_id=?", (str(action_id),)
            )
            if row is not None:
                rows.append(row)
        grouped: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            grouped[str(row["plan_id"])].append(row)
        cases = [
            {
                "name": f"historical-plan-{index + 1}",
                "source_plan_id": plan_id,
                "step_arguments": [
                    json.loads(item["arguments_json"]) for item in plan_rows
                ],
            }
            for index, (plan_id, plan_rows) in enumerate(grouped.items())
        ]
        return cases, rows
