from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from wls.config import default_config
from wls.memory_influence import MemoryInfluenceAnalyzer
from wls.runtime import LivingSystem
from wls.schemas import Goal, MemoryItem, Observation


def test_memory_influence_marks_support_warn_and_penalizes_stale_low_confidence() -> None:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    analyzer = MemoryInfluenceAnalyzer()
    plan = {
        "rationale": "Inspect owner daily notes before writing automation.",
        "actions": [
            {
                "tool": "read_file",
                "purpose": "Inspect owner daily notes",
                "expected_result": "Bounded preview",
            }
        ],
    }
    memories = [
        {
            "memory_id": "support",
            "memory_type": "semantic",
            "content": {"claim": "Owner daily notes should be inspected first."},
            "importance": 0.8,
            "confidence": 0.9,
            "created_at": (now - timedelta(days=1)).isoformat(),
        },
        {
            "memory_id": "warn",
            "memory_type": "procedural",
            "content": {
                "decision_guidance": {
                    "effect": "avoid_tool",
                    "tool": "write_file",
                    "reason": "Avoid writing automation before owner approval.",
                }
            },
            "importance": 0.9,
            "confidence": 0.95,
            "created_at": (now - timedelta(days=2)).isoformat(),
        },
        {
            "memory_id": "stale",
            "memory_type": "episodic",
            "content": {"claim": "Old low confidence note about owner daily notes."},
            "importance": 0.9,
            "confidence": 0.2,
            "created_at": (now - timedelta(days=90)).isoformat(),
        },
    ]

    proof = analyzer.analyze_plan(plan=plan, memories=memories, now=now)

    by_id = {item["memory_id"]: item for item in proof["influences"]}
    assert by_id["support"]["stance"] == "support"
    assert by_id["warn"]["stance"] == "warn"
    assert "stale" in by_id["stale"]["penalties"]
    assert "low_confidence" in by_id["stale"]["penalties"]
    assert by_id["stale"]["effective_score"] < by_id["stale"]["score"]
    assert proof["stance_counts"]["warn"] >= 1
    assert proof["changed_decision_hint"] is True


def test_cycle_records_memory_influence_proof_and_life_state_exposes_it(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    target = runtime.config.sandbox_path / "notes.txt"
    target.write_text("daily owner notes", encoding="utf-8")
    goal_id = runtime.add_goal(
        Goal(
            title="Inspect owner daily notes",
            description=f"Read {target} before suggesting automation.",
            priority=0.9,
        )
    )
    support_memory_id = runtime.memories.add(
        MemoryItem(
            memory_type="semantic",
            content={
                "claim": "Owner daily notes should be inspected before proposing automation.",
                "goal_id": goal_id,
            },
            importance=0.85,
            confidence=0.9,
            source_ids=[goal_id],
            tags=["owner-notes"],
        )
    )
    warn_memory_id = runtime.memories.add(
        MemoryItem(
            memory_type="procedural",
            content={
                "decision_guidance": {
                    "effect": "avoid_tool",
                    "tool": "write_file",
                    "reason": "Avoid writing before approval.",
                },
                "goal_id": goal_id,
            },
            importance=0.95,
            confidence=0.95,
            source_ids=[goal_id],
            tags=["approval", "safety"],
        )
    )
    runtime.events.add_observation(
        Observation(
            source="owner-test",
            kind="external_event",
            subject="owner-request",
            predicate="request",
            value={"action": "inspect_path", "path": str(target.parent)},
            confidence=1.0,
        )
    )

    result = runtime.run_cycle()
    state = runtime.life_state()

    assert result["status"] == "SUCCEEDED"
    proof = result["memory_influence"]
    influenced_ids = {item["memory_id"] for item in proof["influences"]}
    assert support_memory_id in influenced_ids
    assert warn_memory_id in influenced_ids
    assert proof["selected_plan_memory_ids"]
    assert proof["authority"]["policy_and_approval_still_required"] is True
    assert state["memory_influence_proof"]["available"] is True
    assert state["memory_influence_proof"]["plan_id"] == proof["plan_id"]
    assert any(
        item["stance"] in {"support", "warn"}
        for item in state["memory_influence_proof"]["influences"]
    )


def make_runtime(tmp_path: Path) -> LivingSystem:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.sleep_after_idle_cycles = 100
    config.max_actions_per_cycle = 2
    return LivingSystem(config)
