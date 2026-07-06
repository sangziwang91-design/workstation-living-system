from __future__ import annotations

import pytest

from wls.execution_trace import InMemoryTraceSink, TraceEmitter


def test_trace_digest_is_stable_for_payload_key_order() -> None:
    sink = InMemoryTraceSink()
    emitter = TraceEmitter(sink)

    first = emitter.emit(
        trace_id="trace:1",
        span_id="span:a",
        kind="tool",
        actor="wls.test",
        payload={"b": 2, "a": 1},
    )
    second = emitter.emit(
        trace_id="trace:1",
        span_id="span:b",
        kind="tool",
        actor="wls.test",
        payload={"a": 1, "b": 2},
    )

    assert first.payload_digest == second.payload_digest
    assert first.trace_digest != second.trace_digest
    assert len(sink.events) == 2


def test_trace_event_is_candidate_not_canonical_evidence() -> None:
    sink = InMemoryTraceSink()
    event = TraceEmitter(sink).emit(
        trace_id="trace:2",
        span_id="span:a",
        kind="action.receipt",
        actor="wls.test",
        payload={"result": "ok"},
        parent_span_id="root",
        timestamp="2026-07-04T00:00:00+00:00",
    )

    payload = event.to_dict()
    assert payload["authority"] == "trace_candidate_only"
    assert payload["canonical_evidence_owner"] == "wls.evidence.EvidenceLedger"
    assert sink.snapshot() == [payload]


def test_trace_emitter_rejects_missing_identity() -> None:
    sink = InMemoryTraceSink()

    with pytest.raises(ValueError, match="trace_id and span_id are required"):
        TraceEmitter(sink).emit(
            trace_id="",
            span_id="span:a",
            kind="tool",
            actor="wls.test",
            payload={},
        )
