from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import CandidateStatus, MemoryItem, digest_json, new_id, utc_now
from .stores import MemoryStore
from .skills import SkillLibrary


class LearningSystem:
    """Turns prediction errors and repeated outcomes into bounded candidates."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        memories: MemoryStore,
        skills: SkillLibrary,
    ):
        self.db = db
        self.ledger = ledger
        self.memories = memories
        self.skills = skills

    def record_episode(
        self,
        cycle_id: str,
        event_ids: list[str],
        plan_id: str | None,
        outcomes: list[dict[str, Any]],
        prediction_errors: list[dict[str, Any]],
        workspace: list[dict[str, Any]],
    ) -> str:
        success_count = sum(1 for item in outcomes if item.get("success"))
        failure_count = len(outcomes) - success_count
        importance = min(
            1.0,
            0.35
            + 0.08 * len(event_ids)
            + 0.12 * failure_count
            + 0.1 * len(prediction_errors),
        )
        memory = MemoryItem(
            memory_type="episodic",
            content={
                "cycle_id": cycle_id,
                "event_ids": event_ids,
                "plan_id": plan_id,
                "outcomes": outcomes,
                "prediction_errors": prediction_errors,
                "workspace": self._compact_episode_workspace(workspace),
            },
            importance=importance,
            confidence=1.0,
            source_ids=[cycle_id, *event_ids],
            tags=["cycle", "success" if failure_count == 0 else "failure"],
        )
        return self.memories.add(memory)

    @staticmethod
    def _compact_episode_workspace(
        workspace: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for item in workspace:
            payload = item.get("payload", {})
            compact_payload: dict[str, Any] = {}
            if item.get("item_type") == "event" and isinstance(payload, dict):
                observation = payload.get("payload", {}).get("observation", {})
                if isinstance(observation, dict):
                    compact_payload = {
                        "payload": {
                            "observation": {
                                "kind": observation.get("kind"),
                                "subject": observation.get("subject"),
                                "predicate": observation.get("predicate"),
                                "value": observation.get("value"),
                                "confidence": observation.get("confidence"),
                                "observation_id": observation.get("observation_id"),
                            }
                        }
                    }
            if not compact_payload:
                compact_payload = {
                    "digest": digest_json(payload) if isinstance(payload, dict) else None,
                    "keys": sorted(payload)[:20] if isinstance(payload, dict) else [],
                }
            compact.append(
                {
                    "item_type": item.get("item_type"),
                    "reference_id": item.get("reference_id"),
                    "summary": str(item.get("summary", ""))[:500],
                    "salience": item.get("salience"),
                    "reasons": list(item.get("reasons", []))[:5]
                    if isinstance(item.get("reasons", []), list)
                    else [],
                    "payload": compact_payload,
                }
            )
        return compact

    def create_failure_candidates(
        self, minimum_repeats: int = 3, lookback_days: int = 30
    ) -> list[str]:
        cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()
        rows = self.db.query_all(
            """
            SELECT action_id,tool,purpose,error,result_json,finished_at
            FROM actions WHERE status IN ('FAILED','UNKNOWN_SIDE_EFFECT') AND finished_at>=?
            ORDER BY finished_at DESC
            """,
            (cutoff,),
        )
        groups: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            error = str(row["error"] or "unknown")
            key = f"{row['tool']}::{error[:160]}"
            groups[key].append(row)
        created: list[str] = []
        for key, group in groups.items():
            if len(group) < minimum_repeats:
                continue
            source_ids = [str(row["action_id"]) for row in group]
            candidate_id = self._upsert_candidate(
                candidate_type="failure_repair",
                title=f"Repair repeated failure: {key[:120]}",
                proposal={
                    "problem_signature": key,
                    "occurrences": len(group),
                    "required_flow": [
                        "reproduce_in_isolation",
                        "form_root_cause_hypothesis",
                        "apply_minimal_change",
                        "run_targeted_tests",
                        "run_affected_regression",
                        "compare_frozen_baseline",
                        "require_human_promotion",
                    ],
                },
                source_ids=source_ids,
            )
            if candidate_id:
                created.append(candidate_id)
        return created

    def create_prediction_error_candidates(
        self, threshold: float = 0.5, minimum_repeats: int = 2
    ) -> list[str]:
        rows = self.db.query_all(
            "SELECT * FROM predictions WHERE status='REFUTED' AND error_score>=? ORDER BY resolved_at DESC",
            (threshold,),
        )
        groups: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            groups[f"{row['subject']}::{row['predicate']}"].append(row)
        created: list[str] = []
        for key, group in groups.items():
            if len(group) < minimum_repeats:
                continue
            candidate_id = self._upsert_candidate(
                candidate_type="world_model_revision",
                title=f"Revise inaccurate world-model rule: {key}",
                proposal={
                    "prediction_key": key,
                    "mean_error": sum(float(row["error_score"]) for row in group)
                    / len(group),
                    "required_flow": [
                        "collect_counterexamples",
                        "identify_stale_assumption",
                        "replay",
                        "human_review",
                    ],
                },
                source_ids=[str(row["prediction_id"]) for row in group],
            )
            if candidate_id:
                created.append(candidate_id)
        return created

    def _upsert_candidate(
        self,
        candidate_type: str,
        title: str,
        proposal: dict[str, Any],
        source_ids: list[str],
    ) -> str | None:
        fingerprint = json.dumps(
            {"type": candidate_type, "proposal": proposal}, sort_keys=True
        )
        existing = self.db.query_one(
            "SELECT candidate_id FROM evolution_candidates WHERE candidate_type=? AND proposal_json=? AND status NOT IN ('REJECTED','ROLLED_BACK')",
            (candidate_type, fingerprint),
        )
        if existing:
            return None
        candidate_id = new_id("candidate")
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO evolution_candidates(
                    candidate_id,candidate_type,title,proposal_json,source_ids_json,
                    baseline_json,experiment_json,result_json,status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    candidate_type,
                    title,
                    fingerprint,
                    json.dumps(source_ids, ensure_ascii=False),
                    None,
                    None,
                    None,
                    CandidateStatus.PROPOSED.value,
                    now,
                    now,
                ),
            )
            self.ledger.append(
                "evolution_candidate_created",
                {
                    "candidate_id": candidate_id,
                    "candidate_type": candidate_type,
                    "title": title,
                    "source_ids": source_ids,
                },
                connection,
            )
        return candidate_id

    def transition_candidate(
        self,
        candidate_id: str,
        target: CandidateStatus,
        evidence: dict[str, Any],
        human_approved: bool = False,
    ) -> None:
        row = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?", (candidate_id,)
        )
        if row is None:
            raise KeyError(candidate_id)
        current = CandidateStatus(row["status"])
        allowed = {
            CandidateStatus.PROPOSED: {
                CandidateStatus.SANDBOXED,
                CandidateStatus.REJECTED,
            },
            CandidateStatus.SANDBOXED: {
                CandidateStatus.VALIDATED,
                CandidateStatus.REJECTED,
            },
            CandidateStatus.VALIDATED: {
                CandidateStatus.APPROVED,
                CandidateStatus.REJECTED,
            },
            CandidateStatus.APPROVED: {
                CandidateStatus.PROMOTED,
                CandidateStatus.REJECTED,
            },
            CandidateStatus.PROMOTED: {CandidateStatus.ROLLED_BACK},
        }
        if target not in allowed.get(current, set()):
            raise ValueError(
                f"invalid candidate transition: {current.value}->{target.value}"
            )
        if (
            target
            in {
                CandidateStatus.APPROVED,
                CandidateStatus.PROMOTED,
                CandidateStatus.ROLLED_BACK,
            }
            and not human_approved
        ):
            raise PermissionError(f"{target.value} requires human approval")
        self._verify_machine_transition(candidate_id, target, evidence)
        with self.db.transaction() as connection:
            if target in {
                CandidateStatus.VALIDATED,
                CandidateStatus.PROMOTED,
                CandidateStatus.ROLLED_BACK,
            }:
                connection.execute(
                    "UPDATE evolution_candidates SET status=?,result_json=?,updated_at=? WHERE candidate_id=?",
                    (
                        target.value,
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                        utc_now(),
                        candidate_id,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE evolution_candidates SET status=?,experiment_json=?,updated_at=? WHERE candidate_id=?",
                    (
                        target.value,
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                        utc_now(),
                        candidate_id,
                    ),
                )
            self.ledger.append(
                "evolution_candidate_transition",
                {
                    "candidate_id": candidate_id,
                    "from": current.value,
                    "to": target.value,
                    "evidence": evidence,
                },
                connection,
            )
    def _verify_machine_transition(
        self, candidate_id: str, target: CandidateStatus, evidence: dict[str, Any]
    ) -> None:
        if target not in {CandidateStatus.SANDBOXED, CandidateStatus.VALIDATED}:
            return
        candidate = self.db.query_one(
            "SELECT candidate_type FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if candidate is None or candidate["candidate_type"] != "failure_repair":
            return
        experiment_id = str(evidence.get("experiment_id", ""))
        if not experiment_id:
            raise ValueError(f"{target.value} requires a persisted recovery experiment")
        row = self.db.query_one(
            "SELECT * FROM recovery_experiments WHERE experiment_id=? AND candidate_id=?",
            (experiment_id, candidate_id),
        )
        if row is None:
            raise ValueError("recovery experiment evidence does not exist")
        if target == CandidateStatus.SANDBOXED:
            manifest = json.loads(row["manifest_json"])
            if evidence.get("manifest_sha256") != digest_json(manifest):
                raise ValueError("recovery manifest digest mismatch")
            if row["status"] != "RUNNING":
                raise ValueError("recovery sandbox requires a running experiment")
            return
        if row["status"] != "PASSED" or not row["result_json"]:
            raise ValueError("recovery validation requires a passed experiment")
        result = json.loads(row["result_json"])
        if evidence.get("result_sha256") != digest_json(result):
            raise ValueError("recovery result digest mismatch")
        if not result.get("passed") or int(result.get("regressions", 1)) != 0:
            raise ValueError("recovery experiment did not pass without regressions")
        if float(result.get("candidate_pass_rate", 0.0)) <= float(
            result.get("baseline_pass_rate", 1.0)
        ):
            raise ValueError("recovery did not improve the frozen baseline")


