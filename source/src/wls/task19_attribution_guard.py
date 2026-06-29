from __future__ import annotations

from types import MethodType
from typing import Any

from .schemas import Plan, digest_json


def _payload(plan: Plan) -> list[dict[str, Any]]:
    return [
        {
            "tool": action.tool,
            "arguments": action.arguments,
            "purpose": action.purpose,
            "expected_result": action.expected_result,
            "risk": action.risk.value,
            "goal_id": action.goal_id,
            "skill_id": action.skill_id,
            "acceptance": action.acceptance,
        }
        for action in plan.actions
    ]


def register_wls(runtime: Any) -> None:
    if getattr(runtime, "_task19_attribution_guard_installed", False):
        return
    runtime._task19_attribution_guard_installed = True
    runtime._task19_final_plan = {}

    planner = runtime.planner
    original_plan = planner.plan

    def plan(self: Any, context: dict[str, Any]) -> Plan:
        result = original_plan(context)
        cycle_id = str(context.get("cycle_id", ""))
        runtime._task19_final_plan[cycle_id] = {
            "digest": digest_json(_payload(result)),
            "goal_ids": [
                str(action.goal_id)
                for action in result.actions
                if action.goal_id
            ],
        }
        return result

    planner.plan = MethodType(plan, planner)

    goal_runtime = runtime.goal_runtime
    original_record = goal_runtime.record_decision

    def record_decision(
        self: Any,
        cycle_id: str,
        *,
        selected_goal_ids: list[str],
        counterfactual: dict[str, Any],
    ) -> dict[str, Any]:
        final = runtime._task19_final_plan.pop(
            cycle_id, {"digest": None, "goal_ids": []}
        )
        acted = set(final["goal_ids"])
        selected = [goal_id for goal_id in selected_goal_ids if goal_id in acted]
        result = original_record(
            cycle_id,
            selected_goal_ids=selected,
            counterfactual=counterfactual,
        )
        counter = result.get("counterfactual_without_goal", counterfactual)
        counter_digest = str(counter.get("action_digest", "")) or None
        actual_digest = final.get("digest")
        influenced = bool(
            selected
            and (
                result.get("selected_key") != counter.get("key")
                or (
                    actual_digest
                    and counter_digest
                    and actual_digest != counter_digest
                )
            )
        )
        method = str(counter.get("method", "UNSPECIFIED"))
        with runtime.db.transaction() as connection:
            connection.execute(
                """
                UPDATE goal_attributions
                SET selected_goal_ids_json=?,goal_influenced_decision=?,
                    actual_action_digest=?,counterfactual_action_digest=?,
                    counterfactual_method=?
                WHERE goal_trace_id=?
                """,
                (
                    __import__("json").dumps(selected, ensure_ascii=False),
                    int(influenced),
                    actual_digest,
                    counter_digest,
                    method,
                    result["goal_trace_id"],
                ),
            )
            runtime.ledger.append(
                "goal_attribution_refined",
                {
                    "goal_trace_id": result["goal_trace_id"],
                    "selected_goal_ids": selected,
                    "goal_influenced_decision": influenced,
                    "actual_action_digest": actual_digest,
                    "counterfactual_action_digest": counter_digest,
                    "counterfactual_method": method,
                },
                connection,
            )
        result.update(
            {
                "selected_goal_ids": selected,
                "goal_influenced_decision": influenced,
                "actual_action_digest": actual_digest,
                "counterfactual_action_digest": counter_digest,
                "counterfactual_method": method,
            }
        )
        return result

    goal_runtime.record_decision = MethodType(record_decision, goal_runtime)
    runtime.ledger.append("task19_attribution_guard_installed", {})
