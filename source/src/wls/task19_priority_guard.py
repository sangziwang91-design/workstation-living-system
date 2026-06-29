from __future__ import annotations

from types import MethodType
from typing import Any

from .schemas import Plan
from .task19_attribution_guard import register_wls as register_attribution_guard
from .task19_execution_guard import register_wls as register_execution_guard
from .task19_goal_outcome_guard import register_wls as register_goal_outcome_guard
from .task19_recovery_guard import register_wls as register_recovery_guard
from .task19_storage_guard import register_wls as register_storage_guard


OWNER_SOURCES = {"owner", "user", "human", "cli", "api"}


def _has_external_priority(context: dict[str, Any]) -> tuple[bool, list[str]]:
    event_ids: list[str] = []
    for item in context.get("workspace", []):
        if item.get("item_type") != "event":
            continue
        event = item.get("payload", {})
        observation = event.get("payload", {}).get("observation", {})
        source = str(event.get("source", "")).lower()
        kind = str(observation.get("kind", "")).lower()
        subject = str(observation.get("subject", "")).lower()
        if source in OWNER_SOURCES or kind == "external_event" or subject == "owner-request":
            event_ids.append(str(item.get("reference_id", "")))
    return bool(event_ids), event_ids


def register_wls(runtime: Any) -> None:
    register_execution_guard(runtime)
    register_goal_outcome_guard(runtime)
    register_recovery_guard(runtime)
    register_attribution_guard(runtime)
    register_storage_guard(runtime)
    if getattr(runtime, "_task19_priority_guard_installed", False):
        return
    runtime._task19_priority_guard_installed = True
    planner = runtime.planner
    original_plan = planner.plan

    def guarded_plan(self: Any, context: dict[str, Any]) -> Plan:
        external_priority, event_ids = _has_external_priority(context)
        if not external_priority:
            return original_plan(context)
        guarded = dict(context)
        guarded["goals"] = []
        guarded["workspace"] = [
            item for item in context.get("workspace", [])
            if item.get("item_type") != "goal"
        ]
        plan = original_plan(guarded)
        plan.rationale = (
            "Explicit owner request was ordered before background goals; "
            f"event_ids={','.join(event_ids)}. {plan.rationale}"
        )
        return plan

    planner.plan = MethodType(guarded_plan, planner)
    runtime.ledger.append(
        "task19_priority_guard_installed",
        {"owner_sources": sorted(OWNER_SOURCES)},
    )
