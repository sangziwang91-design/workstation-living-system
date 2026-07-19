from __future__ import annotations

import json
from pathlib import Path

from wls import cli
from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Goal, MemoryItem, Observation, Plan, RiskLevel, new_id, utc_now


def make_runtime(tmp_path: Path) -> LivingSystem:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.sleep_after_idle_cycles = 100
    return LivingSystem(config)


def test_life_state_reads_existing_life_organs_and_stays_bounded(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    goal_id = runtime.add_goal(
        Goal(
            title="Restore the daily living loop",
            description="Make sense, memory, judgment, action, learning, self-model, and sleep visible.",
            priority=0.9,
            success_criteria=["life-state surfaces one next action"],
        )
    )
    event_id, observation_id = runtime.events.add_observation(
        Observation(
            source="test-sensor",
            kind="state",
            subject="owner_workspace",
            predicate="changed",
            value={"path": "notes/today.md", "summary": "owner edited project notes"},
            confidence=0.82,
        )
    )
    runtime.memories.add(
        MemoryItem(
            memory_type="episodic",
            content={
                "claim": "Owner prefers small reversible steps before broader automation.",
                "goal_id": goal_id,
            },
            importance=0.85,
            confidence=0.8,
            source_ids=[event_id],
            tags=["owner-preference", "action-style"],
        )
    )
    runtime.sleep.run()

    cycle_id = new_id("cycle")
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    action = ActionSpec(
        tool="write_file",
        arguments={
            "path": str(runtime.config.sandbox_path / "daily-loop-note.txt"),
            "content": "bounded draft",
        },
        purpose="Draft a tiny reversible note for the owner to review",
        expected_result="Owner can approve or reject one concrete draft",
        risk=RiskLevel.REVERSIBLE_WRITE,
        goal_id=goal_id,
    )
    plan = Plan(
        rationale="Observation and memory suggest a small owner-visible draft.",
        actions=[action],
        memory_ids=[],
    )
    runtime._persist_plan_and_ack_events(cycle_id, plan, [event_id])
    outcome = runtime._execute_plan(plan)[0]
    assert outcome["status"] == "WAITING_APPROVAL"

    state = runtime.life_state()

    assert state["loop"] == [
        "sense",
        "remember",
        "judge",
        "act",
        "learn",
        "self-model",
        "sleep",
    ]
    assert state["active_goals"][0]["goal_id"] == goal_id
    assert state["latest_meaningful_observations"][0]["observation_id"] == observation_id
    assert state["top_memory_influences"][0]["memory_type"] == "episodic"
    assert state["self_model_confidence"]["overall"] > 0
    assert state["pending_owner_approvals"][0]["action_id"] == action.action_id
    assert state["last_sleep_consolidation"]["evidence_id"]
    assert state["next_action_candidate"]["action_id"] == action.action_id
    assert state["next_action_candidate"]["approval_required"] is True
    assert len(state["active_goals"]) <= state["bounds"]["max_items_per_section"]
    assert len(state["latest_meaningful_observations"]) <= state["bounds"][
        "max_items_per_section"
    ]
    assert "commercial_readiness_audit_receipts" not in state

    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True)
    assert len(encoded) < 20000


def test_cli_life_state_returns_bounded_json(
    tmp_path: Path, capsys
) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert cli.main(["--config", str(config_path), "life-state"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["active_goals"] == []
    assert payload["next_action_candidate"]["available"] is False
    assert "No active goals" in payload["next_action_candidate"]["reason"]
    assert payload["bounds"]["max_items_per_section"] == 5
