"""Endogenous read-only inquiry from repeated *resolved* prediction errors.

Synthetic DB fixtures test integration and forged-evidence rejection. They are
not proof of real-world open-ended autonomy or learned generalization.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import new_id, utc_now


def make_runtime(home: Path) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    config.sleep_after_idle_cycles = 100
    return LivingSystem(config)


def observed_refutation(
    runtime: LivingSystem, *, subject: str = "outside-system",
    predicate: str = "changes", score: float = 0.8,
    status: str = "REFUTED", resolved: bool = True,
) -> str:
    prediction_id = new_id("pred")
    runtime.db.execute(
        """INSERT INTO predictions(
            prediction_id,subject,predicate,expected_value_json,confidence,
            due_at,source,status,created_at,resolved_at,actual_value_json,error_score
        ) VALUES(?,?,?,'true',0.9,NULL,'observed-outcome',?,?,?,?,?)""",
        (
            prediction_id, subject, predicate, status,
            utc_now(), utc_now() if resolved else None,
            'false', score,
        ),
    )
    return prediction_id


def test_real_cycle_creates_only_bounded_inquiry_and_persists_it(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = make_runtime(home)
    ids = [observed_refutation(runtime) for _ in range(2)]
    assert runtime.db.query_all(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='world_model_revision'"
    ) == []

    report = runtime.run_cycle()
    assert report["status"] == "SUCCEEDED"
    candidates = runtime.db.query_all(
        "SELECT * FROM evolution_candidates WHERE candidate_type='world_model_revision'"
    )
    assert len(candidates) == 1
    assert candidates[0]["status"] == "PROPOSED"
    inquiries = runtime.db.query_all(
        "SELECT * FROM goals WHERE source='autonomy.inquiry'"
    )
    assert len(inquiries) == 1
    goal = runtime.goals.get(inquiries[0]["goal_id"])
    assert goal is not None
    assert goal.risk.value == "READ"
    assert goal.task_spec["operation"] == "INSPECT"
    assert goal.task_spec["proposal_only"] is True
    assert set(goal.task_spec["source_prediction_ids"]) == set(ids)
    assert runtime.db.query_all("SELECT growth_cycle_id FROM growth_cycles") == []
    assert runtime.db.query_all("SELECT skill_id FROM skills WHERE status='PROMOTED'") == []

    observed_refutation(runtime)  # new evidence does not fabricate a new candidate
    assert runtime.run_cycle()["status"] == "SUCCEEDED"
    runtime.db.close_all()
    restarted = make_runtime(home)
    assert restarted.run_cycle()["status"] == "SUCCEEDED"
    assert len(restarted.db.query_all(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='world_model_revision'"
    )) == 1
    assert len(restarted.db.query_all(
        "SELECT goal_id FROM goals WHERE source='autonomy.inquiry'"
    )) == 1
    assert restarted.verify_integrity(full=True)["ok"] is True


@pytest.mark.parametrize("mode", [
    "single",
    "unresolved",
    "unrefuted",
    "low_score",
    "mixed_keys",
])
def test_unverified_prediction_cases_do_not_create_inquiry(
    tmp_path: Path, mode: str,
) -> None:
    runtime = make_runtime(tmp_path / mode)
    observed_refutation(runtime)
    if mode == "unresolved":
        observed_refutation(runtime, resolved=False)
    elif mode == "unrefuted":
        observed_refutation(runtime, status="PENDING")
    elif mode == "low_score":
        observed_refutation(runtime, score=0.2)
    elif mode == "mixed_keys":
        observed_refutation(runtime, predicate="unrelated")
    elif mode != "single":
        raise AssertionError("unrecognized case")
    runtime.run_cycle()
    assert runtime.db.query_all(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='world_model_revision'"
    ) == []
    assert runtime.db.query_all(
        "SELECT goal_id FROM goals WHERE source='autonomy.inquiry'"
    ) == []


def test_rejected_world_model_hypothesis_is_not_reproposed(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path / "rejected")
    for _ in range(2):
        observed_refutation(runtime)
    runtime.run_cycle()
    row = runtime.db.query_one(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='world_model_revision'"
    )
    assert row
    runtime.db.execute(
        "UPDATE evolution_candidates SET status='REJECTED' WHERE candidate_id=?",
        (row["candidate_id"],),
    )
    observed_refutation(runtime)
    runtime.run_cycle()
    assert len(runtime.db.query_all(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='world_model_revision'"
    )) == 1


def test_ineligible_candidate_does_not_consume_inquiry_budget(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path / "fake")
    for _ in range(2):
        observed_refutation(runtime)
    # Create one legitimate candidate before changing its recorded source IDs.
    candidates = runtime.learning.create_prediction_error_candidates()
    assert len(candidates) == 1
    runtime.db.execute(
        "UPDATE evolution_candidates SET source_ids_json='[\"missing-a\",\"missing-b\"]' "
        "WHERE candidate_id=?",
        (candidates[0],),
    )
    assert runtime.autonomy.consider() == []
    assert runtime.goals.autonomous_count() == 0
    assert runtime.db.query_all("SELECT goal_id FROM goals WHERE source='autonomy.inquiry'") == []
