from __future__ import annotations

import math

import pytest

from wls.coding_workers import CodexWorker
from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.experiment_decision import (
    ExperimentPolicy, ExperimentVerdict, MetricResult, decide_experiment,
)
from wls.loop_control import LoopBudget, LoopController
from wls.rsi_evolution import RsiEvolutionPilot


DIGEST = "a" * 64


def policy(max_rounds: int = 3) -> ExperimentPolicy:
    return ExperimentPolicy(
        direction="maximize", minimum_gain=0.01,
        hard_gates={"critical_regressions": 0.0},
        max_rounds=max_rounds, max_failures=1,
        evaluator_digest=DIGEST,
    )


def metric(score: float, regressions: float = 0, digest: str = DIGEST) -> MetricResult:
    return MetricResult(
        primary=score, gates={"critical_regressions": regressions},
        evaluator_digest=digest,
    )


@pytest.fixture
def pilot(tmp_path):
    db = Database(tmp_path / "wls.sqlite")
    ledger = EvidenceLedger(db, tmp_path / "evidence.key")
    yield RsiEvolutionPilot(db, ledger), ledger
    db.close_all()


def test_three_generations_two_branches_persisted_and_evidence_verified(pilot):
    runner, ledger = pilot
    runner.start(
        run_id="rsi-001", baseline_id="base", baseline=metric(0.50),
        policy=policy(),
    )
    proposed: list[tuple[str, int, int]] = []

    def propose(parent: str, gen: int, branch: int) -> str:
        proposed.append((parent, gen, branch))
        return f"g{gen}b{branch}"

    def evaluate(candidate: str) -> MetricResult:
        gen = int(candidate[1])
        branch = int(candidate[-1])
        return metric(0.5 + gen * 0.1 + branch * 0.02)

    done = runner.run_bounded("rsi-001", policy=policy(), propose=propose, evaluate=evaluate)
    assert done["status"] == "COMPLETE"
    assert done["generation"] == 3
    assert done["champion_id"] == "g3b1"
    assert len(done["history"]) == 3
    assert len(proposed) == 6
    assert proposed[2][0] == "g1b1"
    assert runner.read("rsi-001")["champion_id"] == "g3b1"
    ok, details = ledger.verify()
    assert ok, details
    assert details["records"] == 13
    with pytest.raises(ValueError, match="run_id already exists"):
        runner.start(run_id="rsi-001", policy=policy(), baseline_id="base", baseline=metric(0.5))


def test_crash_is_blocked_not_automatically_replayed(pilot):
    runner, ledger = pilot
    runner.start(run_id="blocked", policy=policy(), baseline_id="base", baseline=metric(.5))
    count = 0

    def crash(parent: str, gen: int, branch: int) -> str:
        nonlocal count
        count += 1
        raise RuntimeError("test interruption")

    with pytest.raises(RuntimeError):
        runner.advance("blocked", policy=policy(), propose=crash, evaluate=lambda _: metric(.6))
    assert runner.read("blocked")["status"] == "BLOCKED"
    with pytest.raises(ValueError, match="not ready"):
        runner.advance("blocked", policy=policy(), propose=crash, evaluate=lambda _: metric(.6))
    assert count == 1
    assert ledger.verify()[0]


def test_modified_policy_is_rejected_without_generating_candidate(pilot):
    runner, _ = pilot
    runner.start(run_id="contract", policy=policy(), baseline_id="base", baseline=metric(.5))
    with pytest.raises(ValueError, match="policy changed"):
        runner.advance(
            "contract", policy=policy(4),
            propose=lambda *_: pytest.fail("must not call proposer"),
            evaluate=lambda _: metric(.6),
        )
    assert runner.read("contract")["status"] == "READY"


def test_bad_candidate_metrics_never_promote(pilot):
    runner, ledger = pilot
    runner.start(run_id="invalid", policy=policy(1), baseline_id="base", baseline=metric(.5))
    out = runner.run_bounded(
        "invalid", policy=policy(1),
        propose=lambda _, gen, branch: f"c{gen}-{branch}",
        evaluate=lambda _: metric(math.nan),
    )
    assert out["champion_id"] == "base"
    assert out["generation"] == 1
    assert out["status"] == "COMPLETE"
    assert ledger.verify()[0]


def test_hard_gate_and_zero_gain_fail_closed():
    p = ExperimentPolicy("maximize", 0.0, {"critical_regressions": 0}, 3, 1, DIGEST)
    result = decide_experiment(
        p, baseline=metric(.5), candidate=metric(.5),
        completed_rounds=0, failures=0,
    )
    assert result.verdict == ExperimentVerdict.REVERT
    result = decide_experiment(
        p, baseline=metric(.5), candidate=metric(.9, regressions=math.inf),
        completed_rounds=0, failures=0,
    )
    assert result.verdict == ExperimentVerdict.INVALID
    with pytest.raises(ValueError, match="minimum_gain"):
        ExperimentPolicy("maximize", math.nan, {}, 3, 1, DIGEST)


def test_budget_stops_are_sticky_and_inputs_must_be_finite():
    loop = LoopController(LoopBudget(max_turns=1))
    with pytest.raises(ValueError, match="gain"):
        loop.observe(gain=math.nan)
    with pytest.raises(ValueError, match="cost"):
        loop.observe(gain=1, cost_usd=math.inf)
    first = loop.observe(gain=1)
    assert first.should_stop
    second = loop.observe(gain=2)
    assert second.turn == first.turn
    assert second.stop_reason == first.stop_reason


def test_coding_worker_does_not_fabricate_test_success():
    worker = CodexWorker()
    assert not worker._parse_test_results("I think everything is ok", "")
    assert not worker._parse_test_results("3 passed", "1 failed")
    assert worker._parse_test_results("3 passed in 0.1s", "")
