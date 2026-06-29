from __future__ import annotations

from types import MethodType
from typing import Any

from .schemas import GoalStatus, Plan, utc_now


FRESH = {"EXECUTED_CURRENT_ACTION", "RECOVERED_DURABLE_ACTION"}


def register_wls(runtime: Any) -> None:
    if getattr(runtime, "_task19_goal_outcome_guard_installed", False):
        return
    runtime._task19_goal_outcome_guard_installed = True

    def execute_plan(self: Any, plan: Plan) -> list[dict[str, Any]]:
        """Execute actions without independently mutating goal progress."""
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
    reviewer = runtime.goal_runtime.reviewer

    def apply_outcomes(
        self: Any,
        cycle_id: str,
        selected_goal_ids: list[str],
        outcomes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        outcome_by_action = {
            str(item.get("action_id")): item
            for item in outcomes
            if item.get("action_id")
            and item.get("provenance") in FRESH
        }
        rows = self.db.query_all(
            """
            SELECT action_id,goal_id,status FROM actions
            WHERE goal_id IS NOT NULL AND plan_id IN
                (SELECT plan_id FROM plans WHERE cycle_id=?)
            ORDER BY rowid ASC
            """,
            (cycle_id,),
        )
        grouped: dict[str, list[Any]] = {}
        for row in rows:
            grouped.setdefault(str(row["goal_id"]), []).append(row)
        fresh_goal_ids: set[str] = set()

        for goal_id, action_rows in grouped.items():
            observed = [
                outcome_by_action[str(row["action_id"])]
                for row in action_rows
                if str(row["action_id"]) in outcome_by_action
            ]
            if not observed:
                continue
            fresh_goal_ids.add(goal_id)
            action_ids = [str(row["action_id"]) for row in action_rows]
            failed = [item for item in observed if not bool(item.get("success"))]
            complete = len(observed) == len(action_rows) and not failed
            if failed:
                reason = str(
                    failed[0].get("error")
                    or failed[0].get("reason")
                    or failed[0].get("status")
                    or "governed action failed"
                )
                self.goals.set_state(
                    goal_id,
                    status=GoalStatus.BLOCKED,
                    blocked_reason=reason,
                    remaining_work=["resolve failed governed action before retry"],
                )
                self.debts.add(
                    goal_id,
                    "ACTION_FAILURE",
                    reason,
                    severity=0.8,
                    source_ids=action_ids,
                )
            elif complete:
                self.goals.set_state(
                    goal_id,
                    progress=1.0,
                    status=GoalStatus.COMPLETED,
                    progress_evidence_append=action_ids,
                    blocked_reason=None,
                    completed_at=utc_now(),
                )
                self.debts.resolve_for_goal(
                    goal_id,
                    "all current governed actions completed",
                    source_ids=action_ids,
                )
            reviewed = self.review(
                goal_id,
                cycle_id=cycle_id,
                reason="aggregate_action_outcome",
                source_ids=action_ids,
            )
            if reviewed:
                updates.append(reviewed)

        for goal_id in selected_goal_ids:
            if goal_id in fresh_goal_ids:
                continue
            goal = self.goals.get(goal_id)
            if goal is None or goal.status in self.TERMINAL:
                continue
            self.goals.increment_interruption(goal_id)
            self.goals.set_state(
                goal_id,
                status=GoalStatus.BLOCKED,
                blocked_reason="interrupted_by_unrelated_or_nonfresh_work",
                remaining_work=["resume the same bounded task after interruption"],
            )
            self.debts.add(
                goal_id,
                "INTERRUPTION",
                "selected goal produced no fresh current-cycle outcome",
                severity=0.45,
                source_ids=[cycle_id],
            )

        parent_ids = {
            goal.parent_goal_id
            for goal_id in set(grouped) | set(selected_goal_ids)
            if (goal := self.goals.get(goal_id)) is not None
            and goal.parent_goal_id
        }
        for parent_id in sorted(parent_ids):
            reviewed = self.review(
                str(parent_id),
                cycle_id=cycle_id,
                reason="child_progress_aggregation",
                source_ids=[cycle_id],
            )
            if reviewed:
                updates.append(reviewed)
        return updates

    reviewer.apply_outcomes = MethodType(apply_outcomes, reviewer)
    runtime.ledger.append("task19_goal_outcome_guard_installed", {})
