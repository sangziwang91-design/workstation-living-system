from __future__ import annotations

import json

from wls.learning import LearningSystem


def test_episode_workspace_compaction_preserves_observation_signature() -> None:
    workspace = [
        {
            "item_type": "event",
            "reference_id": "event-1",
            "summary": "x" * 1000,
            "salience": 0.9,
            "reasons": ["a", "b", "c", "d", "e", "f"],
            "payload": {
                "payload": {
                    "observation": {
                        "kind": "resource",
                        "subject": "host",
                        "predicate": "memory_used_ratio",
                        "value": 0.5,
                        "confidence": 1.0,
                        "observation_id": "obs-1",
                        "large": "ignored" * 1000,
                    }
                },
                "large": "ignored" * 1000,
            },
        },
        {
            "item_type": "memory",
            "reference_id": "mem-1",
            "summary": "memory",
            "salience": 0.5,
            "reasons": [],
            "payload": {"blob": "x" * 10000, "keep": "key"},
        },
    ]

    compact = LearningSystem._compact_episode_workspace(workspace)
    encoded = json.dumps(compact, sort_keys=True)

    observation = compact[0]["payload"]["payload"]["observation"]
    assert observation == {
        "kind": "resource",
        "subject": "host",
        "predicate": "memory_used_ratio",
        "value": 0.5,
        "confidence": 1.0,
        "observation_id": "obs-1",
    }
    assert compact[0]["summary"] == "x" * 500
    assert compact[0]["reasons"] == ["a", "b", "c", "d", "e"]
    assert compact[1]["payload"]["digest"]
    assert len(encoded) < 1200
