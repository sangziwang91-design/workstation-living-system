from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
import hashlib
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import CandidateStatus, MemoryItem, new_id, utc_now
from .stores import MemoryStore
from .skills import SkillLibrary


class LearningSystem:
    """Turns prediction errors and repeated outcomes into bounded candidates."""

    MAX_EPISODE_BYTES = 128 * 1024
    MAX_WORKSPACE_ITEMS = 64
    MAX_OUTCOMES = 32

    @classmethod
    def _compact_value(cls, value: Any, depth: int = 0) -> Any:
        """Create a bounded, JSON-safe evidence projection.

        Episode memory must never recursively embed earlier memory payloads or
        unbounded tool output. Large values retain a preview, length, and digest
        so the original result remains referentially auditable without being
        copied into every subsequent cycle.
        """
        if depth >= 4:
            encoded = repr(value).encode("utf-8", errors="replace")
            return {
                "truncated": True,
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
            }
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            encoded = value.encode("utf-8", errors="replace")
            if len(encoded) <= 1024:
                return value
            return {
                "preview": value[:512],
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
                "truncated": True,
            }
        if isinstance(value, bytes):
            return {
                "preview_hex": value[:128].hex(),
                "sha256": hashlib.sha256(value).hexdigest(),
                "bytes": len(value),
                "truncated": len(value) > 128,
            }
        if isinstance(value, list):
            items = [cls._compact_value(item, depth + 1) for item in value[:20]]
            if len(value) > 20:
                items.append({"truncated_items": len(value) - 20})
            return items
        if isinstance(value, tuple):
            return cls._compact_value(list(value), depth)
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for index, key in enumerate(sorted(value, key=lambda item: str(item))):
                if index >= 30:
                    result["__truncated_keys__"] = len(value) - 30
                    break
                result[str(key)] = cls._compact_value(value[key], depth + 1)
            return result
        return cls._compact_value(repr(value), depth + 1)

    @classmethod
    def _compact_workspace(cls, workspace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for item in workspace[: cls.MAX_WORKSPACE_ITEMS]:
            item_type = str(item.get("item_type", "unknown"))
            projected: dict[str, Any] = {
                "item_type": item_type,
                "reference_id": str(item.get("reference_id", "")),
                "summary": str(item.get("summary", ""))[:1000],
                "salience": float(item.get("salience", 0.0)),
                "reasons": [str(value)[:200] for value in item.get("reasons", [])[:10]],
            }
            payload = item.get("payload", {})
            if item_type == "memory" and isinstance(payload, dict):
                # Reference prior memory; never copy its content recursively.
                projected["payload"] = {
                    "memory_id": payload.get("memory_id"),
                    "memory_type": payload.get("memory_type"),
                    "score": payload.get("score"),
                    "importance": payload.get("importance"),
                    "confidence": payload.get("confidence"),
                    "tags": cls._compact_value(payload.get("tags", [])),
                }
            elif item_type == "event" and isinstance(payload, dict):
                event_payload = payload.get("payload", {})
                observation = (
                    event_payload.get("observation", {})
                    if isinstance(event_payload, dict)
                    else {}
                )
                projected["payload"] = {
                    "event_id": payload.get("event_id"),
                    "event_type": payload.get("event_type"),
                    "source": payload.get("source"),
                    "payload": {
                        "observation": cls._compact_value(observation)
                    }
                    if observation
                    else cls._compact_value(event_payload),
                }
            else:
                projected["payload"] = cls._compact_value(payload)
            compact.append(projected)
        if len(workspace) > cls.MAX_WORKSPACE_ITEMS:
            compact.append(
                {
                    "item_type": "truncation",
                    "reference_id": "workspace",
                    "summary": "Workspace items omitted from episodic projection",
                    "salience": 0.0,
                    "reasons": [],
                    "payload": {
                        "omitted_items": len(workspace) - cls.MAX_WORKSPACE_ITEMS
                    },
                }
            )
        return compact

    @classmethod
    def _compact_outcomes(cls, outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            cls._compact_value(outcome)
            for outcome in outcomes[: cls.MAX_OUTCOMES]
        ]

    @classmethod
    def _fit_episode(cls, content: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) <= cls.MAX_EPISODE_BYTES:
            content["serialized_bytes"] = len(encoded)
            return content
        minimal = {
            "cycle_id": content.get("cycle_id"),
            "event_ids": list(content.get("event_ids", []))[:100],
            "plan_id": content.get("plan_id"),
            "outcomes": [
                {
                    "action_id": item.get("action_id") if isinstance(item, dict) else None,
                    "success": item.get("success") if isinstance(item, dict) else None,
                    "status": item.get("status") if isinstance(item, dict) else None,
                }
                for item in content.get("outcomes", [])[: cls.MAX_OUTCOMES]
            ],
            "prediction_errors": cls._compact_value(
                content.get("prediction_errors", [])
            ),
            "workspace": [
                {
                    "item_type": item.get("item_type"),
                    "reference_id": item.get("reference_id"),
                    "summary": str(item.get("summary", ""))[:300],
                    "salience": item.get("salience"),
                }
                for item in content.get("workspace", [])[: cls.MAX_WORKSPACE_ITEMS]
            ],
            "truncated": True,
            "original_bytes": len(encoded),
            "original_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        minimal["serialized_bytes"] = len(
            json.dumps(minimal, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        return minimal

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
        episode_content = self._fit_episode(
            {
                "cycle_id": cycle_id,
                "event_ids": list(event_ids),
                "plan_id": plan_id,
                "outcomes": self._compact_outcomes(outcomes),
                "prediction_errors": self._compact_value(prediction_errors),
                "workspace": self._compact_workspace(workspace),
            }
        )
        memory = MemoryItem(
            memory_type="episodic",
            content=episode_content,
            importance=importance,
            confidence=1.0,
            source_ids=[cycle_id, *event_ids],
            tags=["cycle", "success" if failure_count == 0 else "failure"],
        )
        return self.memories.add(memory)

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
            groups[f"{row['subject']}::{row['predicate']}"] .append(row)
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
