"""Owner-only causal-memory ablation controls without manufactured advantage.

The equal-model/equal-goals paired experiment can use these modes later;
these tests verify isolation and fair retrieval, not held-out task gains.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import Goal, MemoryItem


def build_runtime(path: Path, mode: str = "enabled") -> LivingSystem:
    config = default_config(path)
    config.sensors = []
    config.sleep_after_idle_cycles = 100
    config.max_actions_per_cycle = 0
    config.provider["owner_context_mode"] = mode
    return LivingSystem(config)


def seed_memories(runtime: LivingSystem) -> tuple[str, str]:
    owner = runtime.memories.add(MemoryItem(
        memory_type="owner_context",
        content={"text": "Inspect the evidence before selecting a repair"},
        importance=1.0,
        confidence=0.95,
        source_ids=["owner_context:synthetic-task-method"],
    ))
    ordinary = runtime.memories.add(MemoryItem(
        memory_type="episodic",
        content={"text": "Inspect the evidence before selecting a repair"},
        importance=0.85,
        confidence=0.9,
        source_ids=["test-event:ordinary-memory"],
    ))
    return owner, ordinary


def test_owner_ablation_keeps_other_memories_eligible(tmp_path: Path) -> None:
    runtime = build_runtime(tmp_path / "eligible")
    owner, ordinary = seed_memories(runtime)
    kwargs = {"query": "Inspect the evidence before selecting a repair", "limit": 8}
    enabled = runtime.memories.retrieve_causal(**kwargs)
    disabled = runtime.memories.retrieve_causal(
        **kwargs, excluded_memory_types=frozenset({"owner_context"})
    )
    full_off = runtime.memories.retrieve_causal(**kwargs, enabled=False)
    assert owner in {r["memory_id"] for r in enabled["selected"]}
    assert ordinary in {r["memory_id"] for r in enabled["selected"]}
    assert owner not in {r["memory_id"] for r in disabled["selected"]}
    assert ordinary in {r["memory_id"] for r in disabled["selected"]}
    assert disabled["excluded_memory_types"] == ["owner_context"]
    assert full_off["selected"] == []


def test_exclusion_occurs_before_candidate_window_not_after_top_k(
    tmp_path: Path,
) -> None:
    runtime = build_runtime(tmp_path / "pressure")
    for i in range(165):
        runtime.memories.add(MemoryItem(
            memory_type="owner_context",
            content={"text": "Inspect the evidence before selecting a repair", "idx": i},
            importance=1.0,
            confidence=0.9,
            source_ids=[f"owner_context:synthetic-{i}"],
        ))
    other = runtime.memories.add(MemoryItem(
        memory_type="episodic",
        content={"text": "Inspect the evidence before selecting a repair"},
        importance=0.8,
        confidence=0.9,
        source_ids=["test-event:nonowner-165"],
    ))
    result = runtime.memories.retrieve_causal(
        "Inspect the evidence before selecting a repair",
        limit=1,
        excluded_memory_types=frozenset({"owner_context"}),
    )
    assert [item["memory_id"] for item in result["selected"]] == [other]


@pytest.mark.parametrize("mode,excluded", [
    ("enabled", frozenset()),
    ("disabled", frozenset({"owner_context"})),
])
def test_owner_mode_propagates_into_real_life_cycle(
    tmp_path: Path, mode: str, excluded: frozenset[str],
) -> None:
    runtime = build_runtime(tmp_path / mode, mode)
    seed_memories(runtime)
    runtime.add_goal(Goal(
        title="Inspect evidence of repair", description="Read outcomes before selecting",
    ))
    with patch.object(runtime.memories, "retrieve_causal", wraps=runtime.memories.retrieve_causal) as spy:
        result = runtime.run_cycle()
    assert result["status"] == "SUCCEEDED"
    assert spy.call_count == 1
    assert spy.call_args.kwargs["excluded_memory_types"] == excluded
    assert runtime.goals.active()
    assert runtime.db.query_one("SELECT COUNT(*) AS n FROM memories")["n"] >= 2


def test_invalid_owner_mode_fails_before_runtime_executes(tmp_path: Path) -> None:
    config = default_config(tmp_path / "bad")
    config.provider["owner_context_mode"] = "DISABLE EVERYTHING"
    with pytest.raises(ValueError, match="owner_context_mode"):
        LivingSystem(config)
