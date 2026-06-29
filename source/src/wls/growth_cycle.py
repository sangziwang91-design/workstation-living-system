from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
import json

from .experiments import DEFAULT_ACCEPTANCE, IsolatedToolHarness, write_json_atomic
from .schemas import (
    CandidateStatus,
    Goal,
    RiskLevel,
    SkillDefinition,
    digest_json,
    new_id,
    utc_now,
)
from .text import tokens

if TYPE_CHECKING:
    from .runtime import LivingSystem


ALLOWED_EXPERIMENT_TOOLS = {
    "noop",
    "read_file",
    "list_directory",
    "write_file",
    "emit_note",
}


@dataclass(slots=True)
class GrowthCycleRecord:
    growth_cycle_id: str
    failure_candidate_id: str
    recovery_experiment_id: str | None
    skill_id: str | None
    skill_experiment_id: str | None
    status: str


class GrowthCycleManager:
    """Canonical failure -> skill -> reuse -> retain/rollback lifecycle.

    The manager never bypasses policy or approval. Sandbox results can validate a
    proposal, but approval and promotion still require an explicit human actor and
    authorization reference. Reuse is executed through the canonical runtime.
    """

    def __init__(self, runtime: LivingSystem) -> None:
        self.runtime = runtime
        self.db = runtime.db
        self.ledger = runtime.ledger
        self.skills = runtime.skills
        self.learning = runtime.learning
        self.config = runtime.config

    def run_recovery_experiment(
        self,
        candidate_id: str,
        strategy: str = "contract_recovery",
    ) -> dict[str, Any]:
        if strategy not in {"contract_recovery", "fixture_recovery"}:
            raise ValueError(f"unsupported recovery strategy: {strategy}")
        candidate = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?", (candidate_id,)
        )
        if candidate is None:
            raise KeyError(candidate_id)
        if candidate["candidate_type"] != "failure_repair":
            raise ValueError("candidate is not a failure_repair")
        current = CandidateStatus(str(candidate["status"]))
        if current not in {CandidateStatus.PROPOSED, CandidateStatus.SANDBOXED}:
            raise ValueError(f"recovery experiment cannot start from {current.value}")
        source_ids = [str(value) for value in json.loads(candidate["source_ids_json"])]
        actions = self._source_failure_actions(source_ids)
        if len(actions) < 3:
            raise ValueError("recovery experiment requires at least three failures")
        unsupported = sorted({str(row["tool"]) for row in actions} - ALLOWED_EXPERIMENT_TOOLS)
        if unsupported:
            raise ValueError(f"unsupported recovery tools: {unsupported}")

        experiment_id = new_id("recovery_exp")
        artifact_dir = (
            self.config.sandbox_path / "growth" / "recovery" / experiment_id
        ).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=False)
        frozen_actions = [self._freeze_action(row) for row in actions]
        baseline = {
            "observed_failures": len(frozen_actions),
            "observed_success_rate": 0.0,
            "source": "DIRECT",
            "source_action_ids": source_ids,
        }
        manifest = {
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "strategy": strategy,
            "created_at": utc_now(),
            "source_digests": [digest_json(item) for item in frozen_actions],
            "acceptance_thresholds": {
                "minimum_cases": 3,
                "required_candidate_success_rate": 1.0,
                "minimum_improved_cases": 1,
                "maximum_regressions": 0,
            },
            "baseline": baseline,
            "sandbox_root": str(artifact_dir),
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
            self._run_recovery_case(
                action, strategy, artifact_dir / f"case-{index + 1:03d}"
            )
            for index, action in enumerate(frozen_actions)
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
            total >= 3
            and candidate_rate == 1.0
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
                    "regressions": regressions,
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

    def propose_skill_from_recovery(
        self,
        candidate_id: str,
        recovery_experiment_id: str,
        name: str | None = None,
    ) -> dict[str, str]:
        candidate = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?", (candidate_id,)
        )
        if candidate is None:
            raise KeyError(candidate_id)
        if candidate["status"] != CandidateStatus.VALIDATED.value:
            raise ValueError("failure candidate must be VALIDATED")
        experiment = self.db.query_one(
            """
            SELECT * FROM recovery_experiments
            WHERE experiment_id=? AND candidate_id=? AND status='PASSED'
            """,
            (recovery_experiment_id, candidate_id),
        )
        if experiment is None or not experiment["result_json"]:
            raise ValueError("passed recovery experiment not found")
        result = json.loads(experiment["result_json"])
        source_ids = [str(value) for value in json.loads(candidate["source_ids_json"])]
        source_actions = self._source_failure_actions(source_ids)
        if not source_actions:
            raise ValueError("recovery candidate has no source failures")
        candidate_specs = [item["candidate_spec"] for item in result["case_results"]]
        signatures = {digest_json(item) for item in candidate_specs}
        if len(signatures) != 1:
            raise ValueError("recovery result does not define one stable skill contract")
        spec = candidate_specs[0]
        source_action = source_actions[0]
        trigger_terms = sorted(
            tokens(
                " ".join(
                    [
                        str(source_action["purpose"]),
                        str(candidate["title"]),
                        str(spec["tool"]),
                        "recovered workflow",
                    ]
                )
            )
        )[:20]
        if not trigger_terms:
            trigger_terms = [str(spec["tool"])]
        skill_name = name or (
            f"recovered_{spec['tool']}_{digest_json(spec)[:12]}"[:100]
        )
        skill = SkillDefinition(
            name=skill_name,
            description=(
                f"Recovery skill derived from candidate {candidate_id} and "
                f"experiment {recovery_experiment_id}."
            ),
            trigger_terms=trigger_terms,
            steps=[
                {
                    "tool": spec["tool"],
                    "arguments": dict(spec["arguments"]),
                    "purpose": str(spec["purpose"]),
                    "acceptance": list(spec["acceptance"]),
                }
            ],
            risk=RiskLevel(str(spec["risk"])),
            status=CandidateStatus.PROPOSED,
            source_episode_ids=[
                *source_ids,
                candidate_id,
                recovery_experiment_id,
            ],
        )
        skill_id = self.skills.add(skill)
        growth_cycle_id = new_id("growth")
        baseline = {
            "recovery_experiment_id": recovery_experiment_id,
            "baseline_pass_rate": result["baseline_pass_rate"],
            "candidate_pass_rate": result["candidate_pass_rate"],
            "regressions": result["regressions"],
            "source_action_ids": source_ids,
        }
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO growth_cycles(
                    growth_cycle_id,failure_candidate_id,recovery_experiment_id,
                    skill_id,skill_experiment_id,status,approval_json,promotion_json,
                    baseline_json,reuse_json,measurement_json,rollback_json,created_at,updated_at
                ) VALUES (?,?,?,?,?,'SKILL_PROPOSED',NULL,NULL,?,NULL,NULL,NULL,?,?)
                """,
                (
                    growth_cycle_id,
                    candidate_id,
                    recovery_experiment_id,
                    skill_id,
                    None,
                    json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            self.ledger.append(
                "growth_skill_proposed",
                {
                    "growth_cycle_id": growth_cycle_id,
                    "failure_candidate_id": candidate_id,
                    "recovery_experiment_id": recovery_experiment_id,
                    "skill_id": skill_id,
                    "skill_definition_sha256": digest_json(skill.to_dict()),
                },
                connection,
            )
        return {"growth_cycle_id": growth_cycle_id, "skill_id": skill_id}

    def validate_skill(self, growth_cycle_id: str) -> dict[str, Any]:
        growth = self._growth_row(growth_cycle_id)
        if growth["status"] != "SKILL_PROPOSED":
            raise ValueError(f"skill validation cannot start from {growth['status']}")
        skill = self.db.query_one(
            "SELECT * FROM skills WHERE skill_id=?", (growth["skill_id"],)
        )
        if skill is None:
            raise KeyError(str(growth["skill_id"]))
        definition = json.loads(skill["definition_json"])
        candidate = self.db.query_one(
            "SELECT source_ids_json FROM evolution_candidates WHERE candidate_id=?",
            (growth["failure_candidate_id"],),
        )
        if candidate is None:
            raise KeyError(str(growth["failure_candidate_id"]))
        source_ids = json.loads(candidate["source_ids_json"])
        source_actions = self._source_failure_actions([str(item) for item in source_ids])
        recovery = self.db.query_one(
            "SELECT * FROM recovery_experiments WHERE experiment_id=?",
            (growth["recovery_experiment_id"],),
        )
        if recovery is None or not recovery["result_json"]:
            raise ValueError("recovery evidence is unavailable")
        recovery_result = json.loads(recovery["result_json"])
        fixtures_by_action = {
            item["source_action_id"]: item["candidate_spec"].get("fixtures", [])
            for item in recovery_result["case_results"]
        }
        experiment_id = new_id("skill_exp")
        artifact_dir = (
            self.config.sandbox_path / "growth" / "skill" / experiment_id
        ).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "experiment_id": experiment_id,
            "growth_cycle_id": growth_cycle_id,
            "skill_id": skill["skill_id"],
            "skill_version": int(skill["version"]),
            "definition_sha256": digest_json(definition),
            "source_action_ids": [str(row["action_id"]) for row in source_actions],
            "acceptance_thresholds": {
                "minimum_cases": 3,
                "required_candidate_success_rate": 1.0,
                "maximum_regressions": 0,
            },
            "created_at": utc_now(),
        }
        manifest_sha256 = digest_json(manifest)
        write_json_atomic(artifact_dir / "manifest.json", manifest)
        baseline = json.loads(growth["baseline_json"])
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
                    skill["skill_id"],
                    int(skill["version"]),
                    "RUNNING",
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                    None,
                    str(artifact_dir),
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE growth_cycles SET skill_experiment_id=?,status='SKILL_SANDBOXED',updated_at=? WHERE growth_cycle_id=?",
                (experiment_id, utc_now(), growth_cycle_id),
            )
            self.ledger.append(
                "skill_experiment_started",
                {
                    "experiment_id": experiment_id,
                    "growth_cycle_id": growth_cycle_id,
                    "skill_id": skill["skill_id"],
                    "manifest_sha256": manifest_sha256,
                },
                connection,
            )
        self.skills.transition(
            str(skill["skill_id"]),
            CandidateStatus.SANDBOXED,
            {
                "experiment_id": experiment_id,
                "manifest_sha256": manifest_sha256,
            },
        )
        step = definition["steps"][0]
        cases: list[dict[str, Any]] = []
        for index, source in enumerate(source_actions):
            case_root = artifact_dir / f"case-{index + 1:03d}"
            harness = IsolatedToolHarness(case_root / "candidate", list(ALLOWED_EXPERIMENT_TOOLS))
            action_id = str(source["action_id"])
            candidate_result = harness.execute(
                tool=str(step["tool"]),
                arguments=dict(step.get("arguments", {})),
                purpose=str(step.get("purpose", definition["description"])),
                acceptance=[str(value) for value in step.get("acceptance", [])],
                risk=RiskLevel(str(definition.get("risk", "READ"))),
                fixtures=[
                    dict(item)
                    for item in fixtures_by_action.get(action_id, [])
                    if isinstance(item, dict)
                ],
            )
            case = {
                "source_action_id": action_id,
                "passed": bool(candidate_result["passed"]),
                "candidate": candidate_result,
            }
            write_json_atomic(case_root / "result.json", case)
            cases.append(case)
        passed_cases = sum(bool(item["passed"]) for item in cases)
        total = len(cases)
        candidate_rate = passed_cases / total if total else 0.0
        baseline_rate = float(baseline["baseline_pass_rate"])
        regressions = max(0, int(round(baseline_rate * total)) - passed_cases)
        passed = total >= 3 and candidate_rate == 1.0 and regressions == 0
        result = {
            "experiment_id": experiment_id,
            "growth_cycle_id": growth_cycle_id,
            "skill_id": skill["skill_id"],
            "finished_at": utc_now(),
            "passed": passed,
            "passed_cases": passed_cases,
            "candidate_cases": total,
            "baseline_pass_rate": baseline_rate,
            "candidate_pass_rate": candidate_rate,
            "regressions": regressions,
            "case_results": cases,
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
            connection.execute(
                "UPDATE growth_cycles SET status=?,updated_at=? WHERE growth_cycle_id=?",
                ("SKILL_VALIDATED" if passed else "SKILL_REJECTED", utc_now(), growth_cycle_id),
            )
            evidence_id = self.ledger.append(
                "skill_experiment_completed",
                {
                    "experiment_id": experiment_id,
                    "growth_cycle_id": growth_cycle_id,
                    "skill_id": skill["skill_id"],
                    "status": status,
                    "result_sha256": result_sha256,
                    "regressions": regressions,
                },
                connection,
            )
        if passed:
            self.skills.transition(
                str(skill["skill_id"]),
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
            "manifest_sha256": manifest_sha256,
            "result_sha256": result_sha256,
            "artifact_path": str(artifact_dir),
            "evidence_id": evidence_id,
        }

    def approve_and_promote(
        self,
        growth_cycle_id: str,
        actor: str,
        authorization_reference: str,
        *,
        human_approved: bool,
    ) -> dict[str, Any]:
        if not human_approved:
            raise PermissionError("approval and promotion require explicit human approval")
        if not actor.strip() or not authorization_reference.strip():
            raise ValueError("actor and authorization_reference are required")
        growth = self._growth_row(growth_cycle_id)
        if growth["status"] != "SKILL_VALIDATED":
            raise ValueError(f"promotion cannot start from {growth['status']}")
        skill_id = str(growth["skill_id"])
        approval = {
            "actor": actor.strip(),
            "authorization_reference": authorization_reference.strip(),
            "approved_at": utc_now(),
        }
        self.skills.transition(
            skill_id,
            CandidateStatus.APPROVED,
            approval,
            human_approved=True,
        )
        promotion = {
            **approval,
            "promoted_at": utc_now(),
            "rollback_target": CandidateStatus.ROLLED_BACK.value,
        }
        self.skills.transition(
            skill_id,
            CandidateStatus.PROMOTED,
            promotion,
            human_approved=True,
        )
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE growth_cycles
                SET status='PROMOTED',approval_json=?,promotion_json=?,updated_at=?
                WHERE growth_cycle_id=?
                """,
                (
                    json.dumps(approval, ensure_ascii=False, sort_keys=True),
                    json.dumps(promotion, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    growth_cycle_id,
                ),
            )
            evidence_id = self.ledger.append(
                "growth_skill_promoted",
                {
                    "growth_cycle_id": growth_cycle_id,
                    "skill_id": skill_id,
                    "actor": actor,
                    "authorization_reference": authorization_reference,
                },
                connection,
            )
        return {
            "growth_cycle_id": growth_cycle_id,
            "skill_id": skill_id,
            "status": "PROMOTED",
            "evidence_id": evidence_id,
        }

    def reuse_on_runtime_task(
        self,
        growth_cycle_id: str,
        task_title: str | None = None,
    ) -> dict[str, Any]:
        growth = self._growth_row(growth_cycle_id)
        if growth["status"] != "PROMOTED":
            raise ValueError(f"real-task reuse cannot start from {growth['status']}")
        skill = self.db.query_one(
            "SELECT * FROM skills WHERE skill_id=?", (growth["skill_id"],)
        )
        if skill is None or skill["status"] != CandidateStatus.PROMOTED.value:
            raise ValueError("skill is not promoted")
        definition = json.loads(skill["definition_json"])
        trigger_text = task_title or " ".join(definition.get("trigger_terms", []))
        if not trigger_text.strip():
            trigger_text = definition["name"]
        goal = Goal(
            title=trigger_text,
            description=(
                f"Execute promoted skill {skill['skill_id']} through canonical runtime "
                "for post-promotion measurement."
            ),
            priority=1.0,
            success_criteria=["promoted skill action succeeds"],
            source=f"growth_cycle:{growth_cycle_id}",
            autonomous=False,
        )
        goal_id = self.runtime.add_goal(goal)
        cycle_result = self.runtime.run_cycle()
        runtime_cycle_id = str(cycle_result.get("cycle_id", "")) or None
        plan_row = (
            self.db.query_one(
                "SELECT plan_id FROM plans WHERE cycle_id=? ORDER BY created_at DESC LIMIT 1",
                (runtime_cycle_id,),
            )
            if runtime_cycle_id
            else None
        )
        plan_id = str(plan_row["plan_id"]) if plan_row else None
        action_rows = (
            self.db.query_all(
                "SELECT * FROM actions WHERE plan_id=? AND skill_id=? ORDER BY rowid",
                (plan_id, skill["skill_id"]),
            )
            if plan_id
            else []
        )
        action_outcomes = [
            {
                "action_id": row["action_id"],
                "tool": row["tool"],
                "status": row["status"],
                "error": row["error"],
            }
            for row in action_rows
        ]
        actual_successes = sum(row["status"] == "SUCCEEDED" for row in action_rows)
        actual_rate = actual_successes / len(action_rows) if action_rows else 0.0
        baseline = json.loads(growth["baseline_json"])
        baseline_rate = float(baseline["baseline_pass_rate"])
        regressions = sum(row["status"] != "SUCCEEDED" for row in action_rows)
        improved = actual_rate > baseline_rate
        decision = "RETAIN" if action_rows and actual_rate == 1.0 and regressions == 0 else "ROLLBACK_REQUIRED"
        measurement_id = new_id("measurement")
        reuse = {
            "goal_id": goal_id,
            "runtime_cycle_id": runtime_cycle_id,
            "plan_id": plan_id,
            "skill_id": skill["skill_id"],
            "task_title": trigger_text,
            "actions": action_outcomes,
            "canonical_runtime_status": cycle_result.get("status"),
        }
        measurement = {
            "measurement_id": measurement_id,
            "baseline_success_rate": baseline_rate,
            "actual_success_rate": actual_rate,
            "improved": improved,
            "regressions": regressions,
            "decision": decision,
            "measured_at": utc_now(),
        }
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO growth_measurements(
                    measurement_id,growth_cycle_id,runtime_cycle_id,plan_id,skill_id,
                    baseline_success_rate,actual_success_rate,regressions,outcome_json,
                    decision,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    measurement_id,
                    growth_cycle_id,
                    runtime_cycle_id,
                    plan_id,
                    skill["skill_id"],
                    baseline_rate,
                    actual_rate,
                    regressions,
                    json.dumps(reuse, ensure_ascii=False, sort_keys=True),
                    decision,
                    measurement["measured_at"],
                ),
            )
            connection.execute(
                """
                UPDATE growth_cycles
                SET status=?,reuse_json=?,measurement_json=?,updated_at=?
                WHERE growth_cycle_id=?
                """,
                (
                    "RETAINED" if decision == "RETAIN" else "ROLLBACK_REQUIRED",
                    json.dumps(reuse, ensure_ascii=False, sort_keys=True),
                    json.dumps(measurement, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    growth_cycle_id,
                ),
            )
            evidence_id = self.ledger.append(
                "growth_post_promotion_measured",
                {
                    "growth_cycle_id": growth_cycle_id,
                    "skill_id": skill["skill_id"],
                    **measurement,
                },
                connection,
            )
        return {
            "growth_cycle_id": growth_cycle_id,
            "reuse": reuse,
            "measurement": measurement,
            "evidence_id": evidence_id,
        }

    def rollback(
        self,
        growth_cycle_id: str,
        actor: str,
        authorization_reference: str,
        *,
        human_approved: bool,
    ) -> dict[str, Any]:
        if not human_approved:
            raise PermissionError("rollback requires explicit human approval")
        growth = self._growth_row(growth_cycle_id)
        if growth["status"] not in {"PROMOTED", "ROLLBACK_REQUIRED", "RETAINED"}:
            raise ValueError(f"rollback cannot start from {growth['status']}")
        skill_id = str(growth["skill_id"])
        definition_row = self.db.query_one(
            "SELECT definition_json,status FROM skills WHERE skill_id=?", (skill_id,)
        )
        if definition_row is None:
            raise KeyError(skill_id)
        if definition_row["status"] != CandidateStatus.PROMOTED.value:
            raise ValueError("only a promoted skill can be rolled back")
        definition = json.loads(definition_row["definition_json"])
        evidence = {
            "actor": actor.strip(),
            "authorization_reference": authorization_reference.strip(),
            "rolled_back_at": utc_now(),
            "reason": "tested rollback of promoted skill",
        }
        self.skills.transition(
            skill_id,
            CandidateStatus.ROLLED_BACK,
            evidence,
            human_approved=True,
        )
        active_ids = {item["skill_id"] for item in self.skills.active()}
        trigger_text = " ".join(definition.get("trigger_terms", []))
        matched_ids = {
            item["skill_id"] for item in self.skills.match(trigger_text, limit=100)
        }
        rollback = {
            **evidence,
            "removed_from_active": skill_id not in active_ids,
            "removed_from_matching": skill_id not in matched_ids,
        }
        rollback["passed"] = bool(
            rollback["removed_from_active"] and rollback["removed_from_matching"]
        )
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE growth_cycles SET status='ROLLED_BACK',rollback_json=?,updated_at=? WHERE growth_cycle_id=?",
                (
                    json.dumps(rollback, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    growth_cycle_id,
                ),
            )
            evidence_id = self.ledger.append(
                "growth_skill_rolled_back",
                {
                    "growth_cycle_id": growth_cycle_id,
                    "skill_id": skill_id,
                    **rollback,
                },
                connection,
            )
        return {
            "growth_cycle_id": growth_cycle_id,
            "skill_id": skill_id,
            "rollback": rollback,
            "evidence_id": evidence_id,
        }

    def summary(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM growth_cycles ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(500, int(limit))),),
        )
        return [
            {
                "growth_cycle_id": row["growth_cycle_id"],
                "failure_candidate_id": row["failure_candidate_id"],
                "recovery_experiment_id": row["recovery_experiment_id"],
                "skill_id": row["skill_id"],
                "skill_experiment_id": row["skill_experiment_id"],
                "status": row["status"],
                "measurement": json.loads(row["measurement_json"])
                if row["measurement_json"]
                else None,
                "rollback": json.loads(row["rollback_json"])
                if row["rollback_json"]
                else None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def _growth_row(self, growth_cycle_id: str) -> Any:
        row = self.db.query_one(
            "SELECT * FROM growth_cycles WHERE growth_cycle_id=?",
            (growth_cycle_id,),
        )
        if row is None:
            raise KeyError(growth_cycle_id)
        return row

    def _source_failure_actions(self, source_ids: list[str]) -> list[Any]:
        rows: list[Any] = []
        for source_id in source_ids:
            row = self.db.query_one(
                """
                SELECT * FROM actions
                WHERE action_id=? AND status IN ('FAILED','UNKNOWN_SIDE_EFFECT')
                """,
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

    def _run_recovery_case(
        self,
        action: dict[str, Any],
        strategy: str,
        case_root: Path,
    ) -> dict[str, Any]:
        baseline = IsolatedToolHarness(
            case_root / "baseline", list(ALLOWED_EXPERIMENT_TOOLS)
        ).execute(
            action["tool"],
            action["arguments"],
            action["purpose"],
            action["acceptance"],
            RiskLevel(action["risk"]),
            [],
            False,
        )
        candidate_spec = self._candidate_spec(action, strategy)
        candidate = IsolatedToolHarness(
            case_root / "candidate", list(ALLOWED_EXPERIMENT_TOOLS)
        ).execute(
            candidate_spec["tool"],
            candidate_spec["arguments"],
            candidate_spec["purpose"],
            candidate_spec["acceptance"],
            RiskLevel(candidate_spec["risk"]),
            candidate_spec.get("fixtures", []),
            False,
        )
        result = {
            "source_action_id": action["action_id"],
            "baseline": baseline,
            "candidate": candidate,
            "candidate_spec": candidate_spec,
            "improved": not baseline["passed"] and bool(candidate["passed"]),
        }
        write_json_atomic(case_root / "result.json", result)
        return result

    @staticmethod
    def _candidate_spec(
        action: dict[str, Any], strategy: str
    ) -> dict[str, Any]:
        spec = {
            "tool": action["tool"],
            "arguments": dict(action["arguments"]),
            "purpose": action["purpose"],
            "acceptance": list(action["acceptance"]),
            "risk": action["risk"],
            "fixtures": [],
        }
        if strategy == "contract_recovery":
            acceptance = list(DEFAULT_ACCEPTANCE.get(action["tool"], []))
            if not acceptance:
                raise ValueError(f"no deterministic contract for {action['tool']}")
            spec["acceptance"] = acceptance
        else:
            path = str(spec["arguments"].get("path", ""))
            if action["tool"] == "read_file":
                spec["fixtures"] = [
                    {"path": path, "content": "WLS recovery fixture\n"}
                ]
            elif action["tool"] == "list_directory":
                spec["fixtures"] = [{"path": path, "directory": True}]
            else:
                raise ValueError("fixture_recovery supports read_file/list_directory")
            if not spec["acceptance"]:
                spec["acceptance"] = list(DEFAULT_ACCEPTANCE[action["tool"]])
        return spec
