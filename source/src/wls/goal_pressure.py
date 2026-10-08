from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Sequence
from typing import Any

from .schemas import Goal


@dataclass(slots=True)
class GoalPressureRanker:
    """Rank active goals by life-loop pressure without authorizing action."""

    neglected_days: float = 7.0

    def rank(
        self,
        goals: Sequence[Goal | dict[str, Any]],
        *,
        daily_perception: dict[str, Any] | None = None,
        recent_actions: list[dict[str, Any]] | None = None,
        now: datetime | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        daily_perception = daily_perception or {}
        recent_actions = recent_actions or []
        ranked = [
            self._rank_goal(
                goal,
                daily_perception=daily_perception,
                recent_actions=recent_actions,
                now=now,
            )
            for goal in goals
        ]
        ranked.sort(key=lambda item: (item["pressure_score"], item["priority"]), reverse=True)
        neglected = [
            item
            for item in ranked
            if item["neglected"] or "blocked" in item["pressure_reasons"]
        ]
        top = ranked[0] if ranked else None
        return {
            "schema_version": 1,
            "generated_at": now.isoformat(),
            "ranked_goals": ranked[:limit],
            "top_goal": top,
            "neglected_goals": neglected[:limit],
            "next_small_step": self._next_small_step(top),
            "authority": {
                "candidate_only": True,
                "executes_action": False,
                "policy_and_approval_still_required": True,
            },
        }

    def _rank_goal(
        self,
        goal: Goal | dict[str, Any],
        *,
        daily_perception: dict[str, Any],
        recent_actions: list[dict[str, Any]],
        now: datetime,
    ) -> dict[str, Any]:
        data = self._goal_dict(goal)
        priority = self._float(data.get("priority"), 0.5)
        progress = self._float(data.get("progress"), 0.0)
        status = str(data.get("status", "ACTIVE"))
        created_at = self._parse_time(data.get("created_at")) or now
        updated_at = self._parse_time(data.get("updated_at")) or created_at
        age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
        stale_days = max(0.0, (now - updated_at).total_seconds() / 86400.0)
        goal_id = str(data.get("goal_id", ""))
        perception_links = self._linked_perception(goal_id, daily_perception)
        action_links = [
            action
            for action in recent_actions
            if str(action.get("goal_id", "")) == goal_id
        ]
        blocked = status == "BLOCKED"
        neglected = priority >= 0.7 and stale_days >= self.neglected_days and not action_links
        recent_signal = min(1.0, 0.2 * len(perception_links) + 0.12 * len(action_links))
        pressure = min(
            1.0,
            0.38 * priority
            + 0.12 * min(1.0, age_days / 30.0)
            + 0.18 * (1.0 - progress)
            + (0.14 if blocked else 0.0)
            + (0.14 if neglected else 0.0)
            + recent_signal,
        )
        reasons = [
            f"priority={priority:.2f}",
            f"progress={progress:.2f}",
            f"age_days={age_days:.1f}",
            f"stale_days={stale_days:.1f}",
        ]
        if blocked:
            reasons.append("blocked")
        if neglected:
            reasons.append("neglected_high_priority")
        if perception_links:
            reasons.append("recent_perception_link")
        if action_links:
            reasons.append("recent_action_evidence")
        return {
            "goal_id": goal_id,
            "title": str(data.get("title", "")),
            "status": status,
            "priority": priority,
            "progress": progress,
            "age_days": round(age_days, 3),
            "stale_days": round(stale_days, 3),
            "blocked": blocked,
            "neglected": neglected,
            "pressure_score": round(pressure, 4),
            "pressure_reasons": reasons,
            "recent_perception_links": perception_links[:5],
            "recent_action_evidence": action_links[:5],
        }

    @staticmethod
    def _next_small_step(goal_pressure: dict[str, Any] | None) -> dict[str, Any]:
        if goal_pressure is None:
            return {
                "available": False,
                "reason": "no active goals are available",
            }
        if goal_pressure["blocked"]:
            title = "Identify the smallest blocker-removal step"
            rationale = "The top-pressure goal is blocked and needs a bounded unblock decision."
        elif goal_pressure["recent_perception_links"]:
            title = "Review the linked observation and choose one bounded next step"
            rationale = "Recent perception connected this goal to new local context."
        elif goal_pressure["neglected"]:
            title = "Revive the neglected high-priority goal with one read-only check"
            rationale = "The goal is high-priority and stale without recent action evidence."
        else:
            title = "Choose one bounded next step for the top goal"
            rationale = "This goal currently has the highest combined priority, age, and progress pressure."
        return {
            "available": True,
            "candidate_type": "goal_pressure",
            "goal_id": goal_pressure["goal_id"],
            "title": title,
            "risk": "READ",
            "requires_owner_approval": False,
            "executes_now": False,
            "rationale": rationale,
            "pressure_score": goal_pressure["pressure_score"],
        }

    @staticmethod
    def _linked_perception(
        goal_id: str, daily_perception: dict[str, Any]
    ) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = []
        top_changes = daily_perception.get("top_daily_changes", [])
        if not isinstance(top_changes, list):
            return links
        for item in top_changes:
            if not isinstance(item, dict):
                continue
            goal_links = item.get("goal_links", [])
            if not isinstance(goal_links, list):
                continue
            if any(str(link.get("goal_id", "")) == goal_id for link in goal_links if isinstance(link, dict)):
                links.append(
                    {
                        "observation_id": item.get("observation_id"),
                        "classification": item.get("classification"),
                        "score": item.get("score"),
                        "predicate": item.get("predicate"),
                    }
                )
        return links

    @staticmethod
    def _goal_dict(goal: Goal | dict[str, Any]) -> dict[str, Any]:
        if isinstance(goal, dict):
            return goal
        return goal.to_dict()

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
