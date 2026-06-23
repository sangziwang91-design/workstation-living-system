from __future__ import annotations

import json
import time

import pytest

from wls.config import default_config
from wls.schemas import ActionSpec, CandidateStatus, RiskLevel, new_id, utc_now
from wls.v2_runtime import LivingSystemV2


def _insert_action(
    runtime: LivingSystemV2,
    *,
    status: str,
    acceptance: list[str],
    purpose: str,
    skill_id: str | None = None,
    error: str | None = None,
) -> ActionSpec:
    plan_id = new_id("plan")
    action = ActionSpec(
        tool="noop",
        arguments={},
        purpose=purpose,
        expected_result="noop contract accepted",
        acceptance=acceptance,
        risk=RiskLevel.READ,
        skill_id=skill_id,
        idempotency_key=new_id("idem"),
    )
    now = utc_now()
    with runtime.db.transaction() as connection:
        connection.execute(
            "INSERT INTO plans(plan_id,cycle_id,plan_json,status,created_at) VALUES (?,?,?,?,?)",
            (plan_id, new_id("cycle"), "{}", "PLANNED", now),
        )
        connection.execute(
            """
            INSERT INTO actions(
                action_id,plan_id,goal_id,skill_id,tool,arguments_json,purpose,
                expected_result,risk,acceptance_json,idempotency_key,status,
                started_at,finished_at,error,side_effect_class
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                action.action_id,
                plan_id,
                None,
                skill_id,
                action.tool,
                json.dumps(action.arguments),
                action.purpose,
                action.expected_result,
                action.risk.value,
                json.dumps(action.acceptance),
                action.idempotency_key,
                status,
                now if status != "PLANNED" else None,
                now if status != "PLANNED" else None,
                error,
                "none",
            ),
        )
    return action


def test_failure_to_skill_reuse_degradation_and_rollback(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystemV2(config)

    for index in range(3):
        _insert_action(
            runtime,
            status="FAILED",
            acceptance=["output impossible"],
            purpose=f"repeated failure {index}",
            error="acceptance criteria failed",
        )

    candidates = runtime.learning.create_failure_candidates(
        minimum_repeats=3, lookback_days=1
    )
    assert len(candidates) == 1
    candidate_id = candidates[0]

    recovery = runtime.recoveries.run(candidate_id, "contract_recovery")
    assert recovery["passed"] is True
    assert recovery["regressions"] == 0

    materialized = runtime.evolution.materialize_skill(
        candidate_id, recovery["experiment_id"]
    )
    skill_id = materialized["skill_id"]

    skill_experiment = runtime.skill_experiments.run(skill_id)
    assert skill_experiment["passed"] is True
    assert skill_experiment["candidate_cases"] == 3

    with pytest.raises(PermissionError):
        runtime.evolution.approve(candidate_id, skill_id, {"owner": "test"})
    runtime.evolution.approve(
        candidate_id, skill_id, {"owner": "test", "decision": "approve"}, True
    )

    with pytest.raises(PermissionError):
        runtime.evolution.promote(candidate_id, skill_id, {"owner": "test"})
    deployment = runtime.evolution.promote(
        candidate_id, skill_id, {"owner": "test", "decision": "promote"}, True
    )
    deployment_id = deployment["deployment_id"]

    time.sleep(0.002)
    successful_action = _insert_action(
        runtime,
        status="PLANNED",
        acceptance=["output ok is true"],
        purpose="real promoted-skill reuse succeeds",
        skill_id=skill_id,
    )
    successful_outcome = runtime._execute_action(successful_action)
    assert successful_outcome["success"] is True
    assert successful_outcome["evolution_observation"]["evaluation"]["status"] == "BENEFIT_VERIFIED"

    time.sleep(0.002)
    failing_action = _insert_action(
        runtime,
        status="PLANNED",
        acceptance=["output impossible"],
        purpose="real promoted-skill reuse regresses",
        skill_id=skill_id,
    )
    failing_outcome = runtime._execute_action(failing_action)
    assert failing_outcome["success"] is False
    assert failing_outcome["evolution_observation"]["evaluation"]["status"] == "DEGRADED"
    assert all(item["skill_id"] != skill_id for item in runtime.skills.active())

    with pytest.raises(PermissionError):
        runtime.evolution.rollback(deployment_id, {"reason": "regression"})
    rolled_back = runtime.evolution.rollback(
        deployment_id, {"reason": "measured regression"}, True
    )
    assert rolled_back["status"] == "ROLLED_BACK"

    skill_row = runtime.db.query_one(
        "SELECT status FROM skills WHERE skill_id=?", (skill_id,)
    )
    candidate_row = runtime.db.query_one(
        "SELECT status FROM evolution_candidates WHERE candidate_id=?", (candidate_id,)
    )
    deployment_row = runtime.db.query_one(
        "SELECT status FROM skill_deployments WHERE deployment_id=?", (deployment_id,)
    )
    assert skill_row["status"] == CandidateStatus.ROLLED_BACK.value
    assert candidate_row["status"] == CandidateStatus.ROLLED_BACK.value
    assert deployment_row["status"] == "ROLLED_BACK"
    assert runtime.db.integrity_check() == (True, "ok")
    assert runtime.ledger.verify()[0] is True


def test_legacy_config_aliases_load_into_v2(tmp_path) -> None:
    home = tmp_path / "legacy-home"
    config_path = home / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "home": str(home),
                "sensors": [],
                "skill_experiment_allowed_tools": ["noop"],
                "failure_recovery_min_cases": 4,
                "failure_recovery_allowed_tools": ["noop"],
            }
        ),
        encoding="utf-8",
    )
    runtime = LivingSystemV2.from_config_path(config_path)
    assert runtime.config.skill_validation_allowed_tools == ["noop"]
    assert runtime.config.recovery_validation_min_occurrences == 4
    assert runtime.config.recovery_validation_allowed_tools == ["noop"]
    assert runtime.db.integrity_check() == (True, "ok")
