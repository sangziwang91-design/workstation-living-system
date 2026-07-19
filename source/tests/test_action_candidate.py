from __future__ import annotations

from pathlib import Path

from wls.action_candidate import BoundedActionCandidateBuilder
from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Goal, Observation, Plan, RiskLevel, new_id, utc_now


def test_action_candidate_classifies_l5_risk_classes() -> None:
    builder = BoundedActionCandidateBuilder()

    assert (
        builder.classify_action(
            ActionSpec(
                tool="read_file",
                arguments={"path": "notes.md"},
                purpose="Inspect notes",
                expected_result="Preview",
            ),
            side_effect_class="none",
        )
        == "read"
    )
    assert (
        builder.classify_action(
            ActionSpec(
                tool="emit_note",
                arguments={"path": "outbox/draft.json"},
                purpose="Draft an owner-visible note",
                expected_result="Draft note",
            ),
            side_effect_class="reversible",
        )
        == "draft"
    )
    assert (
        builder.classify_action(
            ActionSpec(
                tool="write_file",
                arguments={"path": "sandbox/draft.txt", "content": "draft"},
                purpose="Draft a bounded file",
                expected_result="File draft",
                risk=RiskLevel.REVERSIBLE_WRITE,
            ),
            side_effect_class="reversible",
        )
        == "write"
    )
    assert (
        builder.classify_action(
            ActionSpec(
                tool="delete_file",
                arguments={"path": "sandbox/old.txt"},
                purpose="Cleanup stale file",
                expected_result="File removed",
                risk=RiskLevel.IRREVERSIBLE,
            ),
            side_effect_class="irreversible",
        )
        == "cleanup"
    )
    assert (
        builder.classify_action(
            ActionSpec(
                tool="run_command",
                arguments={"command": ["git", "status"]},
                purpose="Inspect external command result",
                expected_result="Command output",
                risk=RiskLevel.HIGH,
            ),
            side_effect_class="external",
        )
        == "external"
    )
    assert (
        builder.classify_action(
            ActionSpec(
                tool="write_file",
                arguments={"path": "sandbox/restore.txt", "content": "x"},
                purpose="Rollback the last reversible change",
                expected_result="Previous state restored",
                risk=RiskLevel.REVERSIBLE_WRITE,
            ),
            side_effect_class="reversible",
        )
        == "rollback"
    )


def test_cycle_records_read_action_candidate_and_executes_under_policy(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    watched = runtime.config.sandbox_path / "watched"
    watched.mkdir(parents=True)
    (watched / "note.txt").write_text("owner context", encoding="utf-8")
    goal_id = runtime.add_goal(
        Goal(
            title="Inspect owner workspace",
            description="Use a read-only action before suggesting changes.",
            priority=0.8,
        )
    )
    runtime.events.add_observation(
        Observation(
            source="owner-test",
            kind="external_event",
            subject="owner-request",
            predicate="request",
            value={"action": "inspect_path", "path": str(watched)},
            confidence=1.0,
        )
    )

    result = runtime.run_cycle()
    state = runtime.life_state()
    row = runtime.db.query_one(
        "SELECT status,tool,risk FROM actions ORDER BY rowid DESC LIMIT 1"
    )

    assert result["status"] == "SUCCEEDED"
    assert result["action_candidate"]["available"] is True
    assert result["action_candidate"]["candidate_type"] == "planned_action"
    assert result["action_candidate"]["goal_id"] in {None, goal_id}
    assert result["action_candidate"]["risk_class"] == "read"
    assert result["action_candidate"]["executes_now"] is True
    assert result["action_candidate"]["requires_owner_approval"] is False
    assert row["tool"] == "list_directory"
    assert row["risk"] == "READ"
    assert row["status"] == "SUCCEEDED"
    assert state["next_action_candidate"]["source"] == "planner"
    assert state["next_action_candidate"]["risk_class"] == "read"


def test_write_action_candidate_remains_owner_approval_bound(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    goal_id = runtime.add_goal(
        Goal(
            title="Draft a bounded owner note",
            description="A reversible write must wait for owner approval.",
            priority=0.9,
        )
    )
    cycle_id = new_id("cycle")
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    action = ActionSpec(
        tool="write_file",
        arguments={
            "path": str(runtime.config.sandbox_path / "owner-draft.txt"),
            "content": "bounded draft",
        },
        purpose="Draft a bounded owner note",
        expected_result="Owner can review one concrete draft before it changes state",
        risk=RiskLevel.REVERSIBLE_WRITE,
        goal_id=goal_id,
    )
    plan = Plan(
        rationale="Goal pressure calls for a concrete owner-visible draft.",
        actions=[action],
    )

    candidate = runtime._record_action_candidate(
        cycle_id=cycle_id,
        plan=plan,
        goal_pressure=runtime._goal_pressure_summary(goals=runtime.goals.active()),
        daily_perception={"top_daily_changes": []},
        memory_influence=None,
    )
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    outcome = runtime._execute_plan(plan)[0]
    state = runtime.life_state()

    assert candidate["risk_class"] == "write"
    assert candidate["requires_owner_approval"] is True
    assert candidate["executes_now"] is False
    assert outcome["status"] == "WAITING_APPROVAL"
    assert state["next_action_candidate"]["action_id"] == action.action_id
    assert state["next_action_candidate"]["risk_class"] == "write"
    assert state["next_action_candidate"]["requires_owner_approval"] is True


def make_runtime(tmp_path: Path) -> LivingSystem:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.sleep_after_idle_cycles = 100
    config.max_actions_per_cycle = 1
    config.provider = {"type": "deterministic", "fallback": "deterministic"}
    return LivingSystem(config)
