from __future__ import annotations

import pytest

from wls.loop_control import LoopBudget, LoopController


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_stops_after_no_gain_budget() -> None:
    loop = LoopController(LoopBudget(max_turns=20, max_no_gain_rounds=3))

    assert not loop.observe(gain=1).should_stop
    assert not loop.observe(gain=1).should_stop
    assert not loop.observe(gain=1).should_stop
    final = loop.observe(gain=1)

    assert final.should_stop
    assert final.stop_reason == "no_incremental_gain"
    assert final.authority == "loop_decision_candidate_only"


def test_owner_stop_preempts_other_budgets() -> None:
    loop = LoopController(LoopBudget(max_turns=1))

    loop.request_owner_stop("owner_pause")
    observed = loop.observe(gain=1)

    assert observed.stop_reason == "owner_pause"


def test_time_and_cost_budgets_are_deterministic() -> None:
    clock = FakeClock()
    loop = LoopController(LoopBudget(max_seconds=5, max_cost_usd=2), clock=clock)

    first = loop.observe(gain=1, cost_usd=1)
    clock.advance(6)
    second = loop.observe(gain=2, cost_usd=0.5)

    assert not first.should_stop
    assert second.should_stop
    assert second.stop_reason == "time_budget_exhausted"
    assert second.elapsed_seconds == 6


def test_failure_budget_stops_after_excess_failures() -> None:
    loop = LoopController(LoopBudget(max_failures=1, max_no_gain_rounds=10))

    assert not loop.observe(gain=1, failed=True).should_stop
    observed = loop.observe(gain=2, failed=True)

    assert observed.should_stop
    assert observed.stop_reason == "failure_budget_exhausted"


def test_invalid_budget_and_negative_cost_are_rejected() -> None:
    with pytest.raises(ValueError, match="max_turns"):
        LoopBudget(max_turns=0).validate()

    loop = LoopController(LoopBudget())
    with pytest.raises(ValueError, match="cost_usd"):
        loop.observe(gain=1, cost_usd=-1)
