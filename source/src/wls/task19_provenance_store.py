from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .schemas import ActionStatus, digest_json, utc_now


PROVENANCE_STORE_VERSION = "task19-provenance-store-1"
_COLUMNS = {
    "outcome_provenance": "TEXT",
    "source_action_id": "TEXT",
    "provenance_evidence_id": "TEXT",
}


def _ensure_schema(runtime: Any) -> None:
    existing = {
        str(row["name"])
        for row in runtime.db.query_all("PRAGMA table_info(actions)")
    }
    with runtime.db.transaction() as connection:
        for name, definition in _COLUMNS.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE actions ADD COLUMN {name} {definition}"
                )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_actions_outcome_provenance
            ON actions(outcome_provenance,finished_at)
            """
        )


def _contract_matches(prior: Any, action: Any, side_effect_class: str) -> bool:
    return all(
        (
            str(prior["tool"]) == action.tool,
            str(prior["arguments_json"])
            == json.dumps(action.arguments, ensure_ascii=False, sort_keys=True),
            str(prior["purpose"]) == action.purpose,
            str(prior["expected_result"]) == action.expected_result,
            str(prior["risk"]) == action.risk.value,
            str(prior["acceptance_json"])
            == json.dumps(action.acceptance, ensure_ascii=False),
            str(prior["side_effect_class"]) == side_effect_class,
        )
    )


def _persist_result(runtime: Any, action_id: str, result: dict[str, Any]) -> None:
    provenance = str(result.get("provenance", "NO_OBSERVABLE_OUTCOME"))
    source_action_id = result.get("source_action_id")
    evidence_id = result.get("provenance_evidence_id")
    row = runtime.db.query_one(
        "SELECT result_json FROM actions WHERE action_id=?", (action_id,)
    )
    result_json = None
    if row is not None and row["result_json"]:
        try:
            payload = json.loads(row["result_json"])
        except json.JSONDecodeError:
            payload = {"raw_result_json": str(row["result_json"])}
        payload["outcome_provenance"] = provenance
        payload["source_action_id"] = source_action_id
        payload["provenance_evidence_id"] = evidence_id
        result_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    runtime.db.execute(
        """
        UPDATE actions
        SET outcome_provenance=?,source_action_id=?,
            provenance_evidence_id=?,result_json=COALESCE(?,result_json)
        WHERE action_id=?
        """,
        (
            provenance,
            source_action_id,
            evidence_id,
            result_json,
            action_id,
        ),
    )


def _reject_collision(runtime: Any, action: Any) -> dict[str, Any]:
    reason = "idempotency key collision with a different action contract"
    evidence_id = runtime.ledger.append(
        "action_idempotency_collision",
        {
            "action_id": action.action_id,
            "idempotency_key_digest": digest_json(action.idempotency_key),
            "reason": reason,
        },
    )
    runtime.db.execute(
        """
        UPDATE actions
        SET status=?,finished_at=?,error=?,outcome_provenance=?,
            source_action_id=NULL,provenance_evidence_id=?
        WHERE action_id=?
        """,
        (
            ActionStatus.REJECTED.value,
            utc_now(),
            reason,
            "NO_OBSERVABLE_OUTCOME",
            evidence_id,
            action.action_id,
        ),
    )
    return {
        "action_id": action.action_id,
        "success": False,
        "status": ActionStatus.REJECTED.value,
        "reason": reason,
        "error": reason,
        "provenance": "NO_OBSERVABLE_OUTCOME",
        "source_action_id": None,
        "provenance_evidence_id": evidence_id,
    }


def register_wls(runtime: Any) -> None:
    """Persist outcome provenance without replacing canonical execution logic."""
    if getattr(runtime, "_task19_provenance_store_installed", False):
        return
    runtime._task19_provenance_store_installed = True
    _ensure_schema(runtime)
    original_execute_action = runtime._execute_action

    def execute_action(
        self: Any,
        action: Any,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        prior = self.db.query_one(
            """
            SELECT action_id,tool,arguments_json,purpose,expected_result,
                   risk,acceptance_json,side_effect_class
            FROM actions
            WHERE idempotency_key=? AND status='SUCCEEDED'
              AND action_id<>?
            ORDER BY rowid ASC LIMIT 1
            """,
            (action.idempotency_key, action.action_id),
        )
        if prior is not None:
            try:
                side_effect_class = self.tools.get(action.tool).side_effect_class
            except KeyError:
                side_effect_class = "unknown"
            if not _contract_matches(prior, action, side_effect_class):
                return _reject_collision(self, action)
        result = original_execute_action(action, approval_id=approval_id)
        _persist_result(self, action.action_id, result)
        return result

    runtime._execute_action = MethodType(execute_action, runtime)
    runtime.ledger.append(
        "task19_provenance_store_installed",
        {
            "version": PROVENANCE_STORE_VERSION,
            "columns": sorted(_COLUMNS),
            "idempotency_contract_collision_rejected": True,
        },
    )
