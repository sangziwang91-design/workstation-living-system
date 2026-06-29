from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from wls.examiner import AnchorBank, CandidateChange, Constitution, ExaminerStore, ExaminerSystem, ExaminerVersion, StandaloneDatabase, Verdict, current_implementation_digest
from wls.examiner.fixtures import RULE_SEQUENCE, hidden_holdout_cases, make_control, make_defect
from wls.examiner.system import IdFactory


ROOT = Path(__file__).resolve().parents[2]


def system(tmp_path: Path):
    constitution = Constitution.load(str(ROOT / ".evolution/examiner/constitution.json"))
    holdout = AnchorBank(hidden_holdout_cases(), "holdout", "test")
    version = ExaminerVersion(
        version_id="final",
        epoch_id="EPOCH-0010",
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=RULE_SEQUENCE,
        created_at="2026-06-28T00:00:00+00:00",
    )
    store = ExaminerStore(StandaloneDatabase(tmp_path / "adv.db"))
    return ExaminerSystem(constitution, store, version, id_factory=IdFactory("adv", deterministic=True)), holdout


def test_submission_nonce_replay_is_rejected(tmp_path: Path):
    examiner, _ = system(tmp_path)
    candidate = replace(
        make_defect("R006_EVIDENCE_LAUNDERING", 1).candidate,
        submission_nonce="same-nonce",
    )
    examiner.evaluate(candidate)
    with pytest.raises(RuntimeError, match="nonce replay"):
        examiner.evaluate(candidate)


def test_stale_epoch_score_is_blocked(tmp_path: Path):
    examiner, _ = system(tmp_path)
    result = examiner.evaluate(make_defect("R008_EPOCH_FRESHNESS", 1).candidate, record=False)
    assert result.verdict == Verdict.BLOCK


def test_protected_anchor_refactor_is_blocked(tmp_path: Path):
    examiner, _ = system(tmp_path)
    result = examiner.evaluate(make_defect("R009_PROTECTED_SURFACE", 1).candidate, record=False)
    assert result.verdict == Verdict.BLOCK


def test_correlated_judges_escalate_not_pass(tmp_path: Path):
    examiner, _ = system(tmp_path)
    result = examiner.evaluate(make_defect("R010_JUDGE_DIVERSITY", 1).candidate, record=False)
    assert result.verdict == Verdict.ESCALATE


def test_model_evidence_cannot_be_laundered(tmp_path: Path):
    examiner, _ = system(tmp_path)
    result = examiner.evaluate(make_defect("R006_EVIDENCE_LAUNDERING", 2).candidate, record=False)
    assert result.verdict == Verdict.BLOCK


def test_path_traversal_is_rejected_before_scope_matching(tmp_path: Path):
    examiner, _ = system(tmp_path)
    candidate = CandidateChange(
        candidate_id="path-traversal",
        title="path traversal",
        task_id="ADV",
        allowed_paths=("docs/**",),
        changed_paths=("../../docs/apparently-allowed.md",),
    )
    result = examiner.evaluate(candidate, record=False)
    assert result.verdict == Verdict.BLOCK
    assert "C003_CANONICAL_PATHS" in {item.rule_id for item in result.findings}


def test_runtime_ids_do_not_collide_after_restart(tmp_path: Path):
    constitution = Constitution.load(str(ROOT / ".evolution/examiner/constitution.json"))
    holdout = AnchorBank(hidden_holdout_cases(), "holdout", "test")
    version = ExaminerVersion(
        version_id="restart-safe",
        epoch_id="EPOCH-RESTART",
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=RULE_SEQUENCE,
        created_at="2026-06-28T00:00:00+00:00",
    )
    db = StandaloneDatabase(tmp_path / "restart.db")
    first = ExaminerSystem(constitution, ExaminerStore(db), version)
    first_result = first.evaluate(
        replace(make_control(801).candidate, candidate_id="first-restart")
    )
    second = ExaminerSystem(constitution, ExaminerStore(db), version)
    second_result = second.evaluate(
        replace(make_control(802).candidate, candidate_id="second-restart")
    )
    assert first_result.verdict_id != second_result.verdict_id


def test_version_identity_collision_is_rejected(tmp_path: Path):
    examiner, holdout = system(tmp_path)
    collision = ExaminerVersion(
        version_id=examiner.active_version.version_id,
        epoch_id="DIFFERENT-EPOCH",
        constitution_digest=examiner.constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=("R001_SCOPE_INTEGRITY",),
        created_at="2026-06-28T01:00:00+00:00",
    )
    with pytest.raises(RuntimeError, match="identity collision"):
        examiner.store.register_version(collision)


def test_forged_promotion_decision_fails_deterministic_replay(tmp_path: Path):
    examiner, holdout = system(tmp_path)
    partial = ExaminerVersion(
        version_id="partial",
        epoch_id="EPOCH-PARTIAL",
        constitution_digest=examiner.constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=("R001_SCOPE_INTEGRITY",),
        parent_version_id=examiner.active_version.version_id,
        created_at="2026-06-28T02:00:00+00:00",
    )
    real = examiner.compare_versions(examiner.active_version, partial, holdout)
    forged = replace(
        real,
        eligible=True,
        status="AWAITING_OWNER",
        reason_codes=(),
        wins=max(real.wins, 1),
        losses=0,
        one_sided_p=0.0,
    )
    promotion_id = examiner.request_promotion(forged, promotion_id="forged")
    with pytest.raises(RuntimeError, match="deterministic replay"):
        examiner.apply_promotion(
            partial,
            forged,
            owner_actor="owner",
            owner_approval_ref="fake",
            promotion_id=promotion_id,
            holdout=holdout,
        )


def test_duplicate_evidence_ids_and_short_verified_digest_are_rejected(tmp_path: Path):
    examiner, _ = system(tmp_path)
    candidate = CandidateChange.from_dict(
        {
            "candidate_id": "ambiguous-evidence",
            "task_id": "ADV",
            "allowed_paths": ["docs/**"],
            "changed_paths": ["docs/a.md"],
            "claims": [
                {
                    "text": "external result",
                    "level": "EXTERNAL_VERIFIED",
                    "claim_type": "external_effect",
                    "evidence_ids": ["same"],
                }
            ],
            "evidence": [
                {"source_id": "same", "verification": "UNKNOWN"},
                {
                    "source_id": "same",
                    "source_kind": "external_usage",
                    "verification": "EXTERNAL_VERIFIED",
                    "digest": "x",
                    "independent": True,
                },
            ],
        }
    )
    result = examiner.evaluate(candidate, record=False)
    assert result.verdict == Verdict.BLOCK
    ids = {item.rule_id for item in result.findings}
    assert "C004_EVIDENCE_ID_UNIQUENESS" in ids
    assert "C005_VERIFIED_DIGEST_FORMAT" in ids
