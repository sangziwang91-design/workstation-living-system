from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from math import isfinite
from typing import Mapping

from .schemas import digest_json


class ExperimentVerdict(StrEnum):
    KEEP = "KEEP"
    REVERT = "REVERT"
    CRASH = "CRASH"
    INVALID = "INVALID"
    STOP_BUDGET = "STOP_BUDGET"


@dataclass(frozen=True, slots=True)
class ExperimentPolicy:
    direction: str
    minimum_gain: float
    hard_gates: Mapping[str, float]
    max_rounds: int
    max_failures: int
    evaluator_digest: str

    def __post_init__(self) -> None:
        if self.direction not in {"maximize", "minimize"}:
            raise ValueError("direction must be maximize or minimize")
        if not isfinite(self.minimum_gain) or self.minimum_gain < 0:
            raise ValueError("minimum_gain must be non-negative")
        if any(not isfinite(float(value)) for value in self.hard_gates.values()):
            raise ValueError("hard gates must be finite")
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        if self.max_failures < 0:
            raise ValueError("max_failures must be >= 0")
        if len(self.evaluator_digest) != 64:
            raise ValueError("evaluator_digest must be SHA-256 hex")
        int(self.evaluator_digest, 16)

    def digest(self) -> str:
        return digest_json(asdict(self))


@dataclass(frozen=True, slots=True)
class MetricResult:
    primary: float
    gates: Mapping[str, float]
    evaluator_digest: str
    crashed: bool = False


@dataclass(frozen=True, slots=True)
class ExperimentDecision:
    verdict: ExperimentVerdict
    gain: float | None
    reasons: tuple[str, ...]
    policy_digest: str
    authority: str = "experiment_decision_candidate_only"
    canonical_owner: str = "evolution"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["verdict"] = self.verdict.value
        return data


def decide_experiment(
    policy: ExperimentPolicy,
    *,
    baseline: MetricResult,
    candidate: MetricResult,
    completed_rounds: int,
    failures: int,
) -> ExperimentDecision:
    reasons: list[str] = []
    policy_digest = policy.digest()
    if completed_rounds >= policy.max_rounds:
        reasons.append("round_budget_exhausted")
        return ExperimentDecision(ExperimentVerdict.STOP_BUDGET, None, tuple(reasons), policy_digest)
    if failures > policy.max_failures:
        reasons.append("failure_budget_exhausted")
        return ExperimentDecision(ExperimentVerdict.STOP_BUDGET, None, tuple(reasons), policy_digest)
    if completed_rounds < 0 or failures < 0:
        reasons.append("invalid_budget_counters")
        return ExperimentDecision(ExperimentVerdict.INVALID, None, tuple(reasons), policy_digest)
    if candidate.crashed:
        reasons.append("candidate_crashed")
        return ExperimentDecision(ExperimentVerdict.CRASH, None, tuple(reasons), policy_digest)
    if baseline.evaluator_digest != policy.evaluator_digest:
        reasons.append("baseline_evaluator_digest_mismatch")
        return ExperimentDecision(ExperimentVerdict.INVALID, None, tuple(reasons), policy_digest)
    if candidate.evaluator_digest != policy.evaluator_digest:
        reasons.append("candidate_evaluator_digest_mismatch")
        return ExperimentDecision(ExperimentVerdict.INVALID, None, tuple(reasons), policy_digest)

    if not isfinite(baseline.primary) or not isfinite(candidate.primary):
        reasons.append("nonfinite_primary_metric")
        return ExperimentDecision(ExperimentVerdict.INVALID, None, tuple(reasons), policy_digest)
    for name, ceiling in sorted(policy.hard_gates.items()):
        if name not in candidate.gates or name not in baseline.gates:
            reasons.append(f"missing_gate:{name}")
            return ExperimentDecision(ExperimentVerdict.INVALID, None, tuple(reasons), policy_digest)
        value = float(candidate.gates[name])
        if not isfinite(value) or not isfinite(float(baseline.gates[name])):
            reasons.append(f"nonfinite_gate:{name}")
            return ExperimentDecision(ExperimentVerdict.INVALID, None, tuple(reasons), policy_digest)
        if value > float(ceiling):
            reasons.append(f"hard_gate_failed:{name}")
            return ExperimentDecision(ExperimentVerdict.REVERT, None, tuple(reasons), policy_digest)

    if policy.direction == "maximize":
        gain = candidate.primary - baseline.primary
    else:
        gain = baseline.primary - candidate.primary
    if isfinite(gain) and gain > 0 and gain >= policy.minimum_gain:
        reasons.append("minimum_gain_met")
        return ExperimentDecision(ExperimentVerdict.KEEP, gain, tuple(reasons), policy_digest)
    reasons.append("minimum_gain_not_met")
    return ExperimentDecision(ExperimentVerdict.REVERT, gain, tuple(reasons), policy_digest)
