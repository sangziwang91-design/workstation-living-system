"""Read-only evidence receipts for owner context; no simulated gain claims.

The synthetic attribution edits test reporting and corrupted/unrelated evidence
handling, not a real-world private-memory causal improvement.
"""
from __future__ import annotations

import json
from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import Goal, MemoryItem


def runtime_at(home: Path) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    config.sleep_after_idle_cycles = 100
    config.max_actions_per_cycle = 0
    return LivingSystem(config)


def owner_bundle() -> dict:
    return {
        "schema": "wls.owner_context.v1",
        "consent": "owner_reviewed_local_import",
        "entries": [
            {
                "id": "method-provenance",
                "kind": "method",
                "text": "PRIVATE FACT: prefer reproducible external evidence",
                "source_ref": "synthetic-fixture",
                "origin": "owner_direct",
            }
        ],
    }


def seed_attribution(runtime: LivingSystem) -> str:
    runtime.add_goal(Goal(title="Inspect test outcomes", description="Read observed task evidence"))
    result = runtime.run_cycle()
    assert result["status"] == "SUCCEEDED"
    row = runtime.db.query_one(
        "SELECT cycle_id FROM memory_decision_attributions ORDER BY created_at DESC LIMIT 1"
    )
    assert row is not None
    return str(row["cycle_id"])


def set_fixture(
    runtime: LivingSystem,
    cycle_id: str,
    memory_ids: list[str],
    *,
    changed: bool = False,
    success: bool | None = None,
) -> None:
    runtime.db.execute(
        "UPDATE memory_decision_attributions SET "
        "selected_memory_ids_json=?,memory_changed_decision=?,outcome_json=? "
        "WHERE cycle_id=?",
        (
            json.dumps(memory_ids),
            int(changed),
            json.dumps({"task_success": success}),
            cycle_id,
        ),
    )


def test_owner_evidence_starts_unknown_and_never_claims_benefit(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path / "base")
    empty = runtime.owner_context.effect_report()
    assert empty["status"] == "NO_IMPORTED_OWNER_MEMORY"
    assert empty["transfer_advantage_proven"] is False
    assert "PRIVATE FACT" not in json.dumps(empty)
    runtime.owner_context.import_bundle(owner_bundle(), source_sha256="a" * 64)
    not_used = runtime.owner_context.effect_report()
    assert not_used["status"] == "NO_OWNER_LINKED_DECISIONS"
    assert not_used["owner_linked_decisions"] == 0
    assert not_used["owner_specific_ablation_performed"] is False


def test_only_owner_linked_decisions_are_counted_and_mixed_is_not_isolated(
    tmp_path: Path,
) -> None:
    runtime = runtime_at(tmp_path / "mixed")
    runtime.owner_context.import_bundle(owner_bundle(), source_sha256="b" * 64)
    owner = str(runtime.db.query_one(
        "SELECT memory_id FROM memories WHERE memory_type='owner_context'"
    )["memory_id"])
    first = seed_attribution(runtime)
    set_fixture(runtime, first, ["unrelated-mem"], changed=True, success=True)
    assert runtime.owner_context.effect_report()["owner_linked_decisions"] == 0

    set_fixture(runtime, first, [owner], changed=True, success=True)
    isolated_in_memory_set = runtime.owner_context.effect_report()
    assert isolated_in_memory_set["owner_only_memory_decisions"] == 1
    assert isolated_in_memory_set["all_memory_counterfactual_changed"] == 1
    assert isolated_in_memory_set["recorded_action_success_labels"] == 1
    assert isolated_in_memory_set["transfer_advantage_proven"] is False
    assert "PRIVATE FACT" not in json.dumps(isolated_in_memory_set)

    other = runtime.memories.add(MemoryItem(
        memory_type="episodic", content={"text": "unrelated public"},
        importance=0.6, source_ids=["synthetic-test-event"],
    ))
    second = seed_attribution(runtime)
    set_fixture(runtime, second, [owner, other], changed=True, success=False)
    after = runtime.owner_context.effect_report()
    assert after["owner_linked_decisions"] == 2
    assert after["owner_only_memory_decisions"] == 1
    assert after["mixed_memory_decisions"] == 1
    assert after["observed_task_success"] == 1
    assert after["recorded_action_failure_labels"] == 1
    assert after["transfer_advantage_proven"] is False

    restarted = runtime_at(runtime.config.home_path)
    report = restarted.status()["owner_context_effect"]
    assert report["owner_linked_decisions"] == 2
    assert restarted.life_state()["owner_context_effect"]["mixed_memory_decisions"] == 1
    assert "PRIVATE FACT" not in json.dumps(report, ensure_ascii=False)


def test_malformed_or_unmeasured_attribution_does_not_fabricate_gain(
    tmp_path: Path,
) -> None:
    runtime = runtime_at(tmp_path / "broken")
    runtime.owner_context.import_bundle(owner_bundle(), source_sha256="c" * 64)
    owner = str(runtime.db.query_one(
        "SELECT memory_id FROM memories WHERE memory_type='owner_context'"
    )["memory_id"])
    cid = seed_attribution(runtime)
    runtime.db.execute(
        "UPDATE memory_decision_attributions "
        "SET selected_memory_ids_json='not-json' WHERE cycle_id=?",
        (cid,),
    )
    assert runtime.owner_context.effect_report()["owner_linked_decisions"] == 0
    set_fixture(runtime, cid, [owner], changed=False, success=None)
    report = runtime.owner_context.effect_report()
    assert report["status"] == "OWNER_CONTEXT_SELECTED_NO_DECISION_DIFFERENCE"
    assert report["unmeasured_outcomes"] == 1
    assert report["transfer_advantage_proven"] is False
