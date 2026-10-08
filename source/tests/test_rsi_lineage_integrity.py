"""RSI descendants must remain verifiably descended from their measured parents.

These cases fail against the previous archive implementation, which checked
only the selected child's bytes and accepted generation/policy mismatches.
"""
from __future__ import annotations

import pytest

from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.rsi_artifact_gate import ArtifactIntegrityError, RsiArtifactGate

POLICY = "a" * 64
EVALUATOR = "b" * 64


@pytest.fixture
def archive(tmp_path):
    db = Database(tmp_path / "state.sqlite")
    ledger = EvidenceLedger(db, tmp_path / "evidence.key")
    gate = RsiArtifactGate(
        tmp_path / "artifacts", ledger,
        allowed_files=frozenset({"agent/code.py"}),
    )
    yield gate, ledger
    db.close_all()


def add(gate, name, parent=None, generation=0, policy=POLICY, evaluator=EVALUATOR):
    return gate.register(
        artifact_id=name, parent_id=parent, generation=generation, branch=0,
        files={"agent/code.py": f"version = {generation}\n".encode("utf-8")},
        policy_digest=policy, evaluator_digest=evaluator,
    )


def test_three_generations_have_verifiable_ancestry(archive):
    gate, ledger = archive
    add(gate, "seed")
    add(gate, "g1", "seed", 1)
    leaf = add(gate, "g2", "g1", 2)
    assert gate.verify("g2")["manifest_digest"] == leaf["manifest_digest"]
    assert ledger.verify()[0]


def test_child_rejects_skipped_generation_before_admission(archive):
    gate, _ = archive
    add(gate, "seed")
    with pytest.raises(ValueError, match="generation"):
        add(gate, "invalid", "seed", 3)
    assert not (gate.root / "invalid").exists()


@pytest.mark.parametrize("which", ["policy", "evaluator"])
def test_child_cannot_switch_the_immutable_measurement_contract(archive, which):
    gate, _ = archive
    add(gate, "seed")
    values = {"policy": POLICY, "evaluator": EVALUATOR}
    values[which] = "f" * 64
    with pytest.raises(ValueError, match="policy or evaluator"):
        add(gate, "invalid", "seed", 1, **values)
    assert not (gate.root / "invalid").exists()


def test_zero_generation_cannot_claim_an_existing_parent(archive):
    gate, _ = archive
    add(gate, "seed")
    with pytest.raises(ValueError, match="baseline"):
        add(gate, "fake-seed", "seed", 0)


def test_child_becomes_invalid_when_ancestor_source_is_corrupted(archive):
    gate, _ = archive
    add(gate, "seed")
    add(gate, "g1", "seed", 1)
    add(gate, "g2", "g1", 2)
    (gate.root / "seed" / "agent" / "code.py").write_text(
        "version = 'tampered'\n", encoding="utf-8",
    )
    with pytest.raises(ArtifactIntegrityError, match="source bytes modified"):
        gate.verify("g2")


def test_child_becomes_invalid_when_parent_disappears(archive):
    gate, _ = archive
    add(gate, "seed")
    add(gate, "g1", "seed", 1)
    (gate.root / "seed" / "manifest.json").unlink()
    with pytest.raises(ArtifactIntegrityError, match="manifest missing"):
        gate.verify("g1")


def test_deep_ancestry_checks_ledger_once_per_verification(archive, monkeypatch):
    gate, ledger = archive
    add(gate, "seed")
    for generation in range(1, 32):
        parent = "seed" if generation == 1 else f"g{generation-1}"
        add(gate, f"g{generation}", parent, generation)
    original = ledger.verify
    calls = []

    def counting_verify():
        calls.append(True)
        return original()

    monkeypatch.setattr(ledger, "verify", counting_verify)
    assert gate.verify("g31")["generation"] == 31
    assert len(calls) == 1
