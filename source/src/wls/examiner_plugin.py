from __future__ import annotations

from pathlib import Path

from .examiner import AnchorBank, Constitution, ExaminerStore, ExaminerSystem, ExaminerVersion, current_implementation_digest


def register_wls(runtime) -> None:
    """Register the built-in examiner as an opt-in, advisory WLS organ.

    The plugin adds no second runtime, planner, memory, goal, policy, or action
    authority. It only attaches ``runtime.examiner`` and writes examiner-prefixed
    records to the canonical database/evidence ledger.
    """
    if hasattr(runtime, "examiner"):
        raise RuntimeError("WLS examiner already registered")

    defaults = Path(__file__).with_name("examiner") / "defaults"
    constitution = Constitution.load(str(defaults / "constitution.json"))
    anchors = AnchorBank.load_jsonl(
        defaults / "public_anchors.jsonl",
        bank_id="WLS-EXAMINER-PUBLIC-ANCHORS-V1",
        split="public",
    )
    anchors.assert_unique()
    version = ExaminerVersion(
        version_id="wls-examiner-v1-shadow",
        epoch_id="EXAM-EPOCH-0001",
        constitution_digest=constitution.digest,
        anchor_manifest_digest=anchors.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=(
            "R001_SCOPE_INTEGRITY",
            "R002_GATE_WEAKENING",
            "R003_CLAIM_CEILING",
            "R004_ARTIFACT_HYGIENE",
            "R005_AUTHORITY_UNIQUENESS",
            "R006_EVIDENCE_LAUNDERING",
            "R007_INCREMENTAL_VALUE",
            "R008_EPOCH_FRESHNESS",
            "R009_PROTECTED_SURFACE",
            "R010_JUDGE_DIVERSITY",
        ),
        parent_version_id=None,
        created_at="2026-06-28T15:32:06+08:00",
    )
    store = ExaminerStore(runtime.db, runtime.ledger, ensure_schema=False)
    runtime.examiner = ExaminerSystem(constitution, store, version)
    runtime.ledger.append(
        "examiner_registered",
        {
            "version_id": version.version_id,
            "version_digest": version.digest,
            "epoch_id": version.epoch_id,
            "mode": "SHADOW_ADVISORY",
        },
    )
