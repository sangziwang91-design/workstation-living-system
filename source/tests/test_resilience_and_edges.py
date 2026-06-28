from __future__ import annotations


import pytest

from wls.config import SensorConfig
from wls.planner import PlanningProvider
from wls.runtime import LivingSystem
from wls.schemas import (
    ActionSpec,
    Event,
    Observation,
    Plan,
    RiskLevel,
    VerificationStatus,
)


class FixedProvider(PlanningProvider):
    def __init__(self, payload):
        self.payload = payload

    def create_plan(self, context):
        return self.payload


def test_orphaned_planned_read_action_is_resumed_after_restart(runtime_factory, tmp_path):
    home = tmp_path / "resume-home"
    runtime = runtime_factory(home=home)
    target = home / "input.txt"
    target.write_text("durable", encoding="utf-8")
    event = Event(event_type="test.resume", source="test", payload={"path": str(target)})
    event_id, _ = runtime.ingest_event(event)
    reserved = runtime.events.reserve(runtime.worker_id, 1)
    assert [item.event_id for item in reserved] == [event_id]
    plan = Plan(
        rationale="persist before simulated crash",
        actions=[
            ActionSpec(
                tool="read_file",
                arguments={"path": str(target)},
                purpose="resume a durably planned read",
                expected_result="file content",
                risk=RiskLevel.READ,
                acceptance=["output contains text"],
            )
        ],
    )
    runtime._persist_plan_and_ack_events("cycle_simulated", plan, [event_id])

    restarted = LivingSystem(runtime.config)
    result = restarted.run_cycle()
    row = restarted.db.query_one("SELECT status,result_json FROM actions WHERE action_id=?", (plan.actions[0].action_id,))
    assert row is not None and row["status"] == "SUCCEEDED"
    assert any(item.get("recovered") for item in result["outcomes"])


def test_cycle_failure_after_plan_persist_does_not_requeue_event(runtime_factory, monkeypatch):
    runtime = runtime_factory()
    runtime.planner.provider = FixedProvider(
        {
            "rationale": "grounded no-op",
            "actions": [
                {
                    "tool": "noop",
                    "arguments": {"ok": True},
                    "purpose": "durable test",
                    "expected_result": "no external change",
                    "risk": "READ",
                    "goal_id": None,
                    "skill_id": None,
                    "acceptance": ["output ok is true"],
                }
            ],
            "memory_ids": [],
            "world_fact_ids": [],
            "unknowns": [],
        }
    )
    event_id, _ = runtime.ingest_event(
        Event(event_type="test.persisted", source="test", payload={"value": 1}, salience_hint=1.0)
    )

    def fail_after_actions(*args, **kwargs):
        raise RuntimeError("simulated post-plan failure")

    monkeypatch.setattr(runtime.learning, "record_episode", fail_after_actions)
    with pytest.raises(RuntimeError, match="post-plan"):
        runtime.run_cycle()
    row = runtime.db.query_one("SELECT status FROM events WHERE event_id=?", (event_id,))
    assert row is not None and row["status"] == "PROCESSED"
    assert runtime.db.query_one("SELECT COUNT(*) AS n FROM plans")["n"] == 1


def test_chinese_memory_is_retrievable(runtime_factory):
    runtime = runtime_factory()
    from wls.schemas import MemoryItem

    runtime.memories.add(
        MemoryItem(
            memory_type="semantic",
            content={"finding": "烧伤创面感染需要持续观察"},
            importance=0.8,
            confidence=0.9,
            source_ids=["manual-evidence"],
        )
    )
    matches = runtime.memories.retrieve("创面感染", 5)
    assert matches and "烧伤" in matches[0]["content"]["finding"]


def test_refutation_deactivates_weak_fact(runtime_factory):
    runtime = runtime_factory()
    first = Observation(source="sensor", kind="state", subject="service", predicate="online", value=True, confidence=0.5)
    runtime.events.add_observation(first)
    runtime.world.assimilate(first)
    refutation = Observation(
        source="sensor",
        kind="state",
        subject="service",
        predicate="online",
        value=True,
        confidence=1.0,
        verification=VerificationStatus.REFUTED,
    )
    runtime.events.add_observation(refutation)
    result = runtime.world.assimilate(refutation)
    assert result["action"] == "refuted"
    assert runtime.world.active_facts(subject="service") == []


def test_custom_sensor_plugin_can_be_loaded(runtime_factory, tmp_path, monkeypatch):
    module_path = tmp_path / "custom_sensor_module.py"
    module_path.write_text(
        """
from wls.sensors.base import Sensor
from wls.schemas import Observation
class CustomSensor(Sensor):
    def poll(self, previous_state):
        return [Observation(source=self.name, kind='custom', subject='plugin', predicate='loaded', value=True, metadata={'dedupe_key':'plugin-loaded'})], {'ok': True}
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    runtime = runtime_factory(
        sensors=[SensorConfig(sensor_type="custom_sensor_module:CustomSensor", name="custom", interval_seconds=0.001)]
    )
    result = runtime.run_cycle()
    assert result["sensors"][0]["status"] == "ok"
    assert runtime.world.active_facts(subject="plugin")[0]["value"] is True


def test_approval_cannot_be_issued_twice_or_from_wrong_state(runtime_factory):
    runtime = runtime_factory()
    with pytest.raises(KeyError):
        runtime.approvals.issue("missing", True)
    # No action is waiting yet, so constructing an arbitrary approval target is impossible.
    assert runtime.db.query_one("SELECT COUNT(*) AS n FROM approvals")["n"] == 0


def test_external_event_cannot_be_starved_by_internal_memory(runtime_factory):
    runtime = runtime_factory(workspace_capacity=2, max_events_per_cycle=10, max_actions_per_cycle=0)
    from wls.schemas import MemoryItem

    for index in range(12):
        runtime.memories.add(
            MemoryItem(
                memory_type="semantic",
                content={"pattern": f"high importance internal memory {index}"},
                importance=1.0,
                confidence=1.0,
                source_ids=[f"source-{index}"],
            )
        )
    event_id, _ = runtime.ingest_event(
        Event(
            event_type="external.low_salience",
            source="external",
            payload={"change": "real"},
            salience_hint=0.0,
        )
    )
    result = runtime.run_cycle()
    row = runtime.db.query_one("SELECT status FROM events WHERE event_id=?", (event_id,))
    assert result["selected_events"] == 1
    assert row is not None and row["status"] == "PROCESSED"
