from __future__ import annotations

import json

import pytest
from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.rsi_artifact_gate import ArtifactIntegrityError, RsiArtifactGate
from wls.schemas import digest_json


SHA = "c" * 64


@pytest.fixture
def archive(tmp_path):
    db = Database(tmp_path / "state.sqlite")
    ledger = EvidenceLedger(db, tmp_path / "evidence.key")
    gate = RsiArtifactGate(
        tmp_path / "candidate-artifacts",
        ledger,
        allowed_files=frozenset({"agent/strategy.json", "agent/module.py"}),
        max_total_bytes=2048,
    )
    yield gate, ledger
    db.close_all()


def baseline(gate: RsiArtifactGate) -> dict:
    return gate.register(
        artifact_id="baseline", parent_id=None, generation=0, branch=0,
        files={"agent/strategy.json": b'{"mode":"base"}'},
        policy_digest=SHA, evaluator_digest=SHA,
    )


def test_candidate_archive_chain_is_provable_and_candidate_only(archive):
    gate, ledger = archive
    original = baseline(gate)
    assert gate.verify("baseline")["manifest_digest"] == original["manifest_digest"]
    child = gate.register(
        artifact_id="gen1-b0", parent_id="baseline", generation=1, branch=0,
        files={"agent/strategy.json": b'{"mode":"candidate"}'},
        policy_digest=SHA, evaluator_digest=SHA,
    )
    assert gate.verify("gen1-b0")["manifest_digest"] == child["manifest_digest"]
    assert child["authority"] == "candidate_only"
    assert ledger.verify()[0]


def test_artifact_file_tamper_is_detected(archive):
    gate, _ = archive
    baseline(gate)
    (gate.root / "baseline" / "agent" / "strategy.json").write_bytes(b"evil")
    with pytest.raises(ArtifactIntegrityError, match="source bytes modified"):
        gate.verify("baseline")


def test_manifest_tamper_is_detected(archive):
    gate, _ = archive
    baseline(gate)
    manifest = gate.root / "baseline" / "manifest.json"
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body["generation"] = 100
    manifest.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="manifest digest mismatch"):
        gate.verify("baseline")


def test_out_of_scope_evaluator_edit_rejected(archive):
    gate, _ = archive
    with pytest.raises(ValueError, match="non-allowlisted"):
        gate.register(
            artifact_id="bad", parent_id=None, generation=0, branch=0,
            files={"source/src/wls/evaluator.py": b"# bypass"},
            policy_digest=SHA, evaluator_digest=SHA,
        )


def test_artifact_identity_is_immutable(archive):
    gate, _ = archive
    baseline(gate)
    with pytest.raises(ValueError, match="already admitted"):
        baseline(gate)


def test_parent_tamper_blocks_inheritance(archive):
    gate, _ = archive
    baseline(gate)
    (gate.root / "baseline" / "agent" / "strategy.json").write_bytes(b"tamper")
    with pytest.raises(ArtifactIntegrityError):
        gate.register(
            artifact_id="child", parent_id="baseline", generation=1, branch=0,
            files={"agent/strategy.json": b"new"},
            policy_digest=SHA, evaluator_digest=SHA,
        )


def test_invalid_ids_and_file_budget_fail_closed(archive):
    gate, _ = archive
    for name in ("../escape", "C:bad", "folder/name", "trailing.", ""):
        with pytest.raises(ValueError, match="identifier"):
            gate.register(
                artifact_id=name, parent_id=None, generation=0, branch=0,
                files={"agent/module.py": b"code"},
                policy_digest=SHA, evaluator_digest=SHA,
            )
    with pytest.raises(ValueError, match="size cap"):
        gate.register(
            artifact_id="large", parent_id=None, generation=0, branch=0,
            files={"agent/module.py": b"!" * 2049},
            policy_digest=SHA, evaluator_digest=SHA,
        )


def test_added_file_is_detected_after_admission(archive):
    gate, _ = archive
    baseline(gate)
    (gate.root / "baseline" / "extra.py").write_text("bypass", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="inventory"):
        gate.verify("baseline")


def test_invalid_parent_and_digest_fail_closed(archive):
    gate, _ = archive
    with pytest.raises(ValueError, match="requires a parent"):
        gate.register(
            artifact_id="orphan", parent_id=None, generation=1, branch=0,
            files={"agent/module.py": b"code"},
            policy_digest=SHA, evaluator_digest=SHA,
        )
    with pytest.raises(ValueError, match="digest"):
        gate.register(
            artifact_id="bad-digest", parent_id=None, generation=0, branch=0,
            files={"agent/module.py": b"code"},
            policy_digest="not-a-digest", evaluator_digest=SHA,
        )


def test_resealed_manifest_cannot_override_canonical_evidence(archive):
    gate, ledger = archive
    baseline(gate)
    manifest = gate.root / "baseline" / "manifest.json"
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body["generation"] = 999
    body.pop("manifest_digest")
    body["manifest_digest"] = digest_json(body)
    manifest.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="signed WLS evidence"):
        gate.verify("baseline")
    assert ledger.verify()[0]
