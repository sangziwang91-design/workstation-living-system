from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .goal_tasks import task_spec_action_spec
from .schemas import ActionSpec, ActionStatus, Plan, digest_json, utc_now

VERSION = "task19-execution-guard-2"
FRESH = {"EXECUTED_CURRENT_ACTION", "RECOVERED_DURABLE_ACTION"}


def _ensure_schema(runtime: Any) -> None:
    columns = {
        str(row["name"])
        for row in runtime.db.query_all("PRAGMA table_info(actions)")
    }
    with runtime.db.transaction() as connection:
        for name in (
            "outcome_provenance",
            "source_action_id",
            "provenance_evidence_id",
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE actions ADD COLUMN {name} TEXT")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_actions_provenance "
            "ON actions(outcome_provenance,finished_at)"
        )


def _same_contract(row: Any, action: ActionSpec, side_effect: str) -> bool:
    return (
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


def _reject(runtime: Any, action: ActionSpec, status: ActionStatus, reason: str) -> dict[str, Any]:
    finished = None if status == ActionStatus.WAITING_APPROVAL else utc_now()
    with runtime.db.transaction() as connection:
        evidence_id = runtime.ledger.append(
            "action_guard_blocked",
            {
                "action_id": action.action_id,
                "status": status.value,
                "reason": reason[:2000],
            },
            connection,
        )
        connection.execute(
            """
            UPDATE actions SET status=?,finished_at=?,error=?,
                outcome_provenance='NO_OBSERVABLE_OUTCOME',
                source_action_id=NULL,provenance_evidence_id=?
            WHERE action_id=?
            """,
            (status.value, finished, reason[:4000], evidence_id, action.action_id),
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
    if getattr(runtime, "_task19_execution_guard_installed", False):
        return
    runtime._task19_execution_guard_installed = True
    runtime._task19_recovery_mode = False
    _ensure_schema(runtime)

    planner = runtime.planner
    original_plan = planner.plan

    def plan(self: Any, context: dict[str, Any]) -> Plan:
        result = original_plan(context)
        maximum = max(0, int(context.get("budget", {}).get("max_actions", 0)))
        if maximum:
            for goal in context.get("goals", []):
                if not goal.get("task_spec"):
                    continue
                goal_id = str(goal.get("goal_id", "")) or None
                if not any(item.goal_id == goal_id for item in result.actions):
                    result.actions = [task_spec_action_spec(goal), *result.actions][:maximum]
                break
        return result

    planner.plan = MethodType(plan, planner)
    original_execute = runtime._execute_action

    def execute_action(
        self: Any,
        action: ActionSpec,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            self.policy.validate_arguments(action)
            definition = self.tools.get(action.tool)
        except Exception as exc:
            return _reject(
                self,
                action,
                ActionStatus.REJECTED,
                f"argument validation failed: {type(exc).__name__}: {exc}",
            )

        if approval_id is None:
            decision = self.policy.decide(action, approval_valid=False)
            if not decision.allowed:
                status = (
                    ActionStatus.WAITING_APPROVAL
                    if decision.requires_approval
                    else ActionStatus.REJECTED
                )
                return _reject(self, action, status, decision.reason)

        prior = self.db.query_one(
            """
            SELECT * FROM actions
            WHERE idempotency_key=? AND status='SUCCEEDED' AND action_id<>?
            ORDER BY rowid ASC LIMIT 1
            """,
            (action.idempotency_key, action.action_id),
        )
        if prior is not None and not _same_contract(
            prior, action, definition.side_effect_class
        ):
            return _reject(
                self,
                action,
                ActionStatus.REJECTED,
                "idempotency collision: key belongs to a different action contract",
            )

        outcome = original_execute(action, approval_id=approval_id)
        if outcome.get("reused"):
            provenance = "REUSED_PRIOR_RESULT"
            source_action_id = str(prior["action_id"]) if prior else None
        elif outcome.get("status") in {"SUCCEEDED", "FAILED"}:
            provenance = (
                "RECOVERED_DURABLE_ACTION"
                if self._task19_recovery_mode
                else "EXECUTED_CURRENT_ACTION"
            )
            source_action_id = None
        else:
            provenance = "NO_OBSERVABLE_OUTCOME"
            source_action_id = None
        evidence_id = self.ledger.append(
            "action_outcome_provenance",
            {
                "action_id": action.action_id,
                "provenance": provenance,
                "source_action_id": source_action_id,
                "outcome_digest": digest_json(outcome),
            },
        )
        self.db.execute(
            """
            UPDATE actions SET outcome_provenance=?,source_action_id=?,
                provenance_evidence_id=? WHERE action_id=?
            """,
            (provenance, source_action_id, evidence_id, action.action_id),
        )
        outcome.update(
            {
                "provenance": provenance,
                "source_action_id": source_action_id,
                "provenance_evidence_id": evidence_id,
            }
        )
        return outcome

    runtime._execute_action = MethodType(execute_action, runtime)

    def execute_plan(self: Any, plan: Plan) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        skill_results: dict[str, list[bool]] = {}
        for action in plan.actions:
            outcome = self._execute_action(action)
            outcomes.append(outcome)
            if action.skill_id and outcome.get("provenance") in FRESH:
                skill_results.setdefault(action.skill_id, []).append(
                    bool(outcome.get("success"))
                )
        for skill_id, values in skill_results.items():
            self.skills.record_use(skill_id, all(values))
        self._refresh_plan_status(plan.plan_id)
        return outcomes

    runtime._execute_plan = MethodType(execute_plan, runtime)
    original_resume = runtime._resume_durable_actions

    def resume(self: Any) -> list[dict[str, Any]]:
        self._task19_recovery_mode = True
        try:
            return original_resume()
        finally:
            self._task19_recovery_mode = False

    runtime._resume_durable_actions = MethodType(resume, runtime)
    runtime.ledger.append(
        "task19_execution_guard_installed", {"version": VERSION}
    )
