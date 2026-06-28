from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .goal_debt import GoalDebtLedger
from .schemas import GoalStatus, new_id, utc_now
from .stores import GoalStore


class GoalReviewer:
    """Evidence-bound reconciliation for durable goals and their child dependencies."""

    TERMINAL = {
        GoalStatus.COMPLETED,
        GoalStatus.SUCCEEDED,
        GoalStatus.FAILED,
        GoalStatus.CANCELLED,
        GoalStatus.ABANDONED,
        GoalStatus.ARCHIVED,
    }

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        goals: GoalStore,
        debts: GoalDebtLedger,
    ):
        self.db = db
        self.ledger = ledger
        self.goals = goals
        self.debts = debts
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS goal_reviews (
                review_id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                cycle_id TEXT,
                previous_status TEXT NOT NULL,
                decided_status TEXT NOT NULL,
                previous_progress REAL NOT NULL,
                decided_progress REAL NOT NULL,
                reason TEXT NOT NULL,
                source_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_goal_reviews_goal_time ON goal_reviews(goal_id,created_at)"
        )

    def review_all(self, *, cycle_id: str | None = None, reason: str = "periodic") -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        # Children first, parents second so parent aggregation sees current child state.
        rows = self.db.query_all(
            "SELECT goal_id FROM goals ORDER BY CASE WHEN parent_goal_id IS NULL THEN 1 ELSE 0 END, created_at ASC"
        )
        for row in rows:
            item = self.review(str(row["goal_id"]), cycle_id=cycle_id, reason=reason)
            if item:
                updates.append(item)
        return updates

    def review(
        self,
        goal_id: str,
        *,
        cycle_id: str | None = None,
        reason: str = "periodic",
        source_ids: list[str] | None = None,
    ) -> dict[str, Any] | None:
        goal = self.goals.get(goal_id)
        if goal is None:
            return None
        source_ids = list(dict.fromkeys(source_ids or []))
        previous_status = goal.status
        previous_progress = goal.progress
        decided_status = previous_status
        decided_progress = previous_progress
        children = self.goals.children(goal_id, include_archived=True)

        if children:
            completed = [child for child in children if child.status in {GoalStatus.COMPLETED, GoalStatus.SUCCEEDED, GoalStatus.ARCHIVED}]
            decided_progress = len(completed) / len(children)
            if len(completed) == len(children):
                decided_status = GoalStatus.COMPLETED
            elif previous_status not in self.TERMINAL:
                decided_status = GoalStatus.DECOMPOSED
        elif previous_status not in self.TERMINAL:
            unmet = self.goals.unmet_dependencies(goal)
            if unmet:
                decided_status = GoalStatus.WAITING
            elif (
                previous_status == GoalStatus.BLOCKED
                and goal.blocked_reason == "interrupted_by_unrelated_work"
                and reason != "interruption_recorded"
            ):
                decided_status = GoalStatus.IN_PROGRESS
                self.goals.increment_recovery(goal_id)
                self.debts.resolve_for_goal(
                    goal_id,
                    "goal became actionable after interruption",
                    source_ids=source_ids,
                )
            elif previous_status in {GoalStatus.PROPOSED, GoalStatus.ACTIVE, GoalStatus.WAITING}:
                decided_status = GoalStatus.IN_PROGRESS

        changed = (
            decided_status != previous_status
            or abs(decided_progress - previous_progress) > 1e-12
        )
        if not changed and reason == "cycle_prepare":
            return None

        now = utc_now()
        self.goals.set_state(
            goal_id,
            progress=decided_progress,
            status=decided_status,
            last_reviewed_at=now,
            completed_at=(now if decided_status == GoalStatus.COMPLETED else goal.completed_at),
        )
        review_id = new_id("goalreview")
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO goal_reviews(
                    review_id,goal_id,cycle_id,previous_status,decided_status,
                    previous_progress,decided_progress,reason,source_ids_json,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    review_id,
                    goal_id,
                    cycle_id,
                    previous_status.value,
                    decided_status.value,
                    previous_progress,
                    decided_progress,
                    reason,
                    json.dumps(source_ids, ensure_ascii=False),
                    now,
                ),
            )
            self.ledger.append(
                "goal_reviewed",
                {
                    "review_id": review_id,
                    "goal_id": goal_id,
                    "cycle_id": cycle_id,
                    "previous_status": previous_status.value,
                    "decided_status": decided_status.value,
                    "previous_progress": previous_progress,
                    "decided_progress": decided_progress,
                    "reason": reason,
                    "source_ids": source_ids,
                },
                connection,
            )
        return {
            "review_id": review_id,
            "goal_id": goal_id,
            "previous_status": previous_status.value,
            "decided_status": decided_status.value,
            "previous_progress": previous_progress,
            "decided_progress": decided_progress,
            "reason": reason,
        }

    def apply_outcomes(
        self,
        cycle_id: str,
        selected_goal_ids: list[str],
        outcomes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        outcome_by_action = {
            str(item.get("action_id")): item for item in outcomes if item.get("action_id")
        }
        action_rows = self.db.query_all(
            "SELECT action_id,goal_id,status FROM actions WHERE goal_id IS NOT NULL AND plan_id IN (SELECT plan_id FROM plans WHERE cycle_id=?)",
            (cycle_id,),
        )
        acted_goal_ids: set[str] = set()
        for row in action_rows:
            goal_id = str(row["goal_id"])
            acted_goal_ids.add(goal_id)
            outcome = outcome_by_action.get(str(row["action_id"]))
            if outcome is None:
                continue
            action_id = str(row["action_id"])
            if bool(outcome.get("success")):
                self.goals.set_state(
                    goal_id,
                    progress=1.0,
                    status=GoalStatus.COMPLETED,
                    progress_evidence_append=[action_id],
                    blocked_reason=None,
                    completed_at=utc_now(),
                )
                self.debts.resolve_for_goal(
                    goal_id,
                    "verified action outcome completed goal",
                    source_ids=[action_id],
                )
            else:
                error = str(outcome.get("error") or outcome.get("status") or "action failed")
                self.goals.set_state(
                    goal_id,
                    status=GoalStatus.BLOCKED,
                    blocked_reason=error,
                    remaining_work=["resolve failed governed action before retry"],
                )
                self.debts.add(
                    goal_id,
                    "ACTION_FAILURE",
                    error,
                    severity=0.8,
                    source_ids=[action_id],
                )
            reviewed = self.review(
                goal_id,
                cycle_id=cycle_id,
                reason="action_outcome",
                source_ids=[action_id],
            )
            if reviewed:
                updates.append(reviewed)

        # A goal that was actionable but pre-empted by unrelated work is durable debt,
        # not silently forgotten. It will be resumed by the next prepare/review pass.
        for goal_id in selected_goal_ids:
            if goal_id in acted_goal_ids:
                continue
            goal = self.goals.get(goal_id)
            if goal is None or goal.status in self.TERMINAL:
                continue
            self.goals.increment_interruption(goal_id)
            self.goals.set_state(
                goal_id,
                status=GoalStatus.BLOCKED,
                blocked_reason="interrupted_by_unrelated_work",
                remaining_work=["resume the same bounded task after interruption"],
            )
            self.debts.add(
                goal_id,
                "INTERRUPTION",
                "actionable goal was pre-empted by unrelated work",
                severity=0.45,
                source_ids=[cycle_id],
            )
            reviewed = self.review(
                goal_id,
                cycle_id=cycle_id,
                reason="interruption_recorded",
                source_ids=[cycle_id],
            )
            if reviewed:
                updates.append(reviewed)

        parent_ids = {
            goal.parent_goal_id
            for goal_id in acted_goal_ids | set(selected_goal_ids)
            if (goal := self.goals.get(goal_id)) is not None and goal.parent_goal_id
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
