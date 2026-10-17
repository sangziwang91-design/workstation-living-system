"""R1 identity-gate negative controls; not evidence that any mutation was solved."""
from __future__ import annotations

import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/freeze_r1_public_taskpack.py"


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location("wls_r1_gate_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixture_pack(gate, tmp_path: Path, count: int = 100):
    root = tmp_path / "repo"
    root.mkdir()
    needed = list(gate.PINNED_SCORER_PATHS)
    for path in [*needed, "source/src/wls/method.py", "source/tests/test_method.py"]:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if path == "source/scripts/freeze_r1_public_taskpack.py":
            shutil.copyfile(SCRIPT, target)
        elif path == "source/src/wls/method.py":
            target.write_text("def value():\n    return 4\n", encoding="utf-8")
        else:
            target.write_text("# public synthetic fixture\n", encoding="utf-8")
    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, check=True,
        )
        return completed.stdout.strip()

    git("init", "-q")
    git("add", ".")
    git("-c", "user.email=test@example.invalid",
        "-c", "user.name=R1 synthetic test",
        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline")
    head = git("rev-parse", "HEAD")
    source_hash = gate.digest((root / "source/src/wls/method.py").read_bytes())
    cases = []
    for i in range(count):
        cases.append({
            "id": f"r1_case_{i:04d}",
            "source_path": "source/src/wls/method.py",
            "source_sha256": source_hash,
            "mutant_sha256": gate.digest(f"synthetic mutant fixture {i}".encode()),
            "mutation_kind": "literal", "mutation_line": 2,
            "test_paths": ["source/tests/test_method.py"],
            "candidate_patch_sha256s": [
                gate.digest(f"candidate {i}-a".encode()),
                gate.digest(f"candidate {i}-b".encode()),
            ],
        })
    data = {
        "schema": gate.SCHEMA, "source_head": head,
        "max_test_invocations": 3, "baseline_seed": 20261010,
        "tasks": cases,
    }
    pack_path = tmp_path / "taskpack.json"
    pack_path.write_bytes(gate.encode(data))
    return root, data, pack_path, tmp_path / "sealed-contract.json"


def test_one_hundred_public_case_identity_can_be_sealed_and_rechecked(gate, tmp_path):
    root, _pack, tasks, manifest = fixture_pack(gate, tmp_path)
    result = gate.seal(root, tasks, manifest)
    assert result["task_count"] == 100
    assert result["max_test_invocations"] == 3
    assert result["qualification"].startswith("UNMEASURED")
    assert result["heldout_gain"] == "UNMEASURED"
    assert result["model_gain"] == "UNMEASURED"
    assert gate.verify(root, tasks, manifest) == result
    with pytest.raises(ValueError, match="rewrite"):
        gate.seal(root, tasks, manifest)


@pytest.mark.parametrize("defect", [
    "few_cases", "more_budget", "duplicate_id", "duplicate_mutant",
    "unsafe_source", "unsafe_test", "too_many_candidates", "bad_source_hash",
    "bad_mutant_line", "extra_schema_field",
])
def test_bad_mutation_input_refused(gate, tmp_path, defect):
    root, pack, tasks, manifest = fixture_pack(gate, tmp_path)
    first = pack["tasks"][0]
    if defect == "few_cases":
        pack["tasks"].pop()
    elif defect == "more_budget":
        pack["max_test_invocations"] = 4
    elif defect == "duplicate_id":
        pack["tasks"][1]["id"] = first["id"]
    elif defect == "duplicate_mutant":
        pack["tasks"][1]["mutant_sha256"] = first["mutant_sha256"]
    elif defect == "unsafe_source":
        first["source_path"] = "../../private/secret.py"
    elif defect == "unsafe_test":
        first["test_paths"] = ["../../private/private_test.py"]
    elif defect == "too_many_candidates":
        first["candidate_patch_sha256s"] *= 101
    elif defect == "bad_source_hash":
        first["source_sha256"] = "f" * 64
    elif defect == "bad_mutant_line":
        first["mutation_line"] = 300
    else:
        pack["unauthorized"] = True
    tasks.write_bytes(gate.encode(pack))
    with pytest.raises(ValueError):
        gate.seal(root, tasks, manifest)
    assert not manifest.exists()


@pytest.mark.parametrize("changed", ["tasks", "source", "tests", "scorer", "order", "env"])
def test_immutable_contract_rejects_post_seal_standard_change(gate, tmp_path, monkeypatch, changed):
    root, pack, tasks, manifest = fixture_pack(gate, tmp_path)
    gate.seal(root, tasks, manifest)
    if changed == "tasks":
        pack["baseline_seed"] += 1
        tasks.write_bytes(gate.encode(pack))
    elif changed == "source":
        (root / "source/src/wls/method.py").write_text("def value():\n    return 5\n")
    elif changed == "tests":
        (root / "source/tests/test_method.py").write_text("assert False\n")
    elif changed == "scorer":
        (root / "source/src/wls/benchmark.py").write_text("tampered\n")
    elif changed == "order":
        pack["tasks"][0], pack["tasks"][1] = pack["tasks"][1], pack["tasks"][0]
        tasks.write_bytes(gate.encode(pack))
    else:
        monkeypatch.setattr(gate, "environment", lambda: {"python": "other"})
    with pytest.raises((gate.StandardMoved, ValueError)):
        gate.verify(root, tasks, manifest)


def test_forged_seal_and_duplicate_json_keys_rejected(gate, tmp_path):
    root, pack, tasks, manifest = fixture_pack(gate, tmp_path)
    gate.seal(root, tasks, manifest)
    forged = json.loads(manifest.read_text())
    forged["model_gain"] = "PASS"
    manifest.write_bytes(gate.encode(forged))
    with pytest.raises(gate.StandardMoved):
        gate.verify(root, tasks, manifest)
    tasks.write_text('{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError, match="duplicate"):
        gate.verify(root, tasks, manifest)


def test_budget_cannot_hide_as_boolean(gate, tmp_path):
    root, pack, tasks, manifest = fixture_pack(gate, tmp_path)
    pack["max_test_invocations"] = True
    tasks.write_bytes(gate.encode(pack))
    with pytest.raises(ValueError, match="three"):
        gate.seal(root, tasks, manifest)


def test_task_pack_cannot_claim_another_git_revision(gate, tmp_path):
    root, pack, tasks, manifest = fixture_pack(gate, tmp_path)
    pack["source_head"] = "b" * 40
    tasks.write_bytes(gate.encode(pack))
    with pytest.raises(gate.StandardMoved, match="Git HEAD"):
        gate.seal(root, tasks, manifest)


def test_changed_unlisted_git_tracked_helper_blocks_existing_seal(gate, tmp_path):
    root, pack, tasks, manifest = fixture_pack(gate, tmp_path)
    gate.seal(root, tasks, manifest)
    (root / "source/tests/test_method.py").write_text("assert 1 == 1\n")
    with pytest.raises(gate.StandardMoved, match="tracked checkout"):
        gate.verify(root, tasks, manifest)
