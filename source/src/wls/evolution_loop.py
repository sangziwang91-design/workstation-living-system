from __future__ import annotations

from datetime import datetime
from typing import Any
import json
import re

from .bounded_recovery import FailureRecoveryEngine
from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .learning import LearningSystem
from .schemas import (
    CandidateStatus,
    RiskLevel,
    SkillDefinition,
    digest_json,
    new_id,
    utc_now,
)
from .skills import SkillLibrary


TERMINAL_ACTION_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "REJECTED",
    "CANCELLED",
    "UNKNOWN_SIDE_EFFECT",
}


def ensure_evolution_loop_tables(db: Database) -> None:
    """Create durable links and post-promotion measurement tables.

    These are additive tables so an existing WLS database can be opened without
    rewriting prior evidence or lifecycle rows.
    """

    statements = [
        """
        CREATE TABLE IF NOT EXISTS evolution_skill_links (
            candidate_id TEXT PRIMARY KEY,
            recovery_experiment_id TEXT NOT NULL,
            skill_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES evolution_candidates(candidate_id),
            FOREIGN KEY(recovery_experiment_id) REFERENCES recovery_experiments(experiment_id),
            FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS skill_deployments (
            deployment_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            skill_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            baseline_json TEXT NOT NULL,
            promotion_evidence_json TEXT NOT NULL,
            promoted_at TEXT NOT NULL,
            evaluated_at TEXT,
            result_json TEXT,
            rolled_back_at TEXT,
            rollback_json TEXT,
            FOREIGN KEY(candidate_id) REFERENCES evolution_candidates(candidate_id),
            FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_skill_deployments_skill_time
        ON skill_deployments(skill_id, promoted_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_skill_deployments_candidate_time
        ON skill_deployments(candidate_id, promoted_at DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS skill_reuse_observations (
            observation_id TEXT PRIMARY KEY,
            deployment_id TEXT NOT NULL,
            action_id TEXT NOT NULL UNIQUE,
            success INTEGER NOT NULL,
            unknown_side_effect INTEGER NOT NULL,
            latency_ms REAL,
            baseline_success_rate REAL NOT NULL,
            benefit_score REAL NOT NULL,
            result_digest TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            FOREIGN KEY(deployment_id) REFERENCES skill_deployments(deployment_id),
            FOREIGN KEY(action_id) REFERENCES actions(action_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_skill_reuse_deployment_time
        ON skill_reuse_observations(deployment_id, observed_at ASC)
        """,
    ]
    with db.transaction() as connection:
        for statement in statements:
            connection.execute(statement)


