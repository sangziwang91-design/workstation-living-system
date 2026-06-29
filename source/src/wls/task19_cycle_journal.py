from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .cycle_journal import CycleJournal
from .schemas import ActionSpec, ActionStatus, Plan, RiskLevel


TERMINAL_ACTIONS = {
    "SUCCEEDED",
    "FAILED",
    "REJECTED",
    "CANCELLED",
}


def _plan(runtime: Any, plan_id: str) -> Plan:
    row = runtime.db.query_one(
        "SELECT plan_json FROM plans WHERE plan_id=?", (plan_id,)
    )
    if row is None:
        raise KeyError(plan_id)
    raw = json.loads(row["plan_json"])
    actions = [
        ActionSpec(
            tool=str(item["tool"]),
            arguments=dict(item.get("arguments", {})),
            purpose=str(item["purpose"]),
            expected_result=str(item.get("expected_result", "")),
            risk=RiskLevel(str(item.get("risk", RiskLevel.READ.value))),
            goal_id=item.get("goal_id"),
            skill_id=item.get("skill_id"),
            action_id=str(item["action_id"]),
            idempotency_key=item.get("idempotency_key"),
            acceptance=[str(value) for value in item.get("acceptance", [])],
            status=ActionStatus(str(item.get("status", "PLANNED"))),
        )
        for item in raw.get("actions", [])
    ]
    return Plan(
        rationale=str(raw.get("rationale", "recovered durable plan")),
        actions=actions,
        memory_ids=[str(value) for value in raw.get("memory_ids", [])],
        world_fact_ids=[str(value) for value in raw.get("world_fact_ids", [])],
        unknowns=[str(value) for value in raw.get("unknowns", [])],
        plan_id=str(raw.get("plan_id", plan_id)),
        created_at=str(raw.get("created_at", "")),
    )


def _outcomes(runtime: Any, plan_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows = runtime.db.query_all(
        "SELECT * FROM actions WHERE plan_id=? ORDER BY rowid", (plan_id,)
    )
    pending = [
        str(row["status"])
        for row in rows
        if str(row["status"]) not in TERMINAL_ACTIONS
    ]
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        payload: dict[str, Any] = {}
        if row["result_json"]:
            try:
                payload = json.loads(row["result_json"])
            except json.JSONDecodeError:
                payload = {}
        outcomes.append(
            {
                "action_id": str(row["action_id"]),
                "success": str(row["status"]) == "SUCCEEDED",
                "status": str(row["status"]),
                "evaluation": payload.get("evaluation", {}),
                "output": payload.get("result", {}).get("output", {}),
                "error": row["error"],
                "provenance": row["outcome_provenance"] or "NO_OBSERVABLE_OUTCOME",
                "source_action_id": row["source_action_id"],
                "provenance_evidence_id": row["provenance_evidence_id"],
            }
        )
    return outcomes, pending


def _postprocess(
    runtime: Any,
    cycle_id: str,
    plan_id: str,
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    plan = _plan(runtime, plan_id)
    trace = runtime.db.query_one(
        "SELECT status,outcome_json FROM cognitive_traces WHERE cycle_id=?",
        (cycle_id,),
    )
    cognition = None
    if trace is not None and str(trace["status"]) != "RESOLVED":
        cognition = runtime.cognition.resolve_cycle(cycle_id, plan, outcomes)
    elif trace is not None and trace["outcome_json"]:
        cognition = json.loads(trace["outcome_json"])

    memory = None
    memory_row = runtime.db.query_one(
        "SELECT resolved_at FROM memory_decision_attributions WHERE cycle_id=?",
        (cycle_id,),
    )
    if memory_row is not None and not memory_row["resolved_at"]:
        memory = runtime.memory_attribution.resolve(
            cycle_id,
            outcomes,
            cognition,
            frozen=str(runtime.config.provider.get("memory_mode", "enabled")).lower()
            == "frozen",
        )

    goals = None
    goal_row = runtime.db.query_one(
        """
        SELECT goal_trace_id,selected_goal_ids_json,resolved_at
        FROM goal_attributions WHERE cycle_id=?
        """,
        (cycle_id,),
    )
    if goal_row is not None and not goal_row["resolved_at"]:
        goals = runtime.goal_runtime.resolve_cycle(
            cycle_id,
            selected_goal_ids=json.loads(goal_row["selected_goal_ids_json"]),
            outcomes=outcomes,
            attribution={"goal_trace_id": str(goal_row["goal_trace_id"])},
        )
    return {"cognition": cognition, "memory": memory, "goals": goals}


def register_wls(runtime: Any) -> None:
    """Attach durable cycle recovery without creating a second runtime."""

    if getattr(runtime, "_task19_cycle_journal_installed", False):
        return
    runtime._task19_cycle_journal_installed = True
    runtime.cycle_journal = CycleJournal(runtime.db, runtime.ledger)

    original_initialize = runtime._initialize_runtime

    def initialize(self: Any) -> None:
        self.cycle_journal.classify_interrupted()
        original_initialize()

    runtime._initialize_runtime = MethodType(initialize, runtime)

    original_persist = runtime._persist_plan_and_ack_events

    def persist(self: Any, cycle_id: str, plan: Plan, event_ids: list[str]) -> None:
        original_persist(cycle_id, plan, event_ids)
        self.cycle_journal.record(
            cycle_id,
            "PLAN_PERSISTED",
            {
                "plan_id": plan.plan_id,
                "action_ids": [action.action_id for action in plan.actions],
                "event_ids": event_ids,
                "goal_ids": [action.goal_id for action in plan.actions if action.goal_id],
                "memory_ids": plan.memory_ids,
            },
        )

    runtime._persist_plan_and_ack_events = MethodType(persist, runtime)

    original_execute_plan = runtime._execute_plan

    def execute_plan(self: Any, plan: Plan) -> list[dict[str, Any]]:
        original_execute_plan(plan)
        outcomes, pending = _outcomes(self, plan.plan_id)
        row = self.db.query_one(
            "SELECT cycle_id FROM plans WHERE plan_id=?", (plan.plan_id,)
        )
        if row is not None and not pending:
            self.cycle_journal.record(
                str(row["cycle_id"]),
                "ACTIONS_TERMINAL",
                {"plan_id": plan.plan_id, "outcomes": outcomes},
            )
        return outcomes

    runtime._execute_plan = MethodType(execute_plan, runtime)
