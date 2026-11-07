from __future__ import annotations

from wls.result_promotion import PromotionDecision, ResultCandidate, ResultPromotionGate
from wls.schemas import digest_json
import pytest
import math


class TestResultPromotionGate:
    def test_valid_candidate_admitted(self):
        gate = ResultPromotionGate()
        candidate = ResultCandidate(
            candidate_id="c1",
            source_worker="worker-1",
            source_scope="project-1",
            content={"_delivery_ack": True, "key": "value"},
            content_digest=digest_json({"_delivery_ack": True, "key": "value"}),
            evidence_ids=["ev1"],
        )
        decision = gate.review(
            candidate,
            allowed_scopes=["project-1"],
            target_scope="project-1",
        )
        assert decision.admitted
        assert decision.admitted_to == "project-1"

    def test_wrong_scope_rejected(self):
        gate = ResultPromotionGate()
        candidate = ResultCandidate(
            candidate_id="c1",
            source_worker="w1",
            source_scope="project-2",
            content={"_delivery_ack": True},
            content_digest="abc",
            evidence_ids=["ev1"],
        )
        decision = gate.review(candidate, allowed_scopes=["project-1"])
        assert not decision.admitted

    def test_missing_worker_rejected(self):
        gate = ResultPromotionGate()
        candidate = ResultCandidate(
            candidate_id="c1",
            source_worker="",
            source_scope="p1",
            content={"_delivery_ack": True},
            content_digest="abc",
            evidence_ids=["ev1"],
        )
        decision = gate.review(candidate, allowed_scopes=["p1"], target_scope="p1")
        assert not decision.admitted

    def test_missing_evidence_rejected(self):
        gate = ResultPromotionGate()
        candidate = ResultCandidate(
            candidate_id="c1",
            source_worker="w1",
            source_scope="p1",
            content={"_delivery_ack": True},
            content_digest="abc",
            evidence_ids=[],
        )
        decision = gate.review(candidate, allowed_scopes=["p1"], target_scope="p1")
        assert not decision.admitted

    def test_contamination_rejected(self):
        gate = ResultPromotionGate()
        candidate = ResultCandidate(
            candidate_id="c1",
            source_worker="w1",
            source_scope="p1",
            content={"memory_id": "bad", "_delivery_ack": True},
            content_digest="abc",
            evidence_ids=["ev1"],
        )
        decision = gate.review(candidate, allowed_scopes=["p1"], target_scope="p1")
        assert not decision.admitted
        assert decision.contamination_check["memory_id_field_present"]

    def test_no_delivery_ack_rejected(self):
        gate = ResultPromotionGate()
        candidate = ResultCandidate(
            candidate_id="c1",
            source_worker="w1",
            source_scope="p1",
            content={"key": "val"},
            content_digest="abc",
            evidence_ids=["ev1"],
        )
        decision = gate.review(candidate, allowed_scopes=["p1"], target_scope="p1")
        assert not decision.admitted
        assert "delivery acknowledgement" in decision.reason

    def test_missing_content_digest_rejected(self):
        gate = ResultPromotionGate()
        candidate = ResultCandidate(
            candidate_id="c1",
            source_worker="w1",
            source_scope="p1",
            content={"_delivery_ack": True},
            content_digest="",
            evidence_ids=["ev1"],
        )
        decision = gate.review(candidate, allowed_scopes=["p1"], target_scope="p1")
        assert not decision.admitted

    def test_decision_to_dict(self):
        d = PromotionDecision("d1", "c1", True, "ok", "p1")
        dd = d.to_dict()
        assert dd["admitted"]
        assert dd["admitted_to"] == "p1"

def _candidate(**changes):
    data = {"_delivery_ack": True, "value": 1}
    base = dict(candidate_id="c1", source_worker="w1", source_scope="p1",
                content=data, content_digest=digest_json(data),
                evidence_ids=["ev1"])
    base.update(changes)
    return ResultCandidate(**base)


