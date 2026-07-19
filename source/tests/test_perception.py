from __future__ import annotations

from pathlib import Path

from wls.config import SensorConfig, default_config
from wls.perception import PerceptionClassifier
from wls.runtime import LivingSystem
from wls.schemas import Goal, MemoryItem


def test_perception_classifier_suppresses_noise_but_keeps_risk_and_owner_relevance() -> None:
    classifier = PerceptionClassifier()
    goals = [
        Goal(
            title="Restore daily owner notes loop",
            description="Notice changes in owner notes and propose bounded next steps.",
        )
    ]
    memories = [
        {
            "memory_id": "mem-owner-pref",
            "memory_type": "semantic",
            "content": {"claim": "Owner cares about daily notes and small steps."},
            "importance": 0.9,
            "confidence": 0.8,
        }
    ]
    observations = [
        {
            "observation_id": "obs-noise",
            "kind": "heartbeat",
            "subject": "clock",
            "predicate": "tick",
            "value": "unchanged",
            "confidence": 0.4,
            "verification": "UNKNOWN",
            "metadata": {},
            "salience": 0.05,
        },
        {
            "observation_id": "obs-risk",
            "kind": "resource",
            "subject": "host",
            "predicate": "disk_used_ratio",
            "value": 0.96,
            "confidence": 1.0,
            "verification": "VERIFIED",
            "metadata": {"warning": True},
            "salience": 0.8,
        },
        {
            "observation_id": "obs-owner",
            "kind": "filesystem_change",
            "subject": "owner/daily-notes.md",
            "predicate": "file_state",
            "value": "created",
            "confidence": 1.0,
            "verification": "VERIFIED",
            "metadata": {},
            "salience": 0.45,
        },
    ]

    summary = classifier.daily_summary(
        observations,
        goals=goals,
        memories=memories,
        limit=5,
        day="2026-07-15",
    )

    assert summary["class_counts"]["noise"] == 1
    assert summary["class_counts"]["risk"] == 1
    assert summary["noise_suppressed_count"] == 1
    top_ids = [item["observation_id"] for item in summary["top_daily_changes"]]
    assert "obs-risk" in top_ids
    assert "obs-owner" in top_ids
    assert "obs-noise" not in top_ids
    owner_item = next(
        item for item in summary["top_daily_changes"] if item["observation_id"] == "obs-owner"
    )
    assert owner_item["goal_links"]
    assert owner_item["memory_links"]


def test_cycle_preserves_daily_perception_and_life_state_shows_it(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "daily-notes.md").write_text("next small step", encoding="utf-8")
    config = default_config(tmp_path / "home")
    config.max_actions_per_cycle = 0
    config.sleep_after_idle_cycles = 100
    config.sensors = [
        SensorConfig(
            sensor_type="filesystem",
            name="owner_workspace",
            interval_seconds=0.01,
            settings={"roots": [str(watched)], "recursive": False},
        )
    ]
    runtime = LivingSystem(config)
    goal_id = runtime.add_goal(
        Goal(
            title="Track daily notes",
            description="Notice owner daily notes changes and keep the life loop current.",
            priority=0.9,
        )
    )
    runtime.memories.add(
        MemoryItem(
            memory_type="semantic",
            content={"claim": "Owner daily notes often imply the next bounded action."},
            importance=0.8,
            confidence=0.8,
            source_ids=[goal_id],
            tags=["owner-notes"],
        )
    )

    result = runtime.run_cycle()
    life_state = runtime.life_state()

    assert result["status"] == "SUCCEEDED"
    daily = result["daily_perception"]
    assert daily["observation_count"] >= 1
    assert daily["top_daily_changes"]
    assert daily["top_daily_changes"][0]["classification"] in {
        "context",
        "opportunity",
        "owner_relevant",
        "risk",
    }
    assert runtime.db.get_runtime("daily_perception", {})["top_daily_changes"]
    assert life_state["daily_perception"]["top_daily_changes"]
    assert life_state["latest_meaningful_observations"][0]["perception"][
        "classification"
    ] != "noise"
