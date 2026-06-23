from __future__ import annotations

import json

import pytest

from wls.schemas import CandidateStatus, RiskLevel, SkillDefinition, digest_json, utc_now


def test_skill_machine_stage_cannot_bypass_persisted_experiment(runtime_factory):
    runtime = runtime_factory()
    skill = SkillDefinition(
        name="gate_requires_experiment",
        description="A declarative candidate used to test lifecycle authority.",
        trigger_terms=["gate"],
        steps=[{"tool": "noop", "arguments": {}, "purpose": "gate test"}],
        risk=RiskLevel.READ,
    )
    runtime.skills.add(skill)
    with pytest.raises(ValueError, match="persisted skill experiment"):
        runtime.skills.transition(
            skill.skill_id,
            CandidateStatus.SANDBOXED,
            {"claimed": "tests passed"},
        )
    row = runtime.db.query_one(
        "SELECT status FROM skills WHERE skill_id=?", (skill.skill_id,)
    )
    assert row is not None and row["status"] == CandidateStatus.PROPOSED.value


def test_skill_gate_rejects_manifest_digest_mismatch(runtime_factory):
    runtime = runtime_factory()
    skill = SkillDefinition(
        name="gate_digest_mismatch",
        description="A candidate whose persisted manifest must be bound by digest.",
        trigger_terms=["digest"],
        steps=[{"tool": "noop", "arguments": {}, "purpose": "digest test"}],
        risk=RiskLevel.READ,
    )
    runtime.skills.add(skill)
    manifest = {"skill_id": skill.skill_id, "cases": [1, 2, 3]}
    runtime.db.execute(
        """
        INSERT INTO skill_experiments(
            experiment_id,skill_id,skill_version,status,manifest_json,baseline_json,
            result_json,artifact_path,started_at,finished_at
        ) VALUES (?,?,?,?,?,?,?,?,?,NULL)
        """,
        (
            "skill_exp_fake",
            skill.skill_id,
            1,
            "RUNNING",
            json.dumps(manifest, sort_keys=True),
            "{}",
            None,
            str(runtime.config.sandbox_path / "fake"),
            utc_now(),
        ),
    )
    with pytest.raises(ValueError, match="manifest digest mismatch"):
        runtime.skills.transition(
            skill.skill_id,
            CandidateStatus.SANDBOXED,
            {
                "experiment_id": "skill_exp_fake",
                "manifest_sha256": digest_json({"tampered": True}),
            },
        )


def test_recovery_machine_stage_cannot_bypass_persisted_experiment(runtime_factory):
    runtime = runtime_factory()
    candidate_id = runtime.learning._upsert_candidate(
        candidate_type="failure_recovery",
        title="evidence gate candidate",
        proposal={"problem_signature": "read_file::missing"},
        source_ids=["a", "b", "c"],
    )
    assert candidate_id is not None
    with pytest.raises(ValueError, match="persisted recovery experiment"):
        runtime.learning.transition_candidate(
            candidate_id,
            CandidateStatus.SANDBOXED,
            {"claimed": "isolated"},
        )
    row = runtime.db.query_one(
        "SELECT status FROM evolution_candidates WHERE candidate_id=?",
        (candidate_id,),
    )
    assert row is not None and row["status"] == CandidateStatus.PROPOSED.value


def test_non_recovery_candidate_keeps_generic_lifecycle(runtime_factory):
    runtime = runtime_factory()
    candidate_id = runtime.learning._upsert_candidate(
        candidate_type="world_model_revision",
        title="non-recovery candidate",
        proposal={"prediction_key": "x::y"},
        source_ids=["prediction_1"],
    )
    assert candidate_id is not None
    runtime.learning.transition_candidate(
        candidate_id,
        CandidateStatus.SANDBOXED,
        {"sandbox": "bounded"},
    )
    row = runtime.db.query_one(
        "SELECT status FROM evolution_candidates WHERE candidate_id=?",
        (candidate_id,),
    )
    assert row is not None and row["status"] == CandidateStatus.SANDBOXED.value