class VerifiedEvolutionLoop:
    """Close one evidence-bound failure -> skill -> reuse -> rollback cycle.

    The class deliberately does not approve or promote anything autonomously.
    Machine stages can materialize and validate a skill; owner authorization is
    still required for APPROVED, PROMOTED, and ROLLED_BACK transitions.
    """

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        config: RuntimeConfig,
        skills: SkillLibrary,
        learning: LearningSystem,
        recoveries: FailureRecoveryEngine,
    ) -> None:
        self.db = db
        self.ledger = ledger
        self.config = config
        self.skills = skills
        self.learning = learning
        self.recoveries = recoveries
        ensure_evolution_loop_tables(db)

    def materialize_skill(
        self, candidate_id: str, recovery_experiment_id: str
    ) -> dict[str, Any]:
        """Create a versioned declarative skill from a passed recovery experiment."""

        existing = self.db.query_one(
            "SELECT * FROM evolution_skill_links WHERE candidate_id=?",
            (candidate_id,),
        )
        if existing is not None:
            if str(existing["recovery_experiment_id"]) != recovery_experiment_id:
                raise ValueError("candidate is already linked to another recovery experiment")
            return self._link_view(existing)

        candidate = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if candidate is None:
            raise KeyError(candidate_id)
        if CandidateStatus(str(candidate["status"])) != CandidateStatus.VALIDATED:
            raise ValueError("candidate must be VALIDATED before skill materialization")
        if str(candidate["candidate_type"]) not in {
            "failure_repair",
            "failure_recovery",
        }:
            raise ValueError("only repeated-failure candidates can materialize a recovery skill")

        experiment = self.db.query_one(
            "SELECT * FROM recovery_experiments WHERE experiment_id=? AND candidate_id=?",
            (recovery_experiment_id, candidate_id),
        )
        if experiment is None:
            raise ValueError("recovery experiment does not exist for candidate")
        result = self._require_passed_recovery(experiment)
        strategy = str(experiment["strategy"])
        source_ids = [str(value) for value in json.loads(candidate["source_ids_json"])]
        source_rows = self._source_actions(source_ids)
        minimum_cases = max(1, int(self.config.failure_recovery_min_cases))
        if len(source_rows) < minimum_cases:
            raise ValueError(
                f"materialization requires at least {minimum_cases} retained failure actions"
            )

        tools = {str(row["tool"]) for row in source_rows}
        if len(tools) != 1:
            raise ValueError("one recovery skill cannot combine multiple tools")
        tool = next(iter(tools))
        recovered = [
            self.recoveries._recover(row, strategy)  # noqa: SLF001 - same bounded subsystem
            for row in source_rows
        ]
        arguments = self._generalize_arguments(
            [dict(item["action"].arguments) for item in recovered]
        )
        acceptance = self._stable_acceptance(
            [list(item["action"].acceptance) for item in recovered]
        )
        risk = self._highest_risk([str(row["risk"]) for row in source_rows])
        problem_key = self._problem_key(candidate)
        name = self._skill_name(tool, problem_key)
        version_row = self.db.query_one(
            "SELECT MAX(version) AS version FROM skills WHERE name=?", (name,)
        )
        version = int(version_row["version"] or 0) + 1 if version_row else 1
        trigger_terms = self._trigger_terms(candidate, source_rows, tool)
        validation_cases = []
        for index, (row, recovered_case) in enumerate(zip(source_rows, recovered, strict=True)):
            validation_cases.append(
                {
                    "name": f"retained-failure-{index + 1:03d}",
                    "source_action_id": str(row["action_id"]),
                    "step_arguments": [dict(recovered_case["action"].arguments)],
                    "fixtures": [dict(value) for value in recovered_case["fixtures"]],
                    "synthetic": False,
                    "failure_error": str(row["error"] or ""),
                }
            )

        definition = SkillDefinition(
            name=name,
            description=(
                f"Evidence-bound recovery for {problem_key}; generated only from "
                f"retained runtime failures and recovery experiment {recovery_experiment_id}."
            ),
            trigger_terms=trigger_terms,
            steps=[
                {
                    "tool": tool,
                    "arguments": arguments,
                    "purpose": f"Apply validated recovery for {problem_key}",
                    "acceptance": acceptance,
                }
            ],
            risk=risk,
            source_episode_ids=source_ids,
            version=version,
        )
        skill_id = self.skills.add(definition)
        stored = definition.to_dict()
        stored.update(
            {
                "origin_candidate_id": candidate_id,
                "origin_recovery_experiment_id": recovery_experiment_id,
                "failure_signature": problem_key,
                "validation_cases": validation_cases,
                "frozen_recovery_baseline": json.loads(experiment["baseline_json"]),
                "recovery_result_sha256": digest_json(result),
                "acceptance_thresholds": {
                    "minimum_cases": minimum_cases,
                    "all_cases_must_pass": True,
                    "maximum_regressions": 0,
                    "candidate_rate_must_exceed_baseline": True,
                },
                "materialized_at": utc_now(),
            }
        )
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE skills SET definition_json=? WHERE skill_id=?",
                (json.dumps(stored, ensure_ascii=False, sort_keys=True), skill_id),
            )
            connection.execute(
                """
                INSERT INTO evolution_skill_links(
                    candidate_id,recovery_experiment_id,skill_id,state,created_at,updated_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    recovery_experiment_id,
                    skill_id,
                    CandidateStatus.PROPOSED.value,
                    now,
                    now,
                ),
            )
            evidence_id = self.ledger.append(
                "evolution_skill_materialized",
                {
                    "candidate_id": candidate_id,
                    "recovery_experiment_id": recovery_experiment_id,
                    "skill_id": skill_id,
                    "skill_version": version,
                    "definition_sha256": digest_json(stored),
                    "source_action_ids": source_ids,
                },
                connection,
            )
        return {
            "candidate_id": candidate_id,
            "recovery_experiment_id": recovery_experiment_id,
            "skill_id": skill_id,
            "skill_version": version,
            "status": CandidateStatus.PROPOSED.value,
            "evidence_id": evidence_id,
        }

    def approve(
        self,
        candidate_id: str,
        skill_id: str,
        evidence: dict[str, Any],
        human_approved: bool = False,
    ) -> dict[str, Any]:
        if not human_approved:
            raise PermissionError("owner authorization is required for approval")
        self._require_nonempty_evidence(evidence)
        self._require_link(candidate_id, skill_id)
        self._require_status("evolution_candidates", "candidate_id", candidate_id, "VALIDATED")
        self._require_status("skills", "skill_id", skill_id, "VALIDATED")
        bound = {**evidence, "candidate_id": candidate_id, "skill_id": skill_id}
        self.skills.transition(skill_id, CandidateStatus.APPROVED, bound, True)
        self.learning.transition_candidate(
            candidate_id, CandidateStatus.APPROVED, bound, True
        )
        self._set_link_state(candidate_id, CandidateStatus.APPROVED.value)
        evidence_id = self.ledger.append(
            "evolution_pair_approved",
            {"candidate_id": candidate_id, "skill_id": skill_id, "evidence": evidence},
        )
        return {
            "candidate_id": candidate_id,
            "skill_id": skill_id,
            "status": CandidateStatus.APPROVED.value,
            "evidence_id": evidence_id,
        }

    def promote(
        self,
        candidate_id: str,
        skill_id: str,
        evidence: dict[str, Any],
        human_approved: bool = False,
    ) -> dict[str, Any]:
        if not human_approved:
            raise PermissionError("owner authorization is required for promotion")
        self._require_nonempty_evidence(evidence)
        link = self._require_link(candidate_id, skill_id)
        self._require_status("evolution_candidates", "candidate_id", candidate_id, "APPROVED")
        skill = self._require_status("skills", "skill_id", skill_id, "APPROVED")
        recovery = self.db.query_one(
            "SELECT * FROM recovery_experiments WHERE experiment_id=?",
            (str(link["recovery_experiment_id"]),),
        )
        if recovery is None:
            raise ValueError("linked recovery experiment is missing")
        recovery_result = self._require_passed_recovery(recovery)
        skill_experiment = self.db.query_one(
            """
            SELECT * FROM skill_experiments
            WHERE skill_id=? AND status='PASSED'
            ORDER BY finished_at DESC LIMIT 1
            """,
            (skill_id,),
        )
        if skill_experiment is None or not skill_experiment["result_json"]:
            raise ValueError("promotion requires a passed skill experiment")
        skill_result = json.loads(skill_experiment["result_json"])
        if not skill_result.get("passed") or int(skill_result.get("regressions", 1)) != 0:
            raise ValueError("latest skill experiment contains regressions")

        source_action_ids = self._candidate_source_ids(candidate_id)
        baseline = {
            "source_action_ids": source_action_ids,
            "source_failure_count": len(source_action_ids),
            "recovery_experiment_id": str(recovery["experiment_id"]),
            "recovery_result_sha256": digest_json(recovery_result),
            "skill_experiment_id": str(skill_experiment["experiment_id"]),
            "skill_result_sha256": digest_json(skill_result),
            "historical_success_rate": float(
                recovery_result.get("baseline_pass_rate", 0.0)
            ),
            "sandbox_success_rate": float(
                skill_result.get("candidate_pass_rate", 0.0)
            ),
            "thresholds": {
                "minimum_real_reuses": 1,
                "minimum_benefit_delta": 0.01,
                "maximum_regressions": 0,
                "unknown_side_effects_allowed": 0,
            },
            "frozen_at": utc_now(),
        }
        bound = {
            **evidence,
            "candidate_id": candidate_id,
            "skill_id": skill_id,
            "baseline_sha256": digest_json(baseline),
        }
        self.skills.transition(skill_id, CandidateStatus.PROMOTED, bound, True)
        self.learning.transition_candidate(
            candidate_id, CandidateStatus.PROMOTED, bound, True
        )
        deployment_id = new_id("deployment")
        now = utc_now()
        with self.db.transaction() as connection:
            previous = connection.execute(
                """
                SELECT d.deployment_id FROM skill_deployments d
                JOIN skills s ON s.skill_id=d.skill_id
                WHERE s.name=? AND d.status IN ('ACTIVE','BENEFIT_VERIFIED','DEGRADED')
                ORDER BY d.promoted_at DESC
                """,
                (str(skill["name"]),),
            ).fetchall()
            for row in previous:
                connection.execute(
                    "UPDATE skill_deployments SET status='SUPERSEDED' WHERE deployment_id=?",
                    (row["deployment_id"],),
                )
            connection.execute(
                """
                INSERT INTO skill_deployments(
                    deployment_id,candidate_id,skill_id,skill_version,status,
                    baseline_json,promotion_evidence_json,promoted_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    deployment_id,
                    candidate_id,
                    skill_id,
                    int(skill["version"]),
                    "ACTIVE",
                    json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                    json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            connection.execute(
                "UPDATE evolution_skill_links SET state=?,updated_at=? WHERE candidate_id=?",
                (CandidateStatus.PROMOTED.value, now, candidate_id),
            )
            evidence_id = self.ledger.append(
                "evolution_pair_promoted",
                {
                    "deployment_id": deployment_id,
                    "candidate_id": candidate_id,
                    "skill_id": skill_id,
                    "skill_version": int(skill["version"]),
                    "baseline_sha256": digest_json(baseline),
                },
                connection,
            )
        return {
            "deployment_id": deployment_id,
            "candidate_id": candidate_id,
            "skill_id": skill_id,
            "status": "ACTIVE",
            "baseline": baseline,
            "evidence_id": evidence_id,
        }

    def observe_action(self, action_id: str) -> dict[str, Any] | None:
        """Record a later, non-replayed runtime action that used a promoted skill."""

        existing = self.db.query_one(
            "SELECT * FROM skill_reuse_observations WHERE action_id=?", (action_id,)
        )
        if existing is not None:
            return self._observation_view(existing)
        action = self.db.query_one(
            """
            SELECT a.*,p.created_at AS plan_created_at
            FROM actions a JOIN plans p ON p.plan_id=a.plan_id
            WHERE a.action_id=?
            """,
            (action_id,),
        )
        if action is None or not action["skill_id"]:
            return None
        if str(action["status"]) not in TERMINAL_ACTION_STATUSES:
            return None
        deployment = self.db.query_one(
            """
            SELECT * FROM skill_deployments
            WHERE skill_id=? AND status IN ('ACTIVE','BENEFIT_VERIFIED','DEGRADED')
            ORDER BY promoted_at DESC LIMIT 1
            """,
            (str(action["skill_id"]),),
        )
        if deployment is None:
            return None
        if str(action["plan_created_at"]) <= str(deployment["promoted_at"]):
            return None
        baseline = json.loads(deployment["baseline_json"])
        if action_id in set(baseline.get("source_action_ids", [])):
            return None
        # Idempotent result reuse has no started_at and is not evidence of a new real task.
        if not action["started_at"] or not action["finished_at"]:
            return None
        success = str(action["status"]) == "SUCCEEDED"
        unknown = str(action["status"]) == "UNKNOWN_SIDE_EFFECT"
        historical_rate = float(baseline.get("historical_success_rate", 0.0))
        benefit = (1.0 if success else 0.0) - historical_rate
        result_payload: Any
        if action["result_json"]:
            try:
                result_payload = json.loads(action["result_json"])
            except json.JSONDecodeError:
                result_payload = {"raw": str(action["result_json"])}
        else:
            result_payload = {
                "status": str(action["status"]),
                "error": str(action["error"] or ""),
            }
        observation_id = new_id("reuse")
        observed_at = utc_now()
        latency_ms = self._latency_ms(
            str(action["started_at"]), str(action["finished_at"])
        )
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO skill_reuse_observations(
                    observation_id,deployment_id,action_id,success,unknown_side_effect,
                    latency_ms,baseline_success_rate,benefit_score,result_digest,observed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    observation_id,
                    deployment["deployment_id"],
                    action_id,
                    int(success),
                    int(unknown),
                    latency_ms,
                    historical_rate,
                    benefit,
                    digest_json(result_payload),
                    observed_at,
                ),
            )
            evidence_id = self.ledger.append(
                "promoted_skill_reused",
                {
                    "observation_id": observation_id,
                    "deployment_id": deployment["deployment_id"],
                    "action_id": action_id,
                    "success": success,
                    "unknown_side_effect": unknown,
                    "benefit_score": benefit,
                    "result_digest": digest_json(result_payload),
                },
                connection,
            )
        evaluation = self.evaluate(str(deployment["deployment_id"]))
        return {
            "observation_id": observation_id,
            "deployment_id": deployment["deployment_id"],
            "action_id": action_id,
            "success": success,
            "benefit_score": benefit,
            "latency_ms": latency_ms,
            "evidence_id": evidence_id,
            "evaluation": evaluation,
        }

    def evaluate(self, deployment_id: str) -> dict[str, Any]:
        deployment = self.db.query_one(
            "SELECT * FROM skill_deployments WHERE deployment_id=?", (deployment_id,)
        )
        if deployment is None:
            raise KeyError(deployment_id)
        baseline = json.loads(deployment["baseline_json"])
        thresholds = dict(baseline.get("thresholds", {}))
        rows = self.db.query_all(
            """
            SELECT * FROM skill_reuse_observations
            WHERE deployment_id=? ORDER BY observed_at ASC
            """,
            (deployment_id,),
        )
        count = len(rows)
        successes = sum(int(row["success"]) for row in rows)
        unknowns = sum(int(row["unknown_side_effect"]) for row in rows)
        regressions = count - successes
        observed_rate = successes / count if count else 0.0
        baseline_rate = float(baseline.get("historical_success_rate", 0.0))
        benefit_delta = observed_rate - baseline_rate
        minimum_reuses = int(thresholds.get("minimum_real_reuses", 1))
        minimum_delta = float(thresholds.get("minimum_benefit_delta", 0.01))
        maximum_regressions = int(thresholds.get("maximum_regressions", 0))
        maximum_unknowns = int(thresholds.get("unknown_side_effects_allowed", 0))
        enough = count >= minimum_reuses
        passed = bool(
            enough
            and benefit_delta >= minimum_delta
            and regressions <= maximum_regressions
            and unknowns <= maximum_unknowns
        )
        if not enough:
            status = "MONITORING"
        elif passed:
            status = "BENEFIT_VERIFIED"
        else:
            status = "DEGRADED"
        result = {
            "deployment_id": deployment_id,
            "sample_size": count,
            "successes": successes,
            "regressions": regressions,
            "unknown_side_effects": unknowns,
            "observed_success_rate": observed_rate,
            "baseline_success_rate": baseline_rate,
            "benefit_delta": benefit_delta,
            "thresholds": thresholds,
            "passed": passed,
            "status": status,
            "claim": (
                "single-deployment measured benefit"
                if passed
                else "no verified post-promotion benefit"
            ),
            "evaluated_at": utc_now(),
        }
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE skill_deployments
                SET status=?,evaluated_at=?,result_json=?
                WHERE deployment_id=? AND status!='ROLLED_BACK'
                """,
                (
                    status,
                    result["evaluated_at"],
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    deployment_id,
                ),
            )
            evidence_id = self.ledger.append(
                "skill_deployment_evaluated",
                {
                    "deployment_id": deployment_id,
                    "status": status,
                    "result_sha256": digest_json(result),
                },
                connection,
            )
        return {**result, "evidence_id": evidence_id}

    def rollback(
        self,
        deployment_id: str,
        reason: dict[str, Any],
        human_approved: bool = False,
    ) -> dict[str, Any]:
        if not human_approved:
            raise PermissionError("owner authorization is required for rollback")
        self._require_nonempty_evidence(reason)
        deployment = self.db.query_one(
            "SELECT * FROM skill_deployments WHERE deployment_id=?", (deployment_id,)
        )
        if deployment is None:
            raise KeyError(deployment_id)
        if str(deployment["status"]) == "ROLLED_BACK":
            return self._deployment_view(deployment)
        candidate_id = str(deployment["candidate_id"])
        skill_id = str(deployment["skill_id"])
        self._require_status("evolution_candidates", "candidate_id", candidate_id, "PROMOTED")
        skill = self._require_status("skills", "skill_id", skill_id, "PROMOTED")
        evaluation = (
            json.loads(deployment["result_json"])
            if deployment["result_json"]
            else None
        )
        evidence = {
            "deployment_id": deployment_id,
            "reason": reason,
            "last_evaluation": evaluation,
        }
        self.skills.transition(skill_id, CandidateStatus.ROLLED_BACK, evidence, True)
        self.learning.transition_candidate(
            candidate_id, CandidateStatus.ROLLED_BACK, evidence, True
        )
        now = utc_now()
        reactivated: str | None = None
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE skill_deployments
                SET status='ROLLED_BACK',rolled_back_at=?,rollback_json=?
                WHERE deployment_id=?
                """,
                (
                    now,
                    json.dumps(reason, ensure_ascii=False, sort_keys=True),
                    deployment_id,
                ),
            )
            connection.execute(
                "UPDATE evolution_skill_links SET state=?,updated_at=? WHERE candidate_id=?",
                (CandidateStatus.ROLLED_BACK.value, now, candidate_id),
            )
            prior = connection.execute(
                """
                SELECT d.deployment_id,d.skill_id FROM skill_deployments d
                JOIN skills s ON s.skill_id=d.skill_id
                WHERE s.name=? AND d.deployment_id<>? AND d.status='SUPERSEDED'
                  AND s.status='PROMOTED'
                ORDER BY d.skill_version DESC,d.promoted_at DESC LIMIT 1
                """,
                (str(skill["name"]), deployment_id),
            ).fetchone()
            if prior is not None:
                reactivated = str(prior["deployment_id"])
                connection.execute(
                    "UPDATE skill_deployments SET status='ACTIVE' WHERE deployment_id=?",
                    (reactivated,),
                )
            evidence_id = self.ledger.append(
                "skill_deployment_rolled_back",
                {
                    "deployment_id": deployment_id,
                    "candidate_id": candidate_id,
                    "skill_id": skill_id,
                    "reason": reason,
                    "reactivated_deployment_id": reactivated,
                },
                connection,
            )
        return {
            "deployment_id": deployment_id,
            "candidate_id": candidate_id,
            "skill_id": skill_id,
            "status": "ROLLED_BACK",
            "reactivated_deployment_id": reactivated,
            "evidence_id": evidence_id,
        }

    def status(
        self,
        candidate_id: str | None = None,
        skill_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        limit = max(1, min(500, int(limit)))
        clauses: list[str] = []
        params: list[Any] = []
        if candidate_id:
            clauses.append("candidate_id=?")
            params.append(candidate_id)
        if skill_id:
            clauses.append("skill_id=?")
            params.append(skill_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        deployments = self.db.query_all(
            f"SELECT * FROM skill_deployments{where} ORDER BY promoted_at DESC LIMIT ?",
            (*params, limit),
        )
        links = self.db.query_all(
            f"SELECT * FROM evolution_skill_links{where} ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        )
        return {
            "links": [self._link_view(row) for row in links],
            "deployments": [self._deployment_view(row) for row in deployments],
        }

    def _require_link(self, candidate_id: str, skill_id: str):
        row = self.db.query_one(
            "SELECT * FROM evolution_skill_links WHERE candidate_id=? AND skill_id=?",
            (candidate_id, skill_id),
        )
        if row is None:
            raise ValueError("candidate and skill are not linked")
        return row

    def _set_link_state(self, candidate_id: str, state: str) -> None:
        self.db.execute(
            "UPDATE evolution_skill_links SET state=?,updated_at=? WHERE candidate_id=?",
            (state, utc_now(), candidate_id),
        )

    def _require_status(
        self, table: str, id_column: str, item_id: str, expected: str
    ):
        if table not in {"skills", "evolution_candidates"}:
            raise ValueError("unsupported status table")
        row = self.db.query_one(
            f"SELECT * FROM {table} WHERE {id_column}=?", (item_id,)
        )
        if row is None:
            raise KeyError(item_id)
        if str(row["status"]) != expected:
            raise ValueError(f"{item_id} must be {expected}, got {row['status']}")
        return row

    def _require_passed_recovery(self, row) -> dict[str, Any]:
        if str(row["status"]) != "PASSED" or not row["result_json"]:
            raise ValueError("recovery experiment must be PASSED")
        result = json.loads(row["result_json"])
        if not result.get("passed"):
            raise ValueError("recovery result is not passing")
        if int(result.get("regressions", 1)) != 0:
            raise ValueError("recovery result contains regressions")
        if float(result.get("candidate_pass_rate", 0.0)) <= float(
            result.get("baseline_pass_rate", 1.0)
        ):
            raise ValueError("recovery did not improve its frozen baseline")
        return result

    def _source_actions(self, source_ids: list[str]) -> list[Any]:
        rows = []
        for action_id in source_ids:
            row = self.db.query_one("SELECT * FROM actions WHERE action_id=?", (action_id,))
            if row is None:
                raise ValueError(f"retained failure action is missing: {action_id}")
            if str(row["status"]) != "FAILED":
                raise ValueError(f"source action is not a retained failure: {action_id}")
            rows.append(row)
        return rows

    def _candidate_source_ids(self, candidate_id: str) -> list[str]:
        row = self.db.query_one(
            "SELECT source_ids_json FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if row is None:
            raise KeyError(candidate_id)
        return [str(value) for value in json.loads(row["source_ids_json"])]

    @staticmethod
    def _problem_key(candidate) -> str:
        proposal = json.loads(candidate["proposal_json"])
        value = proposal.get("problem_key") or candidate["title"]
        return str(value)[:500]

    @staticmethod
    def _highest_risk(values: list[str]) -> RiskLevel:
        order = {
            RiskLevel.READ.value: 0,
            RiskLevel.REVERSIBLE_WRITE.value: 1,
            RiskLevel.HIGH_IMPACT.value: 2,
        }
        selected = max(values, key=lambda value: order.get(value, 99))
        return RiskLevel(selected)

    @staticmethod
    def _stable_acceptance(values: list[list[str]]) -> list[str]:
        combined: list[str] = []
        for group in values:
            for item in group:
                text = str(item).strip()
                if text and text not in combined:
                    combined.append(text)
        if not combined:
            raise ValueError("recovered skill has no acceptance contract")
        return combined[:20]

    @staticmethod
    def _generalize_arguments(values: list[dict[str, Any]]) -> dict[str, Any]:
        if not values:
            raise ValueError("recovered skill has no arguments")
        keys = set(values[0])
        if any(set(item) != keys for item in values[1:]):
            raise ValueError("recovered actions have incompatible argument contracts")
        result: dict[str, Any] = {}
        for key in sorted(keys):
            candidates = [item[key] for item in values]
            first = candidates[0]
            if all(value == first for value in candidates[1:]):
                result[key] = first
                continue
            # A variable path can be validated through per-case overrides, but the
            # current runtime has no trusted argument binder. Refuse to invent one.
            raise ValueError(
                f"variable argument {key!r} requires a future evidence-bound binder"
            )
        return result

    @staticmethod
    def _trigger_terms(candidate, rows: list[Any], tool: str) -> list[str]:
        terms = {tool.replace("_", " "), tool}
        terms.update(
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_.-]{3,}", str(candidate["title"]))
        )
        for row in rows[:5]:
            terms.update(
                token.lower()
                for token in re.findall(r"[A-Za-z0-9_.-]{3,}", str(row["purpose"]))
            )
        return sorted(term for term in terms if term)[:30]

    @staticmethod
    def _skill_name(tool: str, problem_key: str) -> str:
        stem = re.sub(r"[^a-z0-9]+", "_", tool.lower()).strip("_") or "recovery"
        suffix = digest_json({"tool": tool, "problem_key": problem_key})[:10]
        return f"recovery_{stem}_{suffix}"

    @staticmethod
    def _latency_ms(started_at: str, finished_at: str) -> float | None:
        try:
            started = datetime.fromisoformat(started_at)
            finished = datetime.fromisoformat(finished_at)
        except ValueError:
            return None
        return max(0.0, (finished - started).total_seconds() * 1000.0)

    @staticmethod
    def _require_nonempty_evidence(value: dict[str, Any]) -> None:
        if not isinstance(value, dict) or not value:
            raise ValueError("non-empty structured evidence is required")

    @staticmethod
    def _link_view(row) -> dict[str, Any]:
        return {
            "candidate_id": row["candidate_id"],
            "recovery_experiment_id": row["recovery_experiment_id"],
            "skill_id": row["skill_id"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _deployment_view(row) -> dict[str, Any]:
        return {
            "deployment_id": row["deployment_id"],
            "candidate_id": row["candidate_id"],
            "skill_id": row["skill_id"],
            "skill_version": row["skill_version"],
            "status": row["status"],
            "baseline": json.loads(row["baseline_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "promoted_at": row["promoted_at"],
            "evaluated_at": row["evaluated_at"],
            "rolled_back_at": row["rolled_back_at"],
            "rollback": json.loads(row["rollback_json"]) if row["rollback_json"] else None,
        }

    @staticmethod
    def _observation_view(row) -> dict[str, Any]:
        return {
            "observation_id": row["observation_id"],
            "deployment_id": row["deployment_id"],
            "action_id": row["action_id"],
            "success": bool(row["success"]),
            "unknown_side_effect": bool(row["unknown_side_effect"]),
            "latency_ms": row["latency_ms"],
            "baseline_success_rate": row["baseline_success_rate"],
            "benefit_score": row["benefit_score"],
            "result_digest": row["result_digest"],
            "observed_at": row["observed_at"],
        }
