from __future__ import annotations

from types import MethodType
from typing import Any

from .goal_tasks import task_spec_action_spec
from .schemas import Plan


def register_wls(runtime: Any) -> None:
    if getattr(runtime, "_task19_task_spec_guard_installed", False):
        return
    runtime._task19_task_spec_guard_installed = True
    planner = runtime.planner
    original_plan = planner.plan

    def plan(self: Any, context: dict[str, Any]) -> Plan:
        result = original_plan(context)
        maximum = max(0, int(context.get("budget", {}).get("max_actions", 0)))
        if maximum <= 0:
            return result
        for goal in context.get("goals", []):
            if not goal.get("task_spec"):
                continue
            goal_id = str(goal.get("goal_id", "")) or None
            if not any(action.goal_id == goal_id for action in result.actions):
                result.actions = [
                    task_spec_action_spec(goal),
                    *result.actions,
                ][:maximum]
            break
        return result

    planner.plan = MethodType(plan, planner)
    runtime.ledger.append("task19_task_spec_guard_installed", {})
