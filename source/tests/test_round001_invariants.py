from __future__ import annotations

import json

import pytest

from wls.schemas import ActionSpec, Event, MemoryItem, Observation, Plan, RiskLevel


def _filesystem_event(path: str) -> Event:
    observation = Observation(
        source="test",
        kind="filesystem_change",
        subject=path,
        predicate="state",
        value="modified",
        metadata={"dedupe_key": f"fs:{path}"},
    )
    return Event(
        event_type="observation.filesystem_change",
        source="test",
        payload={"observation": observation.to_dict()},
        salience_hint=1.0,
        dedupe_key=f"evt:{path}",
    )


def test_event_ack_requires_current_worker_and_is_atomic(runtime_factory):
    runtime = runtime_factory(max_actions_per_cycle=0)
    event_id, _ = runtime.ingest_event(
        Event(event_type="external.test", source="test", payload={"x": 1})
    )
    reserved = runtime.events.reserve("worker-a", 1)
    assert [event.event_id for event in reserved] == [event_id]

    before = runtime.db.query_one("SELECT COUNT(*) AS n FROM evidence")["n"]
    with pytest.raises(RuntimeError, match="acknowledgement invariant"):
        runtime.events.mark_processed(
            [event_id], "cycle-x", worker_id="worker-b"
        )
    row = runtime.db.query_one(
        "SELECT status,reserved_by FROM events WHERE event_id=?", (event_id,)
    )
    after = runtime.db.query_one("SELECT COUNT(*) AS n FROM evidence")["n"]
    assert row["status"] == "RESERVED"
    assert row["reserved_by"] == "worker-a"
    assert after == before


def test_event_ack_rejects_duplicate_ids_without_partial_transition(runtime_factory):
    runtime = runtime_factory(max_actions_per_cycle=0)
    event_id, _ = runtime.ingest_event(
        Event(event_type="external.test", source="test", payload={"x": 2})
    )
    runtime.events.reserve("worker-a", 1)
    with pytest.raises(ValueError, match="unique"):
        runtime.events.mark_processed(
            [event_id, event_id], "cycle-x", worker_id="worker-a"
        )
    row = runtime.db.query_one(
        "SELECT status FROM events WHERE event_id=?", (event_id,)
    )
    assert row["status"] == "RESERVED"


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("acceptance_json", json.dumps(["output contains secret"])),
        ("idempotency_key", "tampered-key"),
        ("goal_id", "goal_tampered"),
        ("skill_id", "skill_tampered"),
    ],
)
def test_approval_digest_binds_complete_action_semantics(
    runtime_factory, column, replacement
):
    runtime = runtime_factory(read_only=True)
    target = runtime.config.sandbox_path / "approved.txt"
    plan = Plan(
        rationale="complete digest",
        actions=[
            ActionSpec(
                tool="write_file",
                arguments={"path": str(target), "content": "safe"},
                purpose="write a reviewed file",
                expected_result="file written",
                risk=RiskLevel.REVERSIBLE_WRITE,
                goal_id="goal_original",
                skill_id="skill_original",
                acceptance=["output contains path"],
            )
        ],
    )
    runtime._persist_plan_and_ack_events("cycle-digest", plan, [])
    action = plan.actions[0]
    blocked = runtime._execute_action(action)
    assert blocked["status"] == "WAITING_APPROVAL"
    runtime.approvals.issue(action.action_id, True)
    runtime.db.execute(
        f"UPDATE actions SET {column}=? WHERE action_id=?",
        (replacement, action.action_id),
    )
    result = runtime.resume_action(action.action_id)
    assert result["success"] is False
    assert result["status"] == "WAITING_APPROVAL"
    assert not target.exists()


def test_only_validated_broadcast_memory_can_change_plan(runtime_factory, tmp_path):
    missing = tmp_path / "missing.txt"

    control = runtime_factory(home=tmp_path / "control")
    control.ingest_event(_filesystem_event(str(missing)))
    control_result = control.run_cycle()
    control_action = control.db.query_one(
        "SELECT tool,status FROM actions ORDER BY rowid DESC LIMIT 1"
    )
    assert control_result["actions"] == 1
    assert control_action["tool"] == "read_file"
    assert control_action["status"] == "FAILED"

    guarded = runtime_factory(home=tmp_path / "guarded")
    guidance = MemoryItem(
        memory_type="failure",
        content={
            "schema": "wls.action_guidance.v1",
            "match": {"tool": "read_file", "path_suffix": ".txt"},
            "effect": {"skip": True},
            "reason": "Repeated missing-path failure reproduced and reviewed.",
        },
        importance=1.0,
        confidence=0.95,
        source_ids=["failure-1", "failure-2"],
        tags=["validated", "decision-guidance"],
    )
    guarded.memories.add(guidance)
    guarded.ingest_event(_filesystem_event(str(missing)))
    guarded_result = guarded.run_cycle()
    guarded_action = guarded.db.query_one(
        "SELECT tool,status FROM actions ORDER BY rowid DESC LIMIT 1"
    )
    plan = guarded.db.query_one(
        "SELECT plan_json FROM plans ORDER BY rowid DESC LIMIT 1"
    )
    plan_json = json.loads(plan["plan_json"])
    assert guarded_result["actions"] == 1
    assert guarded_action["tool"] == "noop"
    assert guarded_action["status"] == "SUCCEEDED"
    assert plan_json["memory_ids"] == [guidance.memory_id]
    assert "Applied validated decision guidance" in plan_json["rationale"]


