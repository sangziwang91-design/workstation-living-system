from __future__ import annotations

from pathlib import Path

import pytest

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, CandidateStatus, Goal, GoalStatus, Plan, RiskLevel, new_id, utc_now


def make_runtime(tmp_path: Path) -> LivingSystem:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.max_actions_per_cycle = 4
    config.sleep_after_idle_cycles = 100
    return LivingSystem(config)


def record_failed_action(
    runtime: LivingSystem,
    *,
    tool: str,
    arguments: dict,
    acceptance: list[str],
    purpose: str,
) -> str:
    cycle_id = new_id("cycle")
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    action = ActionSpec(
        tool=tool,
        arguments=arguments,
        purpose=purpose,
        expected_result="controlled failure is retained",
        risk=RiskLevel.READ,
        acceptance=acceptance,
    )
    plan = Plan(rationale="controlled reproducible failure", actions=[action])
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    outcomes = runtime._execute_plan(plan)
    runtime.db.execute(
        "UPDATE cycles SET finished_at=?,status='SUCCEEDED' WHERE cycle_id=?",
        (utc_now(), cycle_id),
    )
    assert outcomes[0]["success"] is False
    row = runtime.db.query_one(
        "SELECT status,error FROM actions WHERE action_id=?", (action.action_id,)
    )
    assert row is not None
    assert row["status"] == "FAILED"
    return action.action_id


def build_growth_cycle(
    runtime: LivingSystem,
    *,
    tool: str,
    arguments: dict,
    acceptance: list[str],
    purpose: str,
    strategy: str,
) -> tuple[str, str]:
    for _ in range(3):
        record_failed_action(
            runtime,
            tool=tool,
            arguments=arguments,
            acceptance=acceptance,
            purpose=purpose,
        )
    candidate_ids = runtime.learning.create_failure_candidates(minimum_repeats=3)
    assert len(candidate_ids) == 1
    candidate_id = candidate_ids[0]
    recovery = runtime.growth.run_recovery_experiment(candidate_id, strategy)
    assert recovery["status"] == "PASSED"
    assert recovery["candidate_pass_rate"] == 1.0
    assert recovery["baseline_pass_rate"] == 0.0
    proposal = runtime.growth.propose_skill_from_recovery(
        candidate_id, recovery["experiment_id"]
    )
    validation = runtime.growth.validate_skill(proposal["growth_cycle_id"])
    assert validation["status"] == "PASSED"
    assert validation["regressions"] == 0
    runtime.growth.approve_and_promote(
        proposal["growth_cycle_id"],
        actor="owner-test-fixture",
        authorization_reference="pytest://explicit-owner-approval",
        human_approved=True,
    )
    return proposal["growth_cycle_id"], proposal["skill_id"]


