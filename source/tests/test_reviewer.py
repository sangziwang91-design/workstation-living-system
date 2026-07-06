from __future__ import annotations

from wls.reviewer import (
    DisagreementRecord,
    HeterogeneousReviewer,
    ReviewOpinion,
    ReviewerDiversityPolicy,
)


def _make_opinion(rid: str, verdict: str, confidence: float = 0.8) -> ReviewOpinion:
    return ReviewOpinion(
        reviewer_id=rid,
        verdict=verdict,
        confidence=confidence,
        rationale=f"review by {rid}",
        evidence_ids=[],
    )


class TestDiversityPolicy:
    def test_diverse(self):
        policy = ReviewerDiversityPolicy()
        result = policy.check(["claude", "codex", "gpt"])
        assert result["diverse"]

    def test_duplicate_not_diverse(self):
        policy = ReviewerDiversityPolicy()
        result = policy.check(["claude", "claude"])
        assert not result["diverse"]
        assert result["same_provider_risk"]

    def test_single_reviewer(self):
        policy = ReviewerDiversityPolicy()
        result = policy.check(["claude"])
        assert not result["diverse"]


class TestHeterogeneousReviewer:
    def test_consensus(self):
        hr = HeterogeneousReviewer()
        hr.register("r1", lambda ctx: _make_opinion("r1", "PASS"))
        hr.register("r2", lambda ctx: _make_opinion("r2", "PASS"))
        record = hr.review("target-1", {})
        assert record.consensus_verdict == "PASS"
        assert not record.escalated

    def test_disagreement_escalates(self):
        hr = HeterogeneousReviewer()
        hr.register("r1", lambda ctx: _make_opinion("r1", "PASS"))
        hr.register("r2", lambda ctx: _make_opinion("r2", "FAIL"))
        record = hr.review("target-1", {})
        assert record.escalated
        assert record.consensus_verdict is None

    def test_deterministic_overrides(self):
        hr = HeterogeneousReviewer()
        hr.register("r1", lambda ctx: _make_opinion("r1", "PASS"))
        record = hr.review("target-1", {}, deterministic_verdict="FAIL")
        assert record.consensus_verdict == "FAIL"
        assert not record.escalated

    def test_reviewer_error_handled(self):
        hr = HeterogeneousReviewer()
        def _err(_ctx):
            raise RuntimeError("boom")
        hr.register("broken", _err)
        record = hr.review("target-1", {})
        assert any(o.verdict == "ERROR" for o in record.opinions)

    def test_advisory_review(self):
        hr = HeterogeneousReviewer()
        hr.register("r1", lambda ctx: _make_opinion("r1", "WARN", 0.5))
        record = hr.advisory_review("target-1", {})
        assert not record.escalated

    def test_disagreement_record_to_dict(self):
        record = DisagreementRecord(
            record_id="d1", target_id="t1",
            opinions=[_make_opinion("r1", "PASS")],
            disagreement_map={"PASS": ["r1"]},
            consensus_verdict="PASS", escalated=False,
        )
        d = record.to_dict()
        assert d["opinion_count"] == 1
