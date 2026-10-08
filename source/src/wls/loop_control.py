from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from math import isfinite
from time import monotonic


@dataclass(frozen=True, slots=True)
class LoopBudget:
    max_turns: int = 20
    max_seconds: float = 900.0
    max_cost_usd: float = 5.0
    max_failures: int = 4
    max_no_gain_rounds: int = 3

    def validate(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        if not isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("max_seconds must be > 0")
        if not isfinite(self.max_cost_usd) or self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be >= 0")
        if self.max_failures < 0:
            raise ValueError("max_failures must be >= 0")
        if self.max_no_gain_rounds < 1:
            raise ValueError("max_no_gain_rounds must be >= 1")


@dataclass(frozen=True, slots=True)
class LoopObservation:
    turn: int
    elapsed_seconds: float
    cumulative_cost_usd: float
    failures: int
    no_gain_rounds: int
    best_gain: float
    should_stop: bool
    stop_reason: str | None
    authority: str = "loop_decision_candidate_only"

    def to_dict(self) -> dict[str, float | int | str | bool | None]:
        return asdict(self)


class LoopController:
    """Pure loop-stop decision helper; it never mutates runtime state."""

    def __init__(
        self,
        budget: LoopBudget,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        budget.validate()
        self.budget = budget
        self._clock = clock
        self.started = clock()
        self.turn = 0
        self.failures = 0
        self.cost = 0.0
        self.no_gain_rounds = 0
        self.best_gain = float("-inf")
        self._owner_stop: str | None = None
        self._stopped_reason: str | None = None

    def request_owner_stop(self, reason: str) -> None:
        self._owner_stop = reason.strip() or "owner_stop"

    def stop_reason(self) -> str | None:
        """Check budgets without consuming a turn; once stopped, remain stopped."""
        if self._stopped_reason is None:
            elapsed = max(0.0, self._clock() - self.started)
            self._stopped_reason = self._reason(elapsed)
        return self._stopped_reason

    def observe(
        self,
        *,
        gain: float,
        cost_usd: float = 0.0,
        failed: bool = False,
    ) -> LoopObservation:
        if not isfinite(cost_usd) or cost_usd < 0:
            raise ValueError("cost_usd must be finite and >= 0")
        if not isfinite(gain):
            raise ValueError("gain must be finite")
        prior_stop = self.stop_reason()
        if prior_stop is not None:
            return LoopObservation(
                turn=self.turn,
                elapsed_seconds=max(0.0, self._clock() - self.started),
                cumulative_cost_usd=self.cost,
                failures=self.failures,
                no_gain_rounds=self.no_gain_rounds,
                best_gain=self.best_gain,
                should_stop=True,
                stop_reason=prior_stop,
            )
        self.turn += 1
        self.cost += cost_usd
        if failed:
            self.failures += 1
        if gain > self.best_gain:
            self.best_gain = gain
            self.no_gain_rounds = 0
        else:
            self.no_gain_rounds += 1
        elapsed = max(0.0, self._clock() - self.started)
        reason = self.stop_reason()
        return LoopObservation(
            turn=self.turn,
            elapsed_seconds=elapsed,
            cumulative_cost_usd=self.cost,
            failures=self.failures,
            no_gain_rounds=self.no_gain_rounds,
            best_gain=self.best_gain,
            should_stop=reason is not None,
            stop_reason=reason,
        )

    def _reason(self, elapsed: float) -> str | None:
        if self._owner_stop:
            return self._owner_stop
        if self.turn >= self.budget.max_turns:
            return "turn_budget_exhausted"
        if elapsed >= self.budget.max_seconds:
            return "time_budget_exhausted"
        if self.budget.max_cost_usd > 0 and self.cost >= self.budget.max_cost_usd:
            return "cost_budget_exhausted"
        if self.failures > self.budget.max_failures:
            return "failure_budget_exhausted"
        if self.no_gain_rounds >= self.budget.max_no_gain_rounds:
            return "no_incremental_gain"
        return None
