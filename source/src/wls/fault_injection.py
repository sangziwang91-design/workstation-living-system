from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from .schemas import new_id, utc_now


class FaultKind(StrEnum):
    PROVIDER_LOSS = "provider_loss"
    PROCESS_KILL = "process_kill"
    STALE_LEASE = "stale_lease"
    EVENT_STORM = "event_storm"
    DISK_PRESSURE = "disk_pressure"
    CORRUPT_MESSAGE = "corrupt_message"
    TIMEOUT = "timeout"
    QUOTA_EXHAUSTION = "quota_exhaustion"


@dataclass(slots=True)
class FaultInjection:
    injection_id: str
    kind: FaultKind
    target: str
    params: dict[str, Any] = field(default_factory=dict)
    injected_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class FaultOutcome:
    outcome_id: str
    injection_id: str
    kind: FaultKind
    target: str
    survived: bool
    recovery_action: str
    duration_seconds: float
    state_corrupted: bool
    detail: str = ""
    evidence_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "injection_id": self.injection_id,
            "kind": self.kind.value,
            "target": self.target,
            "survived": self.survived,
            "recovery_action": self.recovery_action,
            "duration_seconds": self.duration_seconds,
            "state_corrupted": self.state_corrupted,
            "detail": self.detail,
        }


@dataclass(slots=True)
class FaultMatrix:
    matrix_id: str
    outcomes: list[FaultOutcome] = field(default_factory=list)

    def survival_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        survived = sum(1 for o in self.outcomes if o.survived)
        return survived / len(self.outcomes)

    def corruptions(self) -> int:
        return sum(1 for o in self.outcomes if o.state_corrupted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matrix_id": self.matrix_id,
            "total_faults": len(self.outcomes),
            "survival_rate": self.survival_rate(),
            "corruptions": self.corruptions(),
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


class FaultInjector:
    """Inject faults to prove mission survival under provider loss, process
    kill, stale lease, event storm, disk pressure, and corrupt messages.
    """

    def __init__(self) -> None:
        self._handlers: dict[FaultKind, Callable[[FaultInjection], FaultOutcome]] = {}
        self._matrix = FaultMatrix(matrix_id=new_id("fault_matrix"))

    def register_handler(
        self,
        kind: FaultKind,
        handler: Callable[[FaultInjection], FaultOutcome],
    ) -> None:
        self._handlers[kind] = handler

    def inject(self, injection: FaultInjection) -> FaultOutcome:
        handler = self._handlers.get(injection.kind)
        if handler is None:
            outcome = FaultOutcome(
                outcome_id=new_id("fault_out"),
                injection_id=injection.injection_id,
                kind=injection.kind,
                target=injection.target,
                survived=False,
                recovery_action="NO_HANDLER",
                duration_seconds=0.0,
                state_corrupted=True,
                detail=f"no handler registered for {injection.kind.value}",
            )
            self._matrix.outcomes.append(outcome)
            return outcome

        outcome = handler(injection)
        self._matrix.outcomes.append(outcome)
        return outcome

    def summary(self) -> dict[str, Any]:
        return self._matrix.to_dict()
