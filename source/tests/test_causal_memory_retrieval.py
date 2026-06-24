from __future__ import annotations

from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import MemoryItem, utc_now


def make_runtime(tmp_path: Path) -> LivingSystem:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.sleep_after_idle_cycles = 100
    return LivingSystem(config)


def test_structured_entity_failure_and_applicability_retrieval(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    memory_id = runtime.memories.add(
        MemoryItem(
            memory_type="causal",
            content={
                "project_id": "project-alpha",
                "entity_id": "entity-service-a",
                "failure_signature": "http:timeout",
                "causal_hypothesis_id": "hyp-timeout-retry",
                "outcome_type": "FAILURE",
                "applicability_conditions": {"environment": "local"},
                "decision_guidance": {"effect": "avoid_tool", "tool": "http_get"},
            },
            importance=0.9,
            confidence=0.95,
            source_ids=["episode-alpha"],
        )
    )
    result = runtime.memories.retrieve_causal(
        "unrelated lexical text",
        8,
        context={
            "project_ids": ["project-alpha"],
            "entity_ids": ["entity-service-a"],
            "failure_signatures": ["http:timeout"],
            "causal_hypothesis_ids": ["hyp-timeout-retry"],
            "conditions": {"environment": "local"},
        },
    )
    assert [item["memory_id"] for item in result["selected"]] == [memory_id]
    reasons = result["selected"][0]["retrieval_reasons"]
    assert any(value.startswith("project:") for value in reasons)
    assert any(value.startswith("entity:") for value in reasons)
    assert any(value.startswith("failure:") for value in reasons)
    mismatch = runtime.memories.retrieve_causal(
        "http timeout",
        8,
        context={
            "project_ids": ["project-alpha"],
            "entity_ids": ["entity-service-a"],
            "conditions": {"environment": "cloud"},
        },
    )
    assert memory_id not in {item["memory_id"] for item in mismatch["selected"]}
    assert memory_id in {item["memory_id"] for item in mismatch["suppressed"]}


def test_expired_memory_is_suppressed_and_state_persists(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    memory_id = runtime.memories.add(
        MemoryItem(
            memory_type="causal",
            content={
                "project_id": "project-expired",
                "valid_until": "2000-01-01T00:00:00+00:00",
            },
            importance=1.0,
            confidence=1.0,
            source_ids=["expired-source"],
        )
    )
    result = runtime.memories.retrieve_causal(
        "expired", 8, context={"project_ids": ["project-expired"]}
    )
    assert memory_id in {item["memory_id"] for item in result["suppressed"]}
    assert runtime.memories.memory_state(memory_id)["validity_state"] == "EXPIRED"
    restarted = LivingSystem(runtime.config)
    assert restarted.memories.memory_state(memory_id)["validity_state"] == "EXPIRED"


def test_rolled_back_skill_supersedes_linked_memory(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    skill_id = "skill_et003_rolled_back"
    runtime.db.execute(
        """
        INSERT INTO skills(
            skill_id,name,version,definition_json,status,success_rate,
            use_count,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            skill_id,
            "rolled_back_fixture",
            1,
            '{"skill_id":"skill_et003_rolled_back","name":"rolled_back_fixture","version":1,"description":"fixture","trigger_terms":["fixture"],"steps":[{"tool":"noop","arguments":{}}],"risk":"READ","status":"ROLLED_BACK","success_rate":0.0,"use_count":0,"source_episode_ids":["fixture"],"created_at":"2026-01-01T00:00:00+00:00"}',
            "ROLLED_BACK",
            0.0,
            0,
            utc_now(),
            utc_now(),
        ),
    )
    memory_id = runtime.memories.add(
        MemoryItem(
            memory_type="procedural",
            content={"project_id": "project-rollback", "skill_id": skill_id},
            importance=1.0,
            confidence=1.0,
            source_ids=[skill_id],
        )
    )
    result = runtime.memories.retrieve_causal(
        "fixture",
        8,
        context={"project_ids": ["project-rollback"], "skill_ids": [skill_id]},
    )
    assert memory_id in {item["memory_id"] for item in result["suppressed"]}
    assert runtime.memories.memory_state(memory_id)["validity_state"] == "SUPERSEDED"