def test_complete_failure_to_skill_to_reuse_retains_improvement(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    growth_cycle_id, skill_id = build_growth_cycle(
        runtime,
        tool="noop",
        arguments={"reason": "repeatable contract failure"},
        acceptance=["output contains impossible"],
        purpose="recover repeatable noop workflow",
        strategy="contract_recovery",
    )

    result = runtime.growth.reuse_on_runtime_task(growth_cycle_id)

    assert result["measurement"]["decision"] == "RETAIN"
    assert result["measurement"]["actual_success_rate"] == 1.0
    assert result["measurement"]["baseline_success_rate"] == 0.0
    assert result["measurement"]["regressions"] == 0
    skill = runtime.db.query_one(
        "SELECT status,use_count,success_rate FROM skills WHERE skill_id=?", (skill_id,)
    )
    assert skill is not None
    assert skill["status"] == CandidateStatus.PROMOTED.value
    assert skill["use_count"] == 1
    assert skill["success_rate"] == 1.0
    growth = runtime.db.query_one(
        "SELECT status FROM growth_cycles WHERE growth_cycle_id=?",
        (growth_cycle_id,),
    )
    assert growth is not None
    assert growth["status"] == "RETAINED"
    assert runtime.verify_integrity(full=True)["ok"] is True


def test_failed_real_reuse_triggers_and_tests_rollback(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    missing_path = runtime.config.home_path / "never-created.txt"
    growth_cycle_id, skill_id = build_growth_cycle(
        runtime,
        tool="read_file",
        arguments={"path": str(missing_path), "max_bytes": 4096},
        acceptance=["output contains path", "output contains text or binary marker"],
        purpose="read a file that is absent in the real environment",
        strategy="fixture_recovery",
    )

    reuse = runtime.growth.reuse_on_runtime_task(growth_cycle_id)
    assert reuse["measurement"]["decision"] == "ROLLBACK_REQUIRED"
    assert reuse["measurement"]["actual_success_rate"] == 0.0
    assert reuse["measurement"]["regressions"] == 1

    rollback = runtime.growth.rollback(
        growth_cycle_id,
        actor="owner-test-fixture",
        authorization_reference="pytest://explicit-owner-rollback",
        human_approved=True,
    )
    assert rollback["rollback"]["passed"] is True
    skill = runtime.db.query_one(
        "SELECT status FROM skills WHERE skill_id=?", (skill_id,)
    )
    assert skill is not None
    assert skill["status"] == CandidateStatus.ROLLED_BACK.value
    assert skill_id not in {item["skill_id"] for item in runtime.skills.active()}
    assert runtime.verify_integrity(full=True)["ok"] is True


def test_machine_transitions_reject_fabricated_evidence(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    for _ in range(3):
        record_failed_action(
            runtime,
            tool="noop",
            arguments={"reason": "fabricated evidence test"},
            acceptance=["output contains impossible"],
            purpose="preserve a failed action",
        )
    candidate_id = runtime.learning.create_failure_candidates(minimum_repeats=3)[0]
    with pytest.raises(ValueError, match="persisted recovery experiment"):
        runtime.learning.transition_candidate(
            candidate_id,
            CandidateStatus.SANDBOXED,
            {"passed": True},
        )


@pytest.mark.parametrize(
    "tamper",
    ["unknown", "missing_result", "accepted", "missing_start", "missing_source", "duplicate"],
)
def test_recovery_rejects_untrusted_failure_evidence(
    tmp_path: Path, tamper: str,
) -> None:
    """A manual API call must not reinterpret unresolved/fabricated actions."""
    import json

    runtime = make_runtime(tmp_path)
    ids = [
        record_failed_action(
            runtime,
            tool="noop",
            arguments={"reason": "negative evidence control"},
            acceptance=["output contains impossible"],
            purpose="real independently evaluated failed tool",
        )
        for _ in range(3)
    ]
    candidate = runtime.learning.create_failure_candidates(minimum_repeats=3)[0]
    if tamper == "unknown":
        runtime.db.execute(
            "UPDATE actions SET status='UNKNOWN_SIDE_EFFECT' WHERE action_id=?",
            (ids[0],),
        )
    elif tamper == "missing_result":
        runtime.db.execute(
            "UPDATE actions SET result_json=NULL WHERE action_id=?", (ids[0],)
        )
    elif tamper == "accepted":
        runtime.db.execute(
            "UPDATE actions SET result_json=? WHERE action_id=?",
            (json.dumps({"evaluation": {"accepted": True}}), ids[0]),
        )
    elif tamper == "missing_start":
        runtime.db.execute(
            "UPDATE actions SET started_at=NULL WHERE action_id=?", (ids[0],)
        )
    elif tamper == "missing_source":
        runtime.db.execute(
            "UPDATE evolution_candidates SET source_ids_json=? WHERE candidate_id=?",
            (json.dumps([ids[0], ids[1], "missing-action-id"]), candidate),
        )
    else:
        runtime.db.execute(
            "UPDATE evolution_candidates SET source_ids_json=? WHERE candidate_id=?",
            (json.dumps([ids[0], ids[0], ids[1]]), candidate),
        )
    with pytest.raises(ValueError, match="recovery"):
        runtime.growth.run_recovery_experiment(candidate)
    assert runtime.db.query_all("SELECT experiment_id FROM recovery_experiments") == []
    assert runtime.db.query_all("SELECT skill_id FROM skills WHERE status='PROMOTED'") == []


def test_crashed_recovery_is_durable_and_not_replayed_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real persisted RUNNING trial is an interruption, never a proven fix."""
    runtime = make_runtime(tmp_path)
    for _ in range(3):
        record_failed_action(
            runtime, tool="noop", arguments={"reason": "crash recovery case"},
            acceptance=["output contains impossible"],
            purpose="repeated failure before process termination",
        )
    candidate_id = runtime.learning.create_failure_candidates()[0]
    goal_id = runtime.goals.add(Goal(
        title=f"Inspect recurring failures: {candidate_id}",
        description="Investigate an observed failure",
        source="autonomy.learning", autonomous=True, risk=RiskLevel.READ,
    ))

    def simulated_crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated process termination during sandbox execution")

    monkeypatch.setattr(runtime.growth, "_run_recovery_case", simulated_crash)
    with pytest.raises(RuntimeError, match="simulated process termination"):
        runtime.growth.run_recovery_experiment(candidate_id)

    before = runtime.db.query_one(
        "SELECT experiment_id,status,result_json FROM recovery_experiments WHERE candidate_id=?",
        (candidate_id,),
    )
    assert before is not None
    assert before["status"] == "RUNNING"
    assert before["result_json"] is None
    runtime.db.close_all()

    restored = make_runtime(tmp_path)
    after = restored.db.query_one(
        "SELECT experiment_id,status,finished_at,result_json FROM recovery_experiments "
        "WHERE candidate_id=?", (candidate_id,),
    )
    assert after is not None
    assert after["experiment_id"] == before["experiment_id"]
    assert after["status"] == "INTERRUPTED"
    assert after["finished_at"] is not None
    assert after["result_json"] is None
    goal = restored.goals.get(goal_id)
    assert goal is not None and goal.status == GoalStatus.FAILED
    assert restored.goals.autonomous_count() == 0
    assert restored.db.query_all("SELECT skill_id FROM skills WHERE status='PROMOTED'") == []
    assert restored.growth.recover_interrupted_experiments() == {
        "recovery": 0, "validation": 0,
    }
    restored.run_cycle()
    assert len(restored.db.query_all(
        "SELECT experiment_id FROM recovery_experiments WHERE candidate_id=?",
        (candidate_id,),
    )) == 1
    assert restored.verify_integrity(full=True)["ok"] is True


def test_crashed_skill_validation_never_silently_promotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-finished validation requires reconciliation, not automatic approval."""
    from wls.growth_cycle import IsolatedToolHarness

    runtime = make_runtime(tmp_path)
    for _ in range(3):
        record_failed_action(
            runtime, tool="noop", arguments={"reason": "crash skill experiment"},
            acceptance=["output contains impossible"],
            purpose="recovery evidence for interrupted validation",
        )
    candidate_id = runtime.learning.create_failure_candidates()[0]
    recovery = runtime.growth.run_recovery_experiment(candidate_id)
    assert recovery["status"] == "PASSED"
    proposed = runtime.growth.propose_skill_from_recovery(
        candidate_id, recovery["experiment_id"],
    )
    growth_id = proposed["growth_cycle_id"]
    skill_id = proposed["skill_id"]

    def simulated_crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated process termination during skill validation")

    with monkeypatch.context() as patcher:
        patcher.setattr(IsolatedToolHarness, "execute", simulated_crash)
        with pytest.raises(RuntimeError, match="simulated process termination"):
            runtime.growth.validate_skill(growth_id)
    started = runtime.db.query_one(
        "SELECT experiment_id,status FROM skill_experiments WHERE skill_id=?",
        (skill_id,),
    )
    assert started is not None and started["status"] == "RUNNING"
    runtime.db.close_all()

    restored = make_runtime(tmp_path)
    recovered = restored.db.query_one(
        "SELECT experiment_id,status,finished_at,result_json "
        "FROM skill_experiments WHERE skill_id=?",
        (skill_id,),
    )
    assert recovered is not None
    assert recovered["experiment_id"] == started["experiment_id"]
    assert recovered["status"] == "INTERRUPTED"
    assert recovered["finished_at"] is not None
    assert recovered["result_json"] is None
    record = restored.db.query_one(
        "SELECT status FROM growth_cycles WHERE growth_cycle_id=?", (growth_id,),
    )
    assert record is not None and record["status"] == "RECONCILIATION_REQUIRED"
    assert restored.db.query_all(
        "SELECT skill_id FROM skills WHERE skill_id=? AND status='PROMOTED'",
        (skill_id,),
    ) == []
    with pytest.raises(ValueError, match="promotion cannot start"):
        restored.growth.approve_and_promote(
            growth_id, "owner-fixture", "test://recovery", human_approved=True,
        )
    assert restored.growth.recover_interrupted_experiments() == {
        "recovery": 0, "validation": 0,
    }
    assert restored.verify_integrity(full=True)["ok"] is True


def test_crash_before_candidate_stage_is_not_restarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable RUNNING receipt can precede the candidate's SANDBOXED state."""
    runtime = make_runtime(tmp_path)
    for _ in range(3):
        record_failed_action(
            runtime, tool="noop", arguments={"reason": "pre-stage crash"},
            acceptance=["output contains impossible"],
            purpose="unexpected exit after experimental intent is saved",
        )
    candidate_id = runtime.learning.create_failure_candidates()[0]

    def simulated_crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("crashed between experiment receipt and candidate stage")

    with monkeypatch.context() as patcher:
        patcher.setattr(runtime.learning, "transition_candidate", simulated_crash)
        with pytest.raises(RuntimeError, match="crashed between experiment"):
            runtime.growth.run_recovery_experiment(candidate_id)
    before = runtime.db.query_one(
        "SELECT status FROM evolution_candidates WHERE candidate_id=?",
        (candidate_id,),
    )
    assert before is not None and before["status"] == "PROPOSED"
    assert len(runtime.db.query_all(
        "SELECT experiment_id FROM recovery_experiments WHERE candidate_id=? "
        "AND status='RUNNING'",
        (candidate_id,),
    )) == 1
    runtime.db.close_all()

    restored = make_runtime(tmp_path)
    candidate = restored.db.query_one(
        "SELECT status,experiment_json FROM evolution_candidates WHERE candidate_id=?",
        (candidate_id,),
    )
    assert candidate is not None
    assert candidate["status"] == "SANDBOXED"
    assert '"INTERRUPTED"' in candidate["experiment_json"]
    restored.run_cycle()
    assert restored.db.query_all(
        "SELECT goal_id FROM goals WHERE source='autonomy.learning'"
    ) == []
    assert len(restored.db.query_all(
        "SELECT experiment_id FROM recovery_experiments WHERE candidate_id=?",
        (candidate_id,),
    )) == 1
    assert restored.growth.recover_interrupted_experiments() == {
        "recovery": 0, "validation": 0,
    }
    assert restored.verify_integrity(full=True)["ok"] is True
