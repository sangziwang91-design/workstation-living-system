from __future__ import annotations

from pathlib import Path

import pytest

from wls.adaptive_growth import SkillExperimentRunner
from wls.bounded_recovery import FailureRecoveryEngine
from wls.experiments import IsolatedToolHarness
from wls.schemas import ActionSpec, CandidateStatus, Plan, RiskLevel, SkillDefinition


def _persist_and_execute(runtime, plan: Plan, cycle_id: str):
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    return runtime._execute_plan(plan)


def test_experiment_schema_tables_exist(runtime_factory):
    runtime = runtime_factory()
    tables = {
        row["name"]
        for row in runtime.db.query_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"skill_experiments", "recovery_experiments"} <= tables


def test_isolated_harness_blocks_unlisted_tool_and_contains_paths(tmp_path):
    harness = IsolatedToolHarness(tmp_path / "harness", ["noop", "read_file"])
    assert harness.execute("noop", {}, "bounded no-op")["passed"] is True
    denied = harness.execute(
        "run_command",
        {"command": ["python", "-c", "print('escape')"]},
        "must not execute",
    )
    assert denied == {
        "passed": False,
        "tool": "run_command",
        "reason": "tool_not_allowed_for_isolated_validation",
    }
    mapped = harness.map_path("../../outside/secret.txt").resolve()
    assert mapped.is_relative_to(harness.root)
    assert "_parent_" in mapped.parts


def test_skill_experiment_validates_but_never_auto_promotes(runtime_factory):
    runtime = runtime_factory()
    action_ids: list[str] = []
    for index in range(3):
        plan = Plan(
            rationale=f"historical success {index}",
            actions=[
                ActionSpec(
                    tool="noop",
                    arguments={"case": index},
                    purpose="repeat bounded success",
                    expected_result="no-op succeeds",
                    risk=RiskLevel.READ,
                    acceptance=["output ok is true"],
                )
            ],
        )
        outcomes = _persist_and_execute(runtime, plan, f"skill_history_{index}")
        assert outcomes[0]["success"] is True
        action_ids.append(plan.actions[0].action_id)

    skill = SkillDefinition(
        name="validated_noop_sequence",
        description="A bounded declarative sequence reconstructed from three successes.",
        trigger_terms=["bounded", "success"],
        steps=[
            {
                "tool": "noop",
                "arguments": {"case": "replay"},
                "purpose": "repeat bounded success",
                "acceptance": ["output ok is true"],
            }
        ],
        risk=RiskLevel.READ,
        source_episode_ids=action_ids,
    )
    runtime.skills.add(skill)
    result = SkillExperimentRunner(
        runtime.db, runtime.ledger, runtime.skills, runtime.config
    ).run(skill.skill_id)

    assert result["status"] == "PASSED"
    assert result["candidate_cases"] == 3
    assert result["candidate_pass_rate"] == 1.0
    assert Path(result["artifact_path"], "manifest.json").exists()
    assert Path(result["artifact_path"], "result.json").exists()
    row = runtime.db.query_one(
        "SELECT status FROM skills WHERE skill_id=?", (skill.skill_id,)
    )
    assert row is not None and row["status"] == CandidateStatus.VALIDATED.value
    with pytest.raises(PermissionError):
        runtime.skills.transition(
            skill.skill_id,
            CandidateStatus.APPROVED,
            {"experiment_id": result["experiment_id"]},
        )


def test_skill_experiment_rejects_unlisted_execution_tool(runtime_factory):
    runtime = runtime_factory()
    skill = SkillDefinition(
        name="unsafe_command_candidate",
        description="A candidate whose tool is not allowed in isolated skill validation.",
        trigger_terms=["unsafe"],
        steps=[
            {
                "tool": "run_command",
                "arguments": {"command": ["python", "-c", "print(1)"]},
                "purpose": "must remain denied",
                "acceptance": ["output returncode equals 0"],
            }
        ],
        risk=RiskLevel.HIGH,
        source_episode_ids=[],
    )
    runtime.skills.add(skill)
    result = SkillExperimentRunner(
        runtime.db, runtime.ledger, runtime.skills, runtime.config
    ).run(skill.skill_id)
    assert result["status"] == "FAILED"
    assert result["candidate_pass_rate"] == 0.0
    row = runtime.db.query_one(
        "SELECT status FROM skills WHERE skill_id=?", (skill.skill_id,)
    )
    assert row is not None and row["status"] == CandidateStatus.SANDBOXED.value


def test_repeated_failure_recovery_improves_only_in_isolation(runtime_factory):
    runtime = runtime_factory()
    missing = runtime.config.home_path / "never-created.txt"
    for index in range(3):
        plan = Plan(
            rationale=f"reproduce missing file {index}",
            actions=[
                ActionSpec(
                    tool="read_file",
                    arguments={"path": str(missing)},
                    purpose="read a deliberately missing fixture",
                    expected_result="file content",
                    risk=RiskLevel.READ,
                    acceptance=["output contains path"],
                )
            ],
        )
        outcomes = _persist_and_execute(runtime, plan, f"failure_history_{index}")
        assert outcomes[0]["success"] is False

    candidate_ids = runtime.learning.create_failure_candidates(minimum_repeats=3)
    assert len(candidate_ids) == 1
    candidate_id = candidate_ids[0]
    result = FailureRecoveryEngine(
        runtime.db, runtime.ledger, runtime.config, runtime.learning
    ).run(candidate_id, "fixture_recovery")

    assert result["status"] == "PASSED"
    assert result["cases"] == 3
    assert result["baseline_pass_rate"] == 0.0
    assert result["candidate_pass_rate"] == 1.0
    assert result["regressions"] == 0
    row = runtime.db.query_one(
        "SELECT status FROM evolution_candidates WHERE candidate_id=?",
        (candidate_id,),
    )
    assert row is not None and row["status"] == CandidateStatus.VALIDATED.value
    assert not missing.exists(), "isolated recovery must not mutate the real runtime home"


def test_recovery_rejects_unsupported_strategy_and_insufficient_evidence(runtime_factory):
    runtime = runtime_factory()
    candidate_id = runtime.learning._upsert_candidate(
        candidate_type="failure_recovery",
        title="insufficient evidence candidate",
        proposal={"problem_signature": "read_file::missing"},
        source_ids=["missing_action"],
    )
    assert candidate_id is not None
    engine = FailureRecoveryEngine(
        runtime.db, runtime.ledger, runtime.config, runtime.learning
    )
    with pytest.raises(ValueError, match="unsupported recovery strategy"):
        engine.run(candidate_id, "invented_strategy")
    with pytest.raises(ValueError, match="insufficient repeated failures"):
        engine.run(candidate_id, "fixture_recovery")
