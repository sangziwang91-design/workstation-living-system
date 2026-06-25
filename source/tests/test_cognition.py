from __future__ import annotations

from pathlib import Path
import os

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import (
    EvidenceKind,
    Goal,
    MemoryItem,
    Observation,
    VerificationStatus,
    new_id,
    utc_now,
)


def make_runtime(tmp_path: Path, *, provider: dict | None = None) -> LivingSystem:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.max_actions_per_cycle = 4
    config.sleep_after_idle_cycles = 100
    if provider is not None:
        config.provider = provider
    return LivingSystem(config)


def ingest(runtime: LivingSystem, observation: Observation) -> str:
    event_id, _ = runtime.events.add_observation(observation)
    runtime.world.assimilate(observation)
    return event_id


def test_default_runtime_uses_bounded_local_cognition(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    assert runtime.config.provider["type"] == "cognitive"
    assert runtime.planner.provider_type == "cognitive"
    assert runtime.status()["cognition"]["traces"] == 0


def test_local_cognition_observes_predicts_acts_calibrates_and_survives_restart(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    target = runtime.config.home_path / "sandbox" / "observed"
    target.mkdir(parents=True)
    (target / "evidence.txt").write_text("direct evidence", encoding="utf-8")
    observation = Observation(
        source="owner-test",
        kind="external_event",
        subject="owner-request",
        predicate="request",
        value={"action": "inspect_path", "path": str(target)},
        confidence=1.0,
        evidence_kind=EvidenceKind.DIRECT,
        verification=VerificationStatus.VERIFIED,
    )
    ingest(runtime, observation)

    result = runtime.run_cycle()

    assert result["status"] == "SUCCEEDED"
    assert result["cognition"] is not None
    assert result["cognition"]["selected_key"].startswith("inspect_requested_path:")
    assert result["cognition"]["predictions"][0]["status"] == "CONFIRMED"
    assert result["cognition"]["calibration"]["updated"] is True
    assert runtime.temporal_world.summary()["causal_trials"] == 1
    assert runtime.cognition.integrity()[0] is True

    restarted = LivingSystem(runtime.config)
    traces = restarted.cognition.recent(limit=5)
    assert traces[0]["status"] == "RESOLVED"
    assert traces[0]["selected_key"].startswith("inspect_requested_path:")
    assert restarted.temporal_world.summary()["causal_trials"] == 1
    integrity = restarted.verify_integrity(full=True)
    assert integrity["ok"] is True, integrity


def test_promoted_procedural_memory_changes_the_selected_behavior(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    candidate_id = new_id("candidate")
    now = utc_now()
    runtime.db.execute(
        """
        INSERT INTO evolution_candidates(
            candidate_id,candidate_type,title,proposal_json,source_ids_json,
            baseline_json,experiment_json,result_json,status,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate_id,
            "recovery_rule",
            "Avoid a repeatedly harmful note path",
            "{}",
            '["test-source"]',
            "{}",
            "{}",
            "{}",
            "PROMOTED",
            now,
            now,
        ),
    )
    memory = MemoryItem(
        memory_type="procedural",
        content={
            "decision_rule": {
                "effect": "avoid_tool",
                "tool": "emit_note",
                "source_candidate_id": candidate_id,
            },
            "mechanism": "sensor error note path repeatedly amplified failure",
        },
        importance=1.0,
        confidence=0.99,
        source_ids=[candidate_id],
        tags=["promoted_recovery", "decision_rule"],
    )
    memory_id = runtime.memories.add(memory)
    retrieved = runtime.memories.retrieve("sensor error emit note", limit=8)
    assert memory_id in {item["memory_id"] for item in retrieved}
    observation = Observation(
        source="sensor-test",
        kind="sensor_error",
        subject="sensor-test",
        predicate="poll_status",
        value="failed",
        confidence=1.0,
    )
    event_id = ingest(runtime, observation)
    event = runtime.db.query_one("SELECT * FROM events WHERE event_id=?", (event_id,))
    assert event is not None
    reserved = runtime.events.reserve(runtime.worker_id, 1)
    workspace_items = runtime.attention.select(
        reserved,
        [],
        retrieved,
        *runtime.drives.load(),
    )
    context = {
        "cycle_id": new_id("cycle"),
        "workspace": [item.to_dict() for item in workspace_items],
        "goals": [],
        "world_facts": runtime.world.active_facts(limit=20),
        "memories": retrieved,
        "matching_skills": [],
        "self_model": runtime.self_model.snapshot(),
        "relationships": runtime.relationships.snapshot(),
        "drives": runtime.drives.load()[0].to_dict(),
        "affect": runtime.drives.load()[1].to_dict(),
        "budget": {"max_actions": 2},
        "unknowns": [],
        "available_tools": sorted(runtime.tools._tools),
        "paths": {
            "home": str(runtime.config.home_path),
            "sandbox": str(runtime.config.sandbox_path),
            "outbox": str(runtime.config.outbox_path),
        },
    }

    plan = runtime.planner.plan(context)
    trace = runtime.cognition.recent(limit=1)[0]

    assert trace["counterfactual_key"].startswith("preserve_attention:")
    assert trace["selected_key"].startswith("memory_avoid_tool:")
    assert trace["memory_changed_decision"] is True
    assert trace["memory_delta"] > 0
    assert plan.actions[0].tool == "noop"
    assert memory_id in plan.memory_ids


def test_unsupported_goal_produces_explicit_noop(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.add_goal(
        Goal(
            title="Consider an unsupported possibility",
            description="No target, evidence, or action authority is supplied.",
            priority=0.6,
            success_criteria=["uncertainty remains explicit"],
        )
    )

    result = runtime.run_cycle()

    assert result["status"] == "SUCCEEDED"
    assert result["actions"] == 1
    assert result["outcomes"][0]["success"] is True
    action = runtime.db.query_one(
        "SELECT tool,status FROM actions ORDER BY rowid DESC LIMIT 1"
    )
    assert action is not None
    assert action["tool"] == "noop"
    assert action["status"] == "SUCCEEDED"


def test_external_provider_failure_falls_back_to_local_cognition(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("WLS_TEST_MISSING_KEY", raising=False)
    runtime = make_runtime(
        tmp_path,
        provider={
            "type": "openai_compatible",
            "base_url": "http://127.0.0.1:9/v1",
            "model": "unavailable-test-model",
            "api_key_env": "WLS_TEST_MISSING_KEY",
            "fallback": "cognitive",
        },
    )
    target = runtime.config.sandbox_path / "fallback-target"
    target.mkdir(parents=True)
    ingest(
        runtime,
        Observation(
            source="owner-test",
            kind="external_event",
            subject="owner-request",
            predicate="request",
            value={"action": "inspect_path", "path": str(target)},
        ),
    )

    result = runtime.run_cycle()

    assert result["status"] == "SUCCEEDED"
    assert result["cognition"] is not None
    fallback = runtime.db.query_one(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='planning_provider_fallback' ORDER BY seq DESC LIMIT 1"
    )
    assert fallback is not None
    assert os.environ.get("WLS_TEST_MISSING_KEY") is None
