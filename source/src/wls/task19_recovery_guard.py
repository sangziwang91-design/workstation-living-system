from __future__ import annotations

from types import MethodType
from typing import Any

from .schemas import Plan


FRESH = {"EXECUTED_CURRENT_ACTION", "RECOVERED_DURABLE_ACTION"}


def _current_action_ids(runtime: Any, cycle_id: str) -> set[str]:
    return {
        str(row["action_id"])
        for row in runtime.db.query_all(
            """
            SELECT a.action_id FROM actions a
            JOIN plans p ON p.plan_id=a.plan_id
            WHERE p.cycle_id=?
            """,
            (cycle_id,),
        )
    }


def _current(
    runtime: Any,
    cycle_id: str,
    outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = _current_action_ids(runtime, cycle_id)
    return [
        item
        for item in outcomes
        if str(item.get("action_id", "")) in ids
        and item.get("provenance") in FRESH
    ]


def register_wls(runtime: Any) -> None:
    if getattr(runtime, "_task19_recovery_guard_installed", False):
        return
    runtime._task19_recovery_guard_installed = True

    planner = runtime.planner
    original_plan = planner.plan

    def plan(self: Any, context: dict[str, Any]) -> Plan:
        has_new_input = bool(
            context.get("workspace")
            or context.get("goals")
            or context.get("matching_skills")
            or context.get("unknowns")
        )
        if not has_new_input:
            return Plan(
                rationale="Recovery-only cycle: no new planning evidence.",
                actions=[],
            )
        return original_plan(context)

    planner.plan = MethodType(plan, planner)

    cognition = runtime.cognition
    original_cognition = cognition.resolve_cycle

    def cognition_resolve(
        self: Any,
        cycle_id: str,
        plan: Plan,
        outcomes: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        return original_cognition(
            cycle_id, plan, _current(runtime, cycle_id, outcomes)
        )

    cognition.resolve_cycle = MethodType(cognition_resolve, cognition)

    attribution = runtime.memory_attribution
    original_memory = attribution.resolve

    def memory_resolve(
        self: Any,
        cycle_id: str,
        outcomes: list[dict[str, Any]],
        cognition_result: dict[str, Any] | None,
        *,
        frozen: bool = False,
    ) -> dict[str, Any] | None:
        return original_memory(
            cycle_id,
            _current(runtime, cycle_id, outcomes),
            cognition_result,
            frozen=frozen,
        )

    attribution.resolve = MethodType(memory_resolve, attribution)

    goal_runtime = runtime.goal_runtime
    original_goal = goal_runtime.resolve_cycle

    def goal_resolve(
        self: Any,
        cycle_id: str,
        *,
        selected_goal_ids: list[str],
        outcomes: list[dict[str, Any]],
        attribution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return original_goal(
            cycle_id,
            selected_goal_ids=selected_goal_ids,
            outcomes=_current(runtime, cycle_id, outcomes),
            attribution=attribution,
        )

    goal_runtime.resolve_cycle = MethodType(goal_resolve, goal_runtime)

    learning = runtime.learning
    original_episode = learning.record_episode

    def record_episode(self: Any, **kwargs: Any) -> str:
        cycle_id = str(kwargs.get("cycle_id", ""))
        kwargs["outcomes"] = _current(
            runtime, cycle_id, list(kwargs.get("outcomes", []))
        )
        return original_episode(**kwargs)

    learning.record_episode = MethodType(record_episode, learning)
    runtime.ledger.append("task19_recovery_guard_installed", {})
