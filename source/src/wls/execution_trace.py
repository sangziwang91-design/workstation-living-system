from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .schemas import digest_json, utc_now


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """A candidate execution trace event, not canonical evidence."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    kind: str
    actor: str
    timestamp: str
    payload_digest: str
    payload: dict[str, Any]
    trace_digest: str
    authority: str = "trace_candidate_only"
    canonical_evidence_owner: str = "wls.evidence.EvidenceLedger"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TraceSink(Protocol):
    def append(self, event: TraceEvent) -> None: ...


class InMemoryTraceSink:
    """Volatile sink for tests and candidate traces."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def append(self, event: TraceEvent) -> None:
        self.events.append(event)

    def snapshot(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]


class TraceEmitter:
    """Emits trace candidates while preserving EvidenceLedger authority."""

    def __init__(self, sink: TraceSink) -> None:
        self.sink = sink

    def emit(
        self,
        *,
        trace_id: str,
        span_id: str,
        kind: str,
        actor: str,
        payload: dict[str, Any],
        parent_span_id: str | None = None,
        timestamp: str | None = None,
    ) -> TraceEvent:
        if not trace_id.strip() or not span_id.strip():
            raise ValueError("trace_id and span_id are required")
        if not kind.strip() or not actor.strip():
            raise ValueError("kind and actor are required")
        payload_digest = digest_json(payload)
        event = TraceEvent(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            kind=kind,
            actor=actor,
            timestamp=timestamp or utc_now(),
            payload_digest=payload_digest,
            payload=payload,
            trace_digest=digest_json(
                {
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "parent_span_id": parent_span_id,
                    "kind": kind,
                    "actor": actor,
                    "payload_digest": payload_digest,
                    "authority": "trace_candidate_only",
                    "canonical_evidence_owner": "wls.evidence.EvidenceLedger",
                }
            ),
        )
        self.sink.append(event)
        return event
