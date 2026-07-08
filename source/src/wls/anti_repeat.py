from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import new_id, utc_now


@dataclass(slots=True)
class FailureRecord:
    failure_id: str
    signature: str
    count: int
    last_failed_at: str
    cooldown_until: str | None = None
    suppressed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "signature": self.signature,
            "count": self.count,
            "last_failed_at": self.last_failed_at,
            "cooldown_until": self.cooldown_until,
            "suppressed": self.suppressed,
        }


class AntiRepeatGuard:
    """Prevents repeated identical failures from generating redundant
    recovery attempts. Uses signature-based deduplication with
    configurable cooldown windows and suppression.
    """

    def __init__(
        self,
        *,
        max_repeat_count: int = 3,
        base_cooldown_seconds: float = 300.0,
        max_cooldown_seconds: float = 3600.0,
    ) -> None:
        self.max_repeat_count = max_repeat_count
        self.base_cooldown_seconds = base_cooldown_seconds
        self.max_cooldown_seconds = max_cooldown_seconds
        self._records: dict[str, FailureRecord] = {}

    def record(self, signature: str) -> FailureRecord:
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        now_str = now.isoformat()

        existing = self._records.get(signature)
        if existing is None:
            record = FailureRecord(
                failure_id=new_id("failrep"),
                signature=signature,
                count=1,
                last_failed_at=now_str,
            )
            self._records[signature] = record
            return record

        existing.count += 1
        existing.last_failed_at = now_str

        if existing.count >= self.max_repeat_count:
            cooldown = min(
                self.base_cooldown_seconds * (2 ** (existing.count - self.max_repeat_count)),
                self.max_cooldown_seconds,
            )
            existing.cooldown_until = (now + timedelta(seconds=cooldown)).isoformat()
            existing.suppressed = True

        return existing

    def should_suppress(self, signature: str) -> bool:
        from datetime import UTC, datetime

        record = self._records.get(signature)
        if record is None:
            return False
        if not record.suppressed:
            return False
        if record.cooldown_until is None:
            return False

        now = datetime.now(UTC)
        cooldown = datetime.fromisoformat(record.cooldown_until)
        if now >= cooldown:
            record.suppressed = False
            record.cooldown_until = None
            return False
        return True

    def reset(self, signature: str) -> None:
        self._records.pop(signature, None)

    def active_suppressions(self) -> list[FailureRecord]:
        return [r for r in self._records.values() if r.suppressed]

    def to_summary(self) -> dict[str, Any]:
        return {
            "total_signatures": len(self._records),
            "suppressed": len(self.active_suppressions()),
            "records": [r.to_dict() for r in self._records.values()],
        }
