from __future__ import annotations

import pytest

from wls.experiment_decision import (
    ExperimentPolicy,
    ExperimentVerdict,
    MetricResult,
    decide_experiment,
)


DIGEST = "a" * 64
POLICY = ExperimentPolicy(
    direction="maximize",
    minimum_gain=0.03,
    hard_gates={"critical_regressions": 0, "policy_violations": 0},
    max_rounds=20,
    max_failures=5,
    evaluator_digest=DIGEST,
)


def result(
    primary: float,
    gates: dict[str, float] | None = None,
    digest: str = DIGEST,
    crashed: bool = False,
) -> MetricResult:
    return MetricResult(
        primary=primary,
        gates=gates or {"critical_regressions": 0, "policy_violations": 0},
        evaluator_digest=digest,
        crashed=crashed,
    )


def test_keep_and_revert_with_policy_digest() -> None:
    keep = decide_experiment(
        POLICY,
        baseline=result(0.50),
        candidate=result(0.54),
        completed_rounds=1,
        failures=0,
    )
    revert = decide_experiment(
        POLICY,
        baseline=result(0.50),
        candidate=result(0.51),
        completed_rounds=1,
        failures=0,
    )

    assert keep.verdict is ExperimentVerdict.KEEP
    assert keep.gain == pytest.approx(0.04)
    assert keep.authority == "experiment_decision_candidate_only"
    assert keep.canonical_owner == "evolution"
    assert keep.policy_digest
    assert revert.verdict is ExperimentVerdict.REVERT


def test_hard_gate_and_digest_tamper() -> None:
    bad_gate = result(0.9, {"critical_regressions": 1, "policy_violations": 0})

    assert decide_experiment(
        POLICY,
        baseline=result(0.5),
        candidate=bad_gate,
        completed_rounds=1,
        failures=0,
    ).verdict is ExperimentVerdict.REVERT
    invalid = decide_experiment(
        POLICY,
        baseline=result(0.5),
        candidate=result(0.9, digest="b" * 64),
        completed_rounds=1,
        failures=0,
    )
    assert invalid.verdict is ExperimentVerdict.INVALID
    assert invalid.reasons == ("candidate_evaluator_digest_mismatch",)


def test_crash_and_budget_stop() -> None:
    assert decide_experiment(
        POLICY,
        baseline=result(0.5),
        candidate=result(0.0, crashed=True),
        completed_rounds=1,
        failures=0,
    ).verdict is ExperimentVerdict.CRASH
    assert decide_experiment(
        POLICY,
        baseline=result(0.5),
        candidate=result(0.9),
        completed_rounds=20,
        failures=0,
    ).verdict is ExperimentVerdict.STOP_BUDGET


def test_minimize_direction_and_invalid_policy() -> None:
    policy = ExperimentPolicy(
        direction="minimize",
        minimum_gain=0.2,
        hard_gates={},
        max_rounds=5,
        max_failures=0,
        evaluator_digest=DIGEST,
    )
    decision = decide_experiment(
        policy,
        baseline=result(1.0, {}),
        candidate=result(0.7, {}),
        completed_rounds=1,
        failures=0,
    )

    assert decision.verdict is ExperimentVerdict.KEEP
    with pytest.raises(ValueError, match="direction"):
        ExperimentPolicy("sideways", 0.0, {}, 1, 0, DIGEST)
