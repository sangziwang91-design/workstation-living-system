from __future__ import annotations

from typing import Any
import json

from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .experiments import IsolatedToolHarness, write_json_atomic
from .schemas import (
    CandidateStatus,
    ExperimentStatus,
    RiskLevel,
    SkillDefinition,
    digest_json,
    new_id,
    utc_now,
)
from .skills import SkillLibrary


class SkillExperimentRunner:
    """Machine-verifiable isolated execution for declarative skills."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        skills: SkillLibrary,
        config: RuntimeConfig,
    ):
        self.db = db
        self.ledger = ledger
        self.skills = skills
        self.config = config

    def run(self, skill_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM skills WHERE skill_id=?", (skill_id,))
        if row is None:
            raise KeyError(skill_id)
        current = CandidateStatus(str(row["status"]))
        if current not in {CandidateStatus.PROPOSED, CandidateStatus.SANDBOXED}:
            raise ValueError(
                f"skill experiment requires PROPOSED or SANDBOXED, got {current.value}"
            )
        definition = json.loads(row["definition_json"])
        self.skills.validate_definition(self._definition_from_json(definition))
        cases = [dict(item) for item in definition.get("validation_cases", [])]
        if not cases:
            cases = [
                {
                    "name": "default-contract-replay",
                    "step_arguments": [
                        dict(step.get("arguments", {}))
                        for step in definition.get("steps", [])
                    ],
                    "fixtures": [],
                    "synthetic": True,
                }
            ]
        cases = cases[: self.config.skill_validation_max_cases]
        experiment_id = new_id("skill_exp")
        artifact_dir = (
            self.config.sandbox_path / "skill-experiments" / experiment_id
        ).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=False)
        baseline = self._baseline(definition, cases)
        manifest = {
            "experiment_id": experiment_id,
            "skill_id": skill_id,
            "skill_version": int(row["version"]),
            "definition_sha256": digest_json(definition),
            "created_at": utc_now(),
            "allowed_tools": sorted(self.config.skill_validation_allowed_tools),
            "minimum_cases": self.config.skill_validation_min_cases,
            "cases": cases,
            "baseline": baseline,
            "claim": "isolated contract validation only",
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
                    ExperimentStatus.RUNNING.value,
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    json.dumps(baseline, ensure_ascii=False, sort_keys=True),
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

        results: list[dict[str, Any]] = []
        for index, case in enumerate(cases):
            case_root = artifact_dir / f"case-{index + 1:03d}"
            harness = IsolatedToolHarness(
                case_root / "fs", self.config.skill_validation_allowed_tools
            )
            step_results: list[dict[str, Any]] = []
            overrides = case.get("step_arguments", [])
            for step_index, step in enumerate(definition.get("steps", [])):
                arguments = dict(step.get("arguments", {}))
                if step_index < len(overrides) and isinstance(
                    overrides[step_index], dict
                ):
                    arguments = dict(overrides[step_index])
                step_results.append(
                    harness.execute(
                        tool=str(step.get("tool", "")),
                        arguments=arguments,
                        purpose=str(step.get("purpose", definition.get("name", "skill"))),
                        acceptance=[str(v) for v in step.get("acceptance", [])],
                        risk=RiskLevel(str(definition.get("risk", RiskLevel.READ.value))),
                        fixtures=[dict(v) for v in case.get("fixtures", []) if isinstance(v, dict)],
                    )
                )
            case_result = {
                "name": str(case.get("name", f"case-{index + 1}")),
                "passed": bool(step_results)
                and all(item.get("passed") for item in step_results),
                "steps": step_results,
            }
            write_json_atomic(case_root / "result.json", case_result)
            results.append(case_result)

        passed_cases = sum(1 for item in results if item["passed"])
        total_cases = len(cases)
        candidate_rate = passed_cases / total_cases if total_cases else 0.0
        baseline_rate = float(baseline["historical_success_rate"])
        passed = (
            total_cases >= self.config.skill_validation_min_cases
            and passed_cases == total_cases
            and candidate_rate >= baseline_rate
        )
        result = {
            "experiment_id": experiment_id,
            "skill_id": skill_id,
            "finished_at": utc_now(),
            "passed": passed,
            "passed_cases": passed_cases,
            "candidate_cases": total_cases,
            "candidate_pass_rate": candidate_rate,
            "baseline_pass_rate": baseline_rate,
            "regressions": max(0, int(round(baseline_rate * total_cases)) - passed_cases),
            "case_results": results,
        }
        result_sha256 = digest_json(result)
        write_json_atomic(artifact_dir / "result.json", result)
        status = ExperimentStatus.PASSED if passed else ExperimentStatus.FAILED
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE skill_experiments SET status=?,result_json=?,finished_at=? WHERE experiment_id=?",
                (
                    status.value,
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
                    "status": status.value,
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
            "status": status.value,
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
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "artifact_path": row["artifact_path"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
            }
            for row in rows
        ]

    def _baseline(
        self, definition: dict[str, Any], cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        rows: list[Any] = []
        for source_id in definition.get("source_episode_ids", []):
            row = self.db.query_one(
                "SELECT action_id,plan_id,status,tool FROM actions WHERE action_id=?",
                (str(source_id),),
            )
            if row is not None:
                rows.append(row)
        successes = sum(1 for row in rows if row["status"] == "SUCCEEDED")
        return {
            "source_action_count": len(rows),
            "source_plan_count": len({str(row["plan_id"]) for row in rows}),
            "historical_success_rate": successes / len(rows) if rows else 1.0,
            "validation_case_count": len(cases),
            "source_status": "DIRECT" if rows else "INFERENCE",
        }

    @staticmethod
    def _definition_from_json(definition: dict[str, Any]) -> SkillDefinition:
        data = dict(definition)
        data["risk"] = RiskLevel(str(data.get("risk", RiskLevel.READ.value)))
        data["status"] = CandidateStatus(
            str(data.get("status", CandidateStatus.PROPOSED.value))
        )
        allowed = set(SkillDefinition.__dataclass_fields__)
        return SkillDefinition(**{k: v for k, v in data.items() if k in allowed})
