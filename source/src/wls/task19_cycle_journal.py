from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .cycle_journal import CycleJournal
from .cycle_learning import recover_episode
from .schemas import ActionSpec, ActionStatus, Plan, RiskLevel, digest_json


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


def _outcome_references(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist bounded references; canonical Action rows retain full results."""

    references: list[dict[str, Any]] = []
    for item in outcomes:
        error = str(item.get("error") or "")[:500]
        references.append(
            {
                "action_id": str(item.get("action_id", "")),
                "status": str(item.get("status", "")),
                "success": bool(item.get("success")),
                "provenance": str(
                    item.get("provenance") or "NO_OBSERVABLE_OUTCOME"
                ),
                "source_action_id": item.get("source_action_id"),
                "error": error or None,
                "outcome_digest": digest_json(item),
            }
        )
    return references


def _postprocess(
    runtime: Any,
    cycle_id: str,
    plan_id: str,
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    plan = _plan(runtime, plan_id)
    trace = runtime.db.query_one(
        """
        SELECT trace_id,status,outcome_json
        FROM cognitive_traces WHERE cycle_id=?
        """,
        (cycle_id,),
    )
    cognition = None
    if trace is not None and str(trace["status"]) != "RESOLVED":
        cognition = runtime.cognition.resolve_cycle(cycle_id, plan, outcomes)
    elif trace is not None and trace["outcome_json"]:
        cognition = json.loads(trace["outcome_json"])

    memory = None
    memory_row = runtime.db.query_one(
        """
        SELECT decision_trace_id,resolved_at
        FROM memory_decision_attributions WHERE cycle_id=?
        """,
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

    episode_id = recover_episode(runtime, cycle_id, plan_id, outcomes)
    return {
        "cognitive_trace_id": str(trace["trace_id"]) if trace else None,
        "cognition_resolved": trace is None or cognition is not None,
        "memory_trace_id": (
            str(memory_row["decision_trace_id"]) if memory_row else None
        ),
        "memory_resolved": memory_row is None or memory is not None,
        "goal_trace_id": str(goal_row["goal_trace_id"]) if goal_row else None,
        "goals_resolved": goal_row is None or goals is not None,
        "episode_id": episode_id,
    }


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
                {
                    "plan_id": plan.plan_id,
                    "outcomes": _outcome_references(outcomes),
                },
            )
        return outcomes

    runtime._execute_plan = MethodType(execute_plan, runtime)

    original_resume = runtime._resume_durable_actions

    def resume(self: Any) -> list[dict[str, Any]]:
        current_outcomes = original_resume()
        for recovery in self.cycle_journal.pending(limit=100):
            cycle_id = str(recovery["cycle_id"])
            plan_id = str(recovery["plan_id"])
            outcomes, pending = _outcomes(self, plan_id)
            if pending:
                continue
            references = _outcome_references(outcomes)
            try:
                self.cycle_journal.record(
                    cycle_id,
                    "ACTIONS_TERMINAL",
                    {"plan_id": plan_id, "outcomes": references},
                )
                postprocess = _postprocess(self, cycle_id, plan_id, outcomes)
                self.cycle_journal.resolve(cycle_id, references, postprocess)
            except Exception as exc:
                self.ledger.append(
                    "cycle_recovery_postprocess_failed",
                    {
                        "cycle_id": cycle_id,
                        "plan_id": plan_id,
                        "error": f"{type(exc).__name__}: {exc}"[:2000],
                    },
                )
        return current_outcomes

    runtime._resume_durable_actions = MethodType(resume, runtime)

    original_status = runtime.status

    def status(self: Any) -> dict[str, Any]:
        result = original_status()
        result["cycle_journal"] = self.cycle_journal.summary()
        return result

    runtime.status = MethodType(status, runtime)

    original_verify = runtime.verify_integrity

    def verify(self: Any, full: bool = True) -> dict[str, Any]:
        result = original_verify(full=full)
        ok, details = self.cycle_journal.integrity()
        result["cycle_journal"] = details
        result["ok"] = bool(result.get("ok")) and ok
        if not ok:
            self.kill(f"cycle journal integrity failure: {details}")
        return result

    runtime.verify_integrity = MethodType(verify, runtime)
    runtime.ledger.append(
        "task19_cycle_journal_installed",
        {
            "durable_plan_checkpoint": True,
            "terminal_action_checkpoint": True,
            "reference_only_checkpoint_payloads": True,
            "postprocess_recovery": [
                "cognition",
                "memory",
                "goals",
                "episodic_learning",
            ],
        },
    )
