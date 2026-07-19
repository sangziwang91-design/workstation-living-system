from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from wls.config import default_config
from wls.goal_pressure import GoalPressureRanker
from wls.runtime import LivingSystem
from wls.schemas import Goal, GoalStatus, utc_now


def test_goal_pressure_ranks_stale_high_priority_goal_and_proposes_step() -> None:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    stale = (now - timedelta(days=12)).isoformat()
    fresh = (now - timedelta(hours=1)).isoformat()
    stale_goal = Goal(
        title="Restore living loop",
        description="Make WLS choose a next bounded step every day.",
        priority=0.95,
        created_at=stale,
        updated_at=stale,
    )
    fresh_goal = Goal(
        title="Low pressure polish",
        description="Small cosmetic work.",
        priority=0.3,
        created_at=fresh,
        updated_at=fresh,
    )

    pressure = GoalPressureRanker().rank([fresh_goal, stale_goal], now=now)

    assert pressure["top_goal"]["goal_id"] == stale_goal.goal_id
    assert pressure["top_goal"]["neglected"] is True
    assert "neglected_high_priority" in pressure["top_goal"]["pressure_reasons"]
    assert pressure["neglected_goals"][0]["goal_id"] == stale_goal.goal_id
    assert pressure["next_small_step"]["goal_id"] == stale_goal.goal_id
    assert pressure["next_small_step"]["executes_now"] is False
    assert pressure["authority"]["policy_and_approval_still_required"] is True


def test_goal_pressure_uses_recent_perception_links() -> None:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    goal = Goal(
        title="Track daily notes",
        description="Notice owner daily notes changes.",
        priority=0.5,
        created_at=(now - timedelta(days=1)).isoformat(),
        updated_at=(now - timedelta(hours=1)).isoformat(),
    )
    daily_perception = {
        "top_daily_changes": [
            {
                "observation_id": "obs-1",
                "classification": "opportunity",
                "score": 0.8,
                "predicate": "file_state",
                "goal_links": [{"goal_id": goal.goal_id, "score": 0.4}],
            }
        ]
    }

    pressure = GoalPressureRanker().rank(
        [goal], daily_perception=daily_perception, now=now
    )

    assert pressure["top_goal"]["recent_perception_links"][0]["observation_id"] == "obs-1"
    assert "recent_perception_link" in pressure["top_goal"]["pressure_reasons"]
    assert pressure["next_small_step"]["candidate_type"] == "goal_pressure"


def test_life_state_orders_goals_by_pressure_and_surfaces_candidate(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.max_actions_per_cycle = 0
    runtime = LivingSystem(config)
    now = datetime.now(UTC)
    stale = (now - timedelta(days=14)).isoformat()
    fresh = (now - timedelta(minutes=10)).isoformat()
    low_goal_id = runtime.add_goal(
        Goal(
            title="Fresh low-priority cleanup",
            description="Low pressure work.",
            priority=0.2,
            created_at=fresh,
            updated_at=fresh,
        )
    )
    high_goal_id = runtime.add_goal(
        Goal(
            title="Restore living loop pressure",
            description="High priority goal should surface even when stale.",
            priority=0.95,
            created_at=stale,
            updated_at=stale,
        )
    )

    state = runtime.life_state()

    assert state["active_goals"][0]["goal_id"] == high_goal_id
    assert state["active_goals"][1]["goal_id"] == low_goal_id
    assert state["goal_pressure"]["top_goal"]["goal_id"] == high_goal_id
    assert state["goal_pressure"]["neglected_goals"][0]["goal_id"] == high_goal_id
    assert state["next_action_candidate"]["source"] == "goal_pressure"
    assert state["next_action_candidate"]["goal_id"] == high_goal_id
    assert state["next_action_candidate"]["executes_now"] is False


def test_blocked_goal_pressure_does_not_execute_actions(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.max_actions_per_cycle = 0
    runtime = LivingSystem(config)
    runtime.add_goal(
        Goal(
            title="Blocked owner project",
            description="Needs an unblock decision.",
            priority=0.8,
            status=GoalStatus.BLOCKED,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )

    result = runtime.run_cycle()
    state = runtime.life_state()

    assert result["status"] == "SUCCEEDED"
    assert result["actions"] == 0
    assert state["goal_pressure"]["top_goal"]["blocked"] is True
    assert state["next_action_candidate"]["executes_now"] is False
