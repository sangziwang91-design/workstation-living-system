from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .schemas import ActionSpec, ActionStatus, digest_json, utc_now


VERSION = "task19-execution-guard-2"


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


def _blocked(
    runtime: Any,
    action: ActionSpec,
    status: ActionStatus,
    reason: str,
    event_type: str,
) -> dict[str, Any]:
    finished_at = None if status == ActionStatus.WAITING_APPROVAL else utc_now()
    with runtime.db.transaction() as connection:
        evidence_id = runtime.ledger.append(
            event_type,
            {
                "action_id": action.action_id,
                "status": status.value,
                "reason": reason[:2000],
                "provenance": "NO_OBSERVABLE_OUTCOME",
            },
            connection,
        )
        connection.execute(
            """
            UPDATE actions
            SET status=?,finished_at=?,error=?,outcome_provenance=?,
                source_action_id=NULL,provenance_evidence_id=?
            WHERE action_id=?
            """,
            (
                status.value,
                finished_at,
                reason[:4000],
                "NO_OBSERVABLE_OUTCOME",
                evidence_id,
                action.action_id,
            ),
        )
    return {
        "action_id": action.action_id,
        "success": False,
        "status": status.value,
        "reason": reason,
        "error": reason,
        "provenance": "NO_OBSERVABLE_OUTCOME",
        "source_action_id": None,
        "provenance_evidence_id": evidence_id,
    }


def register_wls(runtime: Any) -> None:
    """Make policy and exact action contracts authoritative over result reuse."""

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
            return _blocked(
                self,
                action,
                ActionStatus.REJECTED,
                f"argument validation failed: {type(exc).__name__}: {exc}",
                "action_rejected",
            )

        approval_valid = False
        if approval_id:
            try:
                approval_valid = bool(
                    self.approvals.validate_and_consume(action, approval_id)
                )
            except Exception as exc:
                return _blocked(
                    self,
                    action,
                    ActionStatus.REJECTED,
                    f"approval validation failed: {type(exc).__name__}: {exc}",
                    "action_approval_rejected",
                )
        decision = self.policy.decide(action, approval_valid=approval_valid)
        if not decision.allowed:
            status = (
                ActionStatus.WAITING_APPROVAL
                if decision.requires_approval
                else ActionStatus.REJECTED
            )
            return _blocked(
                self,
                action,
                status,
                decision.reason,
                "action_blocked",
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
            if not _contract_matches(
                prior, action, definition.side_effect_class
            ):
                return _blocked(
                    self,
                    action,
                    ActionStatus.REJECTED,
                    "idempotency collision: key belongs to a different action contract",
                    "action_idempotency_collision",
                )
            try:
                reused = json.loads(prior["result_json"] or "{}")
            except json.JSONDecodeError as exc:
                return _blocked(
                    self,
                    action,
                    ActionStatus.REJECTED,
                    f"stored idempotent result is invalid JSON: {exc}",
                    "action_reuse_rejected",
                )
            with self.db.transaction() as connection:
                evidence_id = self.ledger.append(
                    "action_result_reused",
                    {
                        "action_id": action.action_id,
                        "source_action_id": str(prior["action_id"]),
                        "result_digest": digest_json(reused),
                    },
                    connection,
                )
                connection.execute(
                    """
                    UPDATE actions
                    SET status='SUCCEEDED',finished_at=?,result_json=?,error=NULL,
                        outcome_provenance='REUSED_PRIOR_RESULT',source_action_id=?,
                        provenance_evidence_id=?
                    WHERE action_id=?
                    """,
                    (
                        utc_now(),
                        json.dumps(reused, ensure_ascii=False, sort_keys=True),
                        str(prior["action_id"]),
                        evidence_id,
                        action.action_id,
                    ),
                )
            return {
                "action_id": action.action_id,
                "success": True,
                "status": ActionStatus.SUCCEEDED.value,
                "reused": True,
                "evaluation": reused.get("evaluation", {}),
                "provenance": "REUSED_PRIOR_RESULT",
                "source_action_id": str(prior["action_id"]),
                "provenance_evidence_id": evidence_id,
            }

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
                    "SELECT status FROM actions WHERE action_id=?",
                    (action.action_id,),
                ).fetchone()
                return {
                    "action_id": action.action_id,
                    "success": False,
                    "status": str(row["status"]) if row else "MISSING",
                    "provenance": "NO_OBSERVABLE_OUTCOME",
                }
            self.ledger.append(
                "action_started",
                {"action_id": action.action_id, "tool": action.tool},
                connection,
            )

        tool_result = self.tools.execute(action)
        evaluation = self.evaluator.evaluate(action, tool_result)
        success = bool(evaluation["accepted"])
        status = ActionStatus.SUCCEEDED if success else ActionStatus.FAILED
        provenance = (
            "RECOVERED_DURABLE_ACTION"
            if getattr(self, "_task19_recovery_mode", False)
            else "EXECUTED_CURRENT_ACTION"
        )
        payload = {"result": tool_result.to_dict(), "evaluation": evaluation}
        with self.db.transaction() as connection:
            evidence_id = self.ledger.append(
                "action_completed",
                {
                    "action_id": action.action_id,
                    "status": status.value,
                    "provenance": provenance,
                    "payload": payload,
                },
                connection,
            )
            connection.execute(
                """
                UPDATE actions
                SET status=?,finished_at=?,result_json=?,error=?,
                    outcome_provenance=?,source_action_id=NULL,
                    provenance_evidence_id=?
                WHERE action_id=?
                """,
                (
                    status.value,
                    tool_result.finished_at,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    tool_result.error
                    or (None if success else "acceptance criteria failed"),
                    provenance,
                    evidence_id,
                    action.action_id,
                ),
            )
        self.self_model.record_action_outcome(action, tool_result, evidence_id)
        return {
            "action_id": action.action_id,
            "success": success,
            "status": status.value,
            "evaluation": evaluation,
            "output": tool_result.output,
            "error": tool_result.error,
            "provenance": provenance,
            "source_action_id": None,
            "provenance_evidence_id": evidence_id,
        }

    runtime._execute_action = MethodType(execute_action, runtime)
    runtime.ledger.append(
        "task19_execution_guard_installed",
        {
            "version": VERSION,
            "policy_before_reuse": True,
            "exact_contract_reuse": True,
            "persistent_provenance": True,
            "schema_migration_transactional": True,
        },
    )
