from __future__ import annotations

from wls.result_promotion import PromotionDecision, ResultCandidate, ResultPromotionGate


class TestResultPromotionGate:
    def test_valid_candidate_admitted(self):
        gate = ResultPromotionGate()
        candidate = ResultCandidate(
            candidate_id="c1",
            source_worker="worker-1",
            source_scope="project-1",
            content={"_delivery_ack": True, "key": "value"},
            content_digest="abc123",
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
        decision = gate.review(candidate, allowed_scopes=["p1"])
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
        decision = gate.review(candidate, allowed_scopes=["p1"])
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
        decision = gate.review(candidate, allowed_scopes=["p1"])
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
        decision = gate.review(candidate, allowed_scopes=["p1"])
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
        decision = gate.review(candidate, allowed_scopes=["p1"])
        assert not decision.admitted

    def test_decision_to_dict(self):
        d = PromotionDecision("d1", "c1", True, "ok", "p1")
        dd = d.to_dict()
        assert dd["admitted"]
        assert dd["admitted_to"] == "p1"
