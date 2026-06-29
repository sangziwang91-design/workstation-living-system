from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .schemas import ActionSpec, ActionStatus, digest_json, utc_now


VERSION = "task19-execution-guard-4"


def _ensure_schema(runtime: Any) -> None:
    with runtime.db.transaction() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(actions)").fetchall()
        }
        if "outcome_provenance" not in columns:
            connection.execute(
                "ALTER TABLE actions ADD COLUMN outcome_provenance TEXT"
            )
        if "source_action_id" not in columns:
            connection.execute(
                "ALTER TABLE actions ADD COLUMN source_action_id TEXT"
            )
        if "provenance_evidence_id" not in columns:
            connection.execute(
                "ALTER TABLE actions ADD COLUMN provenance_evidence_id TEXT"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_actions_outcome_provenance
            ON actions(outcome_provenance,finished_at)
            """
        )


def _contract_matches(row: Any, action: ActionSpec, side_effect: str) -> bool:
    return bool(
        str(row["tool"]) == action.tool
        and str(row["arguments_json"])
        == json.dumps(action.arguments, ensure_ascii=False, sort_keys=True)
        and str(row["purpose"]) == action.purpose
        and str(row["expected_result"]) == action.expected_result
        and str(row["risk"]) == action.risk.value
        and str(row["acceptance_json"])
        == json.dumps(action.acceptance, ensure_ascii=False)
        and str(row["side_effect_class"]) == side_effect
    )


def _persist(
    runtime: Any,
    action: ActionSpec,
    *,
    status: ActionStatus,
    provenance: str,
    event_type: str,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    source_action_id: str | None = None,
    finished: bool = True,
) -> dict[str, Any]:
    event = {
        "action_id": action.action_id,
        "status": status.value,
        "provenance": provenance,
        "source_action_id": source_action_id,
        "error": error,
        "result": result,
    }
    with runtime.db.transaction() as connection:
        evidence_id = runtime.ledger.append(event_type, event, connection)
        connection.execute(
            """
            UPDATE actions
            SET status=?,finished_at=?,result_json=?,error=?,
                outcome_provenance=?,source_action_id=?,provenance_evidence_id=?
            WHERE action_id=?
            """,
            (
                status.value,
                utc_now() if finished else None,
                json.dumps(result, ensure_ascii=False, sort_keys=True)
                if result is not None
                else None,
                error[:4000] if error else None,
                provenance,
                source_action_id,
                evidence_id,
                action.action_id,
            ),
        )
    return {
        "action_id": action.action_id,
        "success": status == ActionStatus.SUCCEEDED,
        "status": status.value,
        "reason": error,
        "error": error,
        "evaluation": (result or {}).get("evaluation", {}),
        "output": (result or {}).get("result", {}).get("output", {}),
        "provenance": provenance,
        "source_action_id": source_action_id,
        "provenance_evidence_id": evidence_id,
    }


def _execution_failure(
    runtime: Any,
    action: ActionSpec,
    side_effect_class: str,
    error: str,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ambiguous = side_effect_class != "none"
    return _persist(
        runtime,
        action,
        status=(
            ActionStatus.UNKNOWN_SIDE_EFFECT
            if ambiguous
            else ActionStatus.FAILED
        ),
        provenance=(
            "NO_OBSERVABLE_OUTCOME"
            if ambiguous
            else "EXECUTED_CURRENT_ACTION"
        ),
        event_type=(
            "action_side_effect_unknown"
            if ambiguous
            else "action_tool_failed"
        ),
        error=error,
        result=result,
    )


def register_wls(runtime: Any) -> None:
    """Make current policy and exact contracts authoritative over reuse."""

    if getattr(runtime, "_task19_execution_guard_installed", False):
        return
    runtime._task19_execution_guard_installed = True
    _ensure_schema(runtime)

    def execute_action(
        self: Any,
        action: ActionSpec,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            self.policy.validate_arguments(action)
            definition = self.tools.get(action.tool)
        except Exception as exc:
            return _persist(
                self,
                action,
                status=ActionStatus.REJECTED,
                provenance="NO_OBSERVABLE_OUTCOME",
                event_type="action_rejected",
                error=f"argument validation failed: {type(exc).__name__}: {exc}",
            )

        approval_valid = False
        if approval_id:
            try:
                approval_valid = bool(
                    self.approvals.validate_and_consume(action, approval_id)
                )
            except Exception as exc:
                return _persist(
                    self,
                    action,
                    status=ActionStatus.REJECTED,
                    provenance="NO_OBSERVABLE_OUTCOME",
                    event_type="action_approval_rejected",
                    error=f"approval validation failed: {type(exc).__name__}: {exc}",
                )
        decision = self.policy.decide(action, approval_valid=approval_valid)
        if not decision.allowed:
            status = (
                ActionStatus.WAITING_APPROVAL
                if decision.requires_approval
                else ActionStatus.REJECTED
            )
            return _persist(
                self,
                action,
                status=status,
                provenance="NO_OBSERVABLE_OUTCOME",
                event_type="action_blocked",
                error=decision.reason,
                finished=status != ActionStatus.WAITING_APPROVAL,
            )

        prior = self.db.query_one(
            """
            SELECT * FROM actions
            WHERE idempotency_key=? AND status='SUCCEEDED' AND action_id<>?
            ORDER BY rowid ASC LIMIT 1
            """,
            (action.idempotency_key, action.action_id),
        )
        if prior is not None:
            if not _contract_matches(prior, action, definition.side_effect_class):
                return _persist(
                    self,
                    action,
                    status=ActionStatus.REJECTED,
                    provenance="NO_OBSERVABLE_OUTCOME",
                    event_type="action_idempotency_collision",
                    error="idempotency key belongs to a different action contract",
                )
            try:
                reused = json.loads(prior["result_json"] or "{}")
            except json.JSONDecodeError as exc:
                return _persist(
                    self,
                    action,
                    status=ActionStatus.REJECTED,
                    provenance="NO_OBSERVABLE_OUTCOME",
                    event_type="action_reuse_rejected",
                    error=f"stored idempotent result is invalid JSON: {exc}",
                )
            result = _persist(
                self,
                action,
                status=ActionStatus.SUCCEEDED,
                provenance="REUSED_PRIOR_RESULT",
                event_type="action_result_reused",
                result={**reused, "reuse_digest": digest_json(reused)},
                source_action_id=str(prior["action_id"]),
            )
            result["reused"] = True
            return result

        with self.db.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE actions SET status='RUNNING',started_at=?,error=NULL
                WHERE action_id=? AND status IN
                    ('PLANNED','APPROVED','WAITING_APPROVAL')
                """,
                (utc_now(), action.action_id),
            ).rowcount
            if updated != 1:
                row = connection.execute(
                    "SELECT status,outcome_provenance FROM actions WHERE action_id=?",
                    (action.action_id,),
                ).fetchone()
                return {
                    "action_id": action.action_id,
                    "success": False,
                    "status": str(row["status"]) if row else "MISSING",
                    "provenance": (
                        str(row["outcome_provenance"])
                        if row and row["outcome_provenance"]
                        else "NO_OBSERVABLE_OUTCOME"
                    ),
                }
            self.ledger.append(
                "action_started",
                {"action_id": action.action_id, "tool": action.tool},
                connection,
            )

        try:
            tool_result = self.tools.execute(action)
        except Exception as exc:
            return _execution_failure(
                self,
                action,
                definition.side_effect_class,
                f"{type(exc).__name__}: {exc}",
            )
        tool_payload = {"result": tool_result.to_dict()}
        if not tool_result.success:
            return _execution_failure(
                self,
                action,
                definition.side_effect_class,
                tool_result.error or "tool execution failed",
                tool_payload,
            )
        try:
            evaluation = self.evaluator.evaluate(action, tool_result)
        except Exception as exc:
            return _execution_failure(
                self,
                action,
                definition.side_effect_class,
                f"evaluation failed: {type(exc).__name__}: {exc}",
                tool_payload,
            )

        success = bool(evaluation["accepted"])
        provenance = (
            "RECOVERED_DURABLE_ACTION"
            if getattr(self, "_task19_recovery_mode", False)
            else "EXECUTED_CURRENT_ACTION"
        )
        payload = {"result": tool_result.to_dict(), "evaluation": evaluation}
        result = _persist(
            self,
            action,
            status=ActionStatus.SUCCEEDED if success else ActionStatus.FAILED,
            provenance=provenance,
            event_type="action_completed",
            error=None if success else "acceptance criteria failed",
            result=payload,
        )
        self.self_model.record_action_outcome(
            action, tool_result, result["provenance_evidence_id"]
        )
        return result

    runtime._execute_action = MethodType(execute_action, runtime)
    runtime.ledger.append(
        "task19_execution_guard_installed",
        {
            "version": VERSION,
            "policy_before_reuse": True,
            "exact_contract_reuse": True,
            "persistent_provenance": True,
            "schema_migration_transactional": True,
            "ambiguous_side_effects_require_reconciliation": True,
        },
    )
