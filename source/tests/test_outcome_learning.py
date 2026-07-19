from __future__ import annotations

import json
from pathlib import Path

import pytest

from wls import cli
from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Goal, Observation, Plan, RiskLevel, new_id, utc_now


def test_owner_outcome_feedback_requires_evidence_for_goal_progress(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    action_id, goal_id = create_goal_linked_successful_action(runtime)

    with pytest.raises(ValueError, match="requires evidence"):
        runtime.record_owner_outcome_feedback(
            action_id=action_id,
            outcome="helped",
            goal_progress_delta=0.25,
        )

    feedback = runtime.record_owner_outcome_feedback(
        action_id=action_id,
        outcome="helped",
        owner_note="This inspection moved the goal forward.",
        evidence={"owner_review": "read output was useful"},
        goal_progress_delta=0.25,
    )
    goal = runtime.goals.get(goal_id)
    state = runtime.life_state()

    assert feedback["goal_progress_update"]["before"] == 0.0
    assert feedback["goal_progress_update"]["after"] == 0.25
    assert goal is not None
    assert goal.progress == 0.25
    assert state["outcome_learning"]["feedback_count"] == 1
    assert state["outcome_learning"]["recent_feedback"][0]["outcome"] == "helped"


def test_repeated_failed_owner_feedback_suppresses_future_matching_action(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    action_id, _goal_id = create_successful_read_action(runtime)
    for index in range(2):
        runtime.record_owner_outcome_feedback(
            action_id=action_id,
            outcome="failed",
            owner_note=f"Repeatedly not useful {index}",
            evidence={"owner_review": "inspection did not answer the need"},
        )

    add_inspect_observation(runtime, "repeat")
    result = runtime.run_cycle()

    assert result["status"] == "SUCCEEDED"
    assert result["actions"] == 0
    assert result["outcomes"] == []
    assert result["action_candidate"].get("suppressed") is True, json.dumps(
        result["action_candidate"], ensure_ascii=False, sort_keys=True
    )
    assert result["action_candidate"]["status"] == "SUPPRESSED_BY_OUTCOME_LEARNING"
    assert result["action_candidate"]["suppression"]["failed"] == 2


def test_repeated_helpful_feedback_creates_reusable_heuristic_memory(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    action_id, _goal_id = create_successful_read_action(runtime)

    first = runtime.record_owner_outcome_feedback(
        action_id=action_id,
        outcome="helped",
        owner_note="This was a useful inspection.",
        evidence={"owner_review": "helped"},
    )
    second = runtime.record_owner_outcome_feedback(
        action_id=action_id,
        outcome="helped",
        owner_note="The same pattern helped again.",
        evidence={"owner_review": "helped again"},
    )
    memories = runtime.memories.recent("procedural", limit=10)

    assert first["learning_summary"]["new_heuristic_memory_ids"] == []
    assert second["learning_summary"]["reusable_pattern_count"] == 1
    assert second["learning_summary"]["new_heuristic_memory_ids"]
    assert any(
        "prefer-heuristic" in item.get("tags", [])
        for item in memories
    )


def test_owner_feedback_calibrates_self_model_and_life_state(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    action_id, _goal_id = create_successful_read_action(runtime)

    feedback = runtime.record_owner_outcome_feedback(
        action_id=action_id,
        outcome="avoid",
        owner_note="Do not repeat this inspection pattern for this kind of request.",
        evidence={"owner_review": "not useful"},
    )
    state = runtime.life_state()

    calibration = feedback["self_model_calibration"]
    assert calibration["key"].startswith("capability.owner_outcome.")
    assert calibration["should_defer"] is True
    assert calibration["capability_confidence"] == 0.0
    assert state["self_model_calibration"]["available"] is True
    assert state["self_model_calibration"]["deferred_capabilities"][0][
        "should_defer"
    ] is True


def test_self_model_defer_calibration_blocks_future_matching_action(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.add_goal(
        Goal(
            title="Avoid unsafe repetition",
            description="Self-model should defer action classes it has learned to avoid.",
            priority=0.8,
        )
    )
    evidence_id = runtime.ledger.append(
        "unit_self_model_calibration",
        {"reason": "seed owner-calibrated defer rule"},
    )
    runtime.self_model.set(
        "capability.owner_outcome.list_directory.read",
        {
            "tool": "list_directory",
            "risk_class": "read",
            "helped": 0,
            "failed": 1,
            "avoid": 1,
            "neutral": 0,
            "owner_observation_count": 2,
            "capability_confidence": 0.0,
            "should_defer": True,
            "defer_reason": "owner_avoid_feedback",
        },
        0.7,
        [evidence_id],
    )

    add_inspect_observation(runtime, "self-model-defer")
    result = runtime.run_cycle()

    assert result["status"] == "SUCCEEDED"
    assert result["actions"] == 0
    assert result["outcomes"] == []
    assert result["action_candidate"]["status"] == "DEFERRED_BY_SELF_MODEL"
    assert result["action_candidate"]["defer_to_owner"] is True
    assert result["action_candidate"]["self_model_readiness"]["attempt_allowed"] is False


def test_cli_outcome_feedback_records_latest_candidate_feedback(
    tmp_path: Path,
    capsys,
) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()
    config = default_config(home)
    config.provider = {"type": "deterministic", "fallback": "deterministic"}
    config.sensors = []
    from wls.config import save_config

    save_config(config, config_path)
    runtime = LivingSystem(config)
    runtime.add_goal(Goal(title="CLI feedback goal", description="exercise feedback"))
    add_inspect_observation(runtime, "cli")
    runtime.run_cycle()
    runtime.db.close_all()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "outcome-feedback",
                "helped",
                "--note",
                "candidate helped",
                "--evidence",
                json.dumps({"owner_review": "ok"}),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["outcome"] == "helped"
    assert payload["action_signature"]


def create_successful_read_action(runtime: LivingSystem) -> tuple[str, str]:
    goal_id = runtime.add_goal(
        Goal(
            title="Inspect owner workspace",
            description="Read local context before changing anything.",
            priority=0.8,
        )
    )
    add_inspect_observation(runtime, "initial")
    result = runtime.run_cycle()
    row = runtime.db.query_one(
        "SELECT action_id,status FROM actions ORDER BY rowid DESC LIMIT 1"
    )

    assert result["status"] == "SUCCEEDED"
    assert row is not None
    assert row["status"] == "SUCCEEDED"
    return str(row["action_id"]), goal_id


def create_goal_linked_successful_action(runtime: LivingSystem) -> tuple[str, str]:
    goal_id = runtime.add_goal(
        Goal(
            title="Goal-linked inspection",
            description="Progress can move only when owner evidence says it did.",
            priority=0.8,
        )
    )
    watched = runtime.config.sandbox_path / "progress"
    watched.mkdir(parents=True, exist_ok=True)
    cycle_id = new_id("cycle")
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    action = ActionSpec(
        tool="list_directory",
        arguments={"path": str(watched), "limit": 20},
        purpose="Inspect progress evidence for the owner goal",
        expected_result="Directory listing reviewed by owner",
        risk=RiskLevel.READ,
        goal_id=goal_id,
    )
    plan = Plan(
        rationale="Owner-visible goal progress evidence.",
        actions=[action],
    )
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    outcome = runtime._execute_plan(plan)[0]
    assert outcome["success"] is True
    return action.action_id, goal_id


def add_inspect_observation(runtime: LivingSystem, label: str) -> None:
    watched = runtime.config.sandbox_path / "watched"
    watched.mkdir(parents=True, exist_ok=True)
    (watched / f"{label}.txt").write_text(label, encoding="utf-8")
    runtime.events.add_observation(
        Observation(
            source=f"owner-test-{label}",
            kind="external_event",
            subject=f"owner-request-{label}",
            predicate="request",
            value={"action": "inspect_path", "path": str(watched)},
            confidence=1.0,
        )
    )


def make_runtime(tmp_path: Path) -> LivingSystem:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.sleep_after_idle_cycles = 100
    config.max_actions_per_cycle = 4
    config.provider = {"type": "deterministic", "fallback": "deterministic"}
    return LivingSystem(config)
