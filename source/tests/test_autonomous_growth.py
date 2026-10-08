"""Regression proof: real WLS failures can select an endogenous growth goal.

This is not evidence of autonomous model coding or recursive improvement.
"""
from __future__ import annotations

from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Plan, RiskLevel, new_id, utc_now


def runtime_at(home: Path) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    config.sleep_after_idle_cycles = 100
    return LivingSystem(config)


def record_real_failure(runtime: LivingSystem) -> str:
    cycle_id = new_id("cycle")
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    action = ActionSpec(
        tool="noop",
        arguments={"reason": "repeated observed task contract failure"},
        purpose="observe actual tool acceptance failures",
        expected_result="a real, checked outcome",
        risk=RiskLevel.READ,
        acceptance=["output contains deliberately impossible receipt"],
    )
    plan = Plan(rationale="real failing action", actions=[action])
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    assert runtime._execute_plan(plan)[0]["success"] is False
    runtime.db.execute(
        "UPDATE cycles SET finished_at=?,status='SUCCEEDED' WHERE cycle_id=?",
        (utc_now(), cycle_id),
    )
    return action.action_id


def test_real_failure_growth_is_selected_once_and_survives_restart(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = runtime_at(home)
    source_ids = [record_real_failure(runtime) for _ in range(3)]
    assert not runtime.db.query_all(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='failure_repair'"
    )

    runtime.run_cycle()
    proposals = runtime.db.query_all(
        "SELECT * FROM evolution_candidates WHERE candidate_type='failure_repair'"
    )
    assert len(proposals) == 1
    candidate_id = proposals[0]["candidate_id"]
    goals = runtime.db.query_all(
        "SELECT * FROM goals WHERE source='autonomy.learning'"
    )
    assert len(goals) == 1
    assert goals[0]["title"] == f"Inspect recurring failures: {candidate_id}"
    assert goals[0]["risk"] == "READ"
    assert goals[0]["autonomous"] == 1

    # Further observations must not create a new proposal fingerprint.
    record_real_failure(runtime)
    runtime.run_cycle()
    runtime.db.close_all()

    restored = runtime_at(home)
    restored.run_cycle()
    assert len(restored.db.query_all(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='failure_repair'"
    )) == 1
    assert len(restored.db.query_all(
        "SELECT goal_id FROM goals WHERE source='autonomy.learning'"
    )) == 1
    assert len(source_ids) == 3
    assert restored.verify_integrity(full=True)["ok"] is True


def test_unknown_side_effect_does_not_become_learning_truth(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path / "uncertain")
    source_ids = [record_real_failure(runtime) for _ in range(3)]
    runtime.db.execute(
        "UPDATE actions SET status='UNKNOWN_SIDE_EFFECT',result_json=NULL WHERE action_id=?",
        (source_ids[-1],),
    )
    runtime.run_cycle()
    assert runtime.db.query_all(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='failure_repair'"
    ) == []
    assert runtime.db.query_all(
        "SELECT goal_id FROM goals WHERE source='autonomy.learning'"
    ) == []


def test_rejected_failure_hypothesis_is_not_silently_resurrected(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path / "rejection")
    for _ in range(3):
        record_real_failure(runtime)
    runtime.run_cycle()
    candidate = runtime.db.query_one(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='failure_repair'"
    )
    assert candidate is not None
    runtime.db.execute(
        "UPDATE evolution_candidates SET status='REJECTED' WHERE candidate_id=?",
        (candidate["candidate_id"],),
    )
    record_real_failure(runtime)
    runtime.run_cycle()
    assert len(runtime.db.query_all(
        "SELECT candidate_id FROM evolution_candidates WHERE candidate_type='failure_repair'"
    )) == 1