def test_unvalidated_memory_is_retrieved_but_cannot_control_plan(runtime_factory, tmp_path):
    missing = tmp_path / "missing.txt"
    runtime = runtime_factory()
    memory = MemoryItem(
        memory_type="failure",
        content={
            "schema": "wls.action_guidance.v1",
            "match": {"tool": "read_file"},
            "effect": {"skip": True},
        },
        importance=1.0,
        confidence=1.0,
        source_ids=["unreviewed"],
        tags=["decision-guidance"],
    )
    runtime.memories.add(memory)
    runtime.ingest_event(_filesystem_event(str(missing)))
    runtime.run_cycle()
    action = runtime.db.query_one(
        "SELECT tool,status FROM actions ORDER BY rowid DESC LIMIT 1"
    )
    plan = json.loads(
        runtime.db.query_one(
            "SELECT plan_json FROM plans ORDER BY rowid DESC LIMIT 1"
        )["plan_json"]
    )
    assert action["tool"] == "read_file"
    assert action["status"] == "FAILED"
    assert plan["memory_ids"] == []


def test_internal_regulation_changes_runtime_action_budget(runtime_factory, tmp_path):
    normal = runtime_factory(home=tmp_path / "normal", max_actions_per_cycle=4)
    normal.ingest_event(_filesystem_event(str(tmp_path / "a.txt")))
    normal_budget = normal.run_cycle()["actions"]

    pressured = runtime_factory(home=tmp_path / "pressured", max_actions_per_cycle=4)
    drives, affect = pressured.drives.load()
    drives.safety = 0.0
    drives.resource_balance = 0.0
    affect.safety_tension = 1.0
    affect.fatigue = 1.0
    pressured.drives.save(drives, affect, {"test": "high pressure"})
    pressured.ingest_event(_filesystem_event(str(tmp_path / "b.txt")))
    result = pressured.run_cycle()

    assert normal_budget == 1
    assert result["actions"] == 0
    assert result["workspace_items"] >= 1


def test_attention_reserves_half_workspace_for_real_events(runtime_factory):
    runtime = runtime_factory(
        workspace_capacity=8,
        max_events_per_cycle=20,
        max_actions_per_cycle=0,
    )
    for index in range(20):
        runtime.memories.add(
            MemoryItem(
                memory_type="semantic",
                content={"pattern": f"high priority internal memory {index}"},
                importance=1.0,
                confidence=1.0,
                source_ids=[f"memory-source-{index}"],
            )
        )
    for index in range(10):
        runtime.ingest_event(
            Event(
                event_type="external.low-salience",
                source="external",
                payload={"index": index},
                salience_hint=0.0,
                dedupe_key=f"external:{index}",
            )
        )
    result = runtime.run_cycle()
    assert result["selected_events"] >= 4
    assert result["selected_events"] <= 6
    assert runtime.events.counts().get("PENDING", 0) <= 6


def test_episodic_memory_is_bounded_and_non_recursive(runtime_factory):
    runtime = runtime_factory(
        workspace_capacity=8,
        max_events_per_cycle=50,
        max_actions_per_cycle=0,
        sleep_after_idle_cycles=1000,
    )
    for index in range(40):
        runtime.ingest_event(
            Event(
                event_type="external.growth-test",
                source="test",
                payload={"index": index, "text": "x" * 1000},
                salience_hint=1.0,
                dedupe_key=f"growth:{index}",
            )
        )
    while runtime.events.counts().get("PENDING", 0):
        runtime.run_cycle()

    rows = runtime.db.query_all(
        "SELECT content_json,LENGTH(content_json) AS n FROM memories "
        "WHERE memory_type='episodic' ORDER BY created_at"
    )
    sizes = [int(row["n"]) for row in rows]
    assert rows
    assert max(sizes) < runtime.learning.MAX_EPISODE_BYTES
    assert max(sizes) < max(16_384, min(sizes) * 4)
    for row in rows:
        content = json.loads(row["content_json"])
        for item in content.get("workspace", []):
            if item.get("item_type") == "memory":
                payload = item.get("payload", {})
                assert "content" not in payload
                assert set(payload) <= {
                    "memory_id",
                    "memory_type",
                    "score",
                    "importance",
                    "confidence",
                    "tags",
                }


def test_large_tool_output_is_summarized_in_episode(runtime_factory):
    runtime = runtime_factory()
    huge = "z" * 500_000
    memory_id = runtime.learning.record_episode(
        cycle_id="cycle-large-output",
        event_ids=["event-large-output"],
        plan_id="plan-large-output",
        outcomes=[
            {
                "action_id": "act-large",
                "success": True,
                "status": "SUCCEEDED",
                "output": {"text": huge, "path": "large.txt"},
            }
        ],
        prediction_errors=[],
        workspace=[],
    )
    row = runtime.db.query_one(
        "SELECT content_json,LENGTH(content_json) AS n FROM memories WHERE memory_id=?",
        (memory_id,),
    )
    content = json.loads(row["content_json"])
    assert int(row["n"]) < 10_000
    text_projection = content["outcomes"][0]["output"]["text"]
    assert text_projection["truncated"] is True
    assert text_projection["bytes"] == len(huge)
    assert len(text_projection["sha256"]) == 64