def _review(candidate, **kwargs):
    return ResultPromotionGate().review(
        candidate, allowed_scopes=["p1"], target_scope="p1", **kwargs,
    )


@pytest.mark.parametrize("name,changes,reason", [
    ("P01 blank id", {"candidate_id": ""}, "candidate identifier"),
    ("P02 traversal id", {"candidate_id": "../evil"}, "candidate identifier"),
    ("P03 blank worker", {"source_worker": "  "}, "source worker"),
    ("P04 blank scope", {"source_scope": ""}, "source scope"),
    ("P05 wrong source scope", {"source_scope": "other"}, "source scope"),
    ("P06 missing evidence", {"evidence_ids": []}, "evidence provenance"),
    ("P07 duplicate evidence", {"evidence_ids": ["ev1", "ev1"]}, "evidence provenance"),
    ("P08 blank evidence", {"evidence_ids": [" "]}, "evidence provenance"),
    ("P09 invalid evidence type", {"evidence_ids": "ev1"}, "evidence provenance"),
    ("P10 missing digest", {"content_digest": ""}, "content digest"),
    ("P11 fabricated digest", {"content_digest": "0" * 64}, "content digest"),
    ("P12 malformed digest", {"content_digest": "abc123"}, "content digest"),
    ("P13 string ack", {"content": {"_delivery_ack": "true"}}, "delivery acknowledgement"),
    ("P14 integer ack", {"content": {"_delivery_ack": 1}}, "delivery acknowledgement"),
    ("P15 canonical false", {"content": {"_delivery_ack": True, "_canonical_write": False}}, "canonical authority"),
    ("P16 forged promotion metadata", {"content": {"_delivery_ack": True, "_promotion_digest": "x"}}, "canonical authority"),
    ("P17 invalid content type", {"content": ["not", "object"]}, "JSON object"),
    ("P18 stale digest", {"content": {"_delivery_ack": True, "value": 2}}, "content digest"),
])
def test_eighteen_distinct_fail_closed_promotions(name, changes, reason):
    decision = _review(_candidate(**changes))
    assert not decision.admitted, name
    assert reason in decision.reason, (name, decision.reason)


def test_p19_empty_allowed_scopes_does_not_fall_back_to_target():
    decision = ResultPromotionGate().review(
        _candidate(), allowed_scopes=[], target_scope="p1",
    )
    assert not decision.admitted
    assert "allowed scopes" in decision.reason


def test_p20_target_cannot_silently_redirect_to_first_allowed_scope():
    decision = ResultPromotionGate().review(
        _candidate(), allowed_scopes=["p1"], target_scope="project",
    )
    assert not decision.admitted
    assert "target scope" in decision.reason


def test_p21_review_is_pure_and_metadata_is_separate():
    candidate = _candidate()
    original = dict(candidate.content)
    first = _review(candidate)
    second = _review(candidate)
    assert first.admitted and second.admitted
    assert candidate.content == original
    assert first.promotion_metadata["_promotion_digest"] == candidate.content_digest
    assert first.to_dict()["promotion_metadata"] == first.promotion_metadata


def test_p22_nonfinite_json_and_nonstring_keys_are_rejected():
    for data in ({"_delivery_ack": True, "value": math.nan},
                 {"_delivery_ack": True, 1: "value"}):
        decision = _review(_candidate(content=data))
        assert not decision.admitted


def test_p23_oversized_payload_is_rejected():
    data = {"_delivery_ack": True, "value": "x" * 1_000_001}
    decision = _review(_candidate(content=data, content_digest=digest_json(data)))
    assert not decision.admitted
    assert "size" in decision.reason


def test_p24_duplicate_and_malformed_allowed_scopes_are_rejected():
    for scopes in (["p1", "p1"], ["p1", " "], "p1"):
        decision = ResultPromotionGate().review(
            _candidate(), allowed_scopes=scopes, target_scope="p1",
        )
        assert not decision.admitted
