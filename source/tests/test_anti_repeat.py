from __future__ import annotations

from wls.anti_repeat import AntiRepeatGuard


class TestAntiRepeatGuard:
    def test_first_failure_not_suppressed(self):
        guard = AntiRepeatGuard(max_repeat_count=3)
        guard.record("sig-1")
        assert not guard.should_suppress("sig-1")

    def test_third_failure_suppressed(self):
        guard = AntiRepeatGuard(max_repeat_count=3)
        guard.record("sig-1")
        guard.record("sig-1")
        guard.record("sig-1")
        assert guard.should_suppress("sig-1")

    def test_different_signatures_independent(self):
        guard = AntiRepeatGuard(max_repeat_count=2)
        guard.record("sig-a")
        guard.record("sig-a")
        assert guard.should_suppress("sig-a")
        assert not guard.should_suppress("sig-b")

    def test_reset_clears_suppression(self):
        guard = AntiRepeatGuard(max_repeat_count=2)
        guard.record("sig-1")
        guard.record("sig-1")
        assert guard.should_suppress("sig-1")
        guard.reset("sig-1")
        assert not guard.should_suppress("sig-1")

    def test_cooldown_expiry(self):
        guard = AntiRepeatGuard(
            max_repeat_count=2,
            base_cooldown_seconds=0.001,
            max_cooldown_seconds=0.01,
        )
        guard.record("sig-1")
        guard.record("sig-1")
        assert guard.should_suppress("sig-1")

    def test_active_suppressions(self):
        guard = AntiRepeatGuard(max_repeat_count=2)
        guard.record("sig-1")
        guard.record("sig-1")
        guard.record("sig-2")
        assert len(guard.active_suppressions()) == 1

    def test_to_summary(self):
        guard = AntiRepeatGuard(max_repeat_count=2)
        guard.record("sig-1")
        guard.record("sig-1")
        summary = guard.to_summary()
        assert summary["total_signatures"] == 1
        assert summary["suppressed"] == 1

    def test_count_increments(self):
        guard = AntiRepeatGuard(max_repeat_count=10)
        r1 = guard.record("s")
        assert r1.count == 1
        import time
        time.sleep(0.01)
        r2 = guard.record("s")
        assert r2.count == 2

    def test_unknown_signature_not_suppressed(self):
        guard = AntiRepeatGuard()
        assert not guard.should_suppress("never-seen")
