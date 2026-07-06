from __future__ import annotations

from wls.fault_injection import (
    FaultInjector,
    FaultInjection,
    FaultKind,
    FaultOutcome,
    FaultMatrix,
)


class TestFaultInjection:
    def test_no_handler_survives_false(self):
        injector = FaultInjector()
        injection = FaultInjection(
            injection_id="inj1",
            kind=FaultKind.PROVIDER_LOSS,
            target="planner",
        )
        outcome = injector.inject(injection)
        assert not outcome.survived
        assert outcome.recovery_action == "NO_HANDLER"
        assert outcome.state_corrupted

    def test_registered_handler_used(self):
        injector = FaultInjector()
        injector.register_handler(
            FaultKind.STALE_LEASE,
            lambda inj: FaultOutcome(
                outcome_id="out1", injection_id=inj.injection_id,
                kind=inj.kind, target=inj.target,
                survived=True, recovery_action="RECOVERED_LEASE",
                duration_seconds=0.5, state_corrupted=False,
                detail="lease recovered",
            ),
        )
        injection = FaultInjection(
            injection_id="inj1",
            kind=FaultKind.STALE_LEASE,
            target="worker-1",
        )
        outcome = injector.inject(injection)
        assert outcome.survived
        assert outcome.duration_seconds == 0.5

    def test_matrix_survival_rate(self):
        matrix = FaultMatrix(
            matrix_id="m1",
            outcomes=[
                FaultOutcome("o1", "i1", FaultKind.PROCESS_KILL, "w1", True, "RESTART", 1.0, False),
                FaultOutcome("o2", "i2", FaultKind.TIMEOUT, "w2", False, "NO_HANDLER", 5.0, True),
            ],
        )
        assert matrix.survival_rate() == 0.5
        assert matrix.corruptions() == 1

    def test_injector_summary(self):
        injector = FaultInjector()
        injector.register_handler(
            FaultKind.PROCESS_KILL,
            lambda inj: FaultOutcome(
                "o1", inj.injection_id, inj.kind, inj.target,
                True, "RESTARTED", 2.0, False,
            ),
        )
        injector.inject(FaultInjection("i1", FaultKind.PROCESS_KILL, "runtime"))
        summary = injector.summary()
        assert summary["total_faults"] == 1
        assert summary["survival_rate"] == 1.0

    def test_all_fault_kinds_exist(self):
        kinds = [
            FaultKind.PROVIDER_LOSS,
            FaultKind.PROCESS_KILL,
            FaultKind.STALE_LEASE,
            FaultKind.EVENT_STORM,
            FaultKind.DISK_PRESSURE,
            FaultKind.CORRUPT_MESSAGE,
            FaultKind.TIMEOUT,
            FaultKind.QUOTA_EXHAUSTION,
        ]
        assert len(kinds) == 8
