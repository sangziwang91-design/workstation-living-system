from __future__ import annotations

from types import MethodType
from typing import Any

from .reliable_events import ReliableEventStore


def register_wls(runtime: Any) -> None:
    """Install retry scheduling on the canonical events table and ledger."""

    if getattr(runtime, "_task19_event_queue_installed", False):
        return
    runtime._task19_event_queue_installed = True
    runtime.events = ReliableEventStore(runtime.db, runtime.ledger)

    original_status = runtime.status

    def status(self: Any) -> dict[str, Any]:
        result = original_status()
        result["event_queue"] = self.events.reliability_status()
        return result

    runtime.status = MethodType(status, runtime)
    runtime.ledger.append(
        "task19_event_queue_installed",
        {
            "canonical_table": "events",
            "retry_policy": "deterministic_exponential_backoff",
            "dead_letter": True,
            "parallel_queue_authority": False,
        },
    )
