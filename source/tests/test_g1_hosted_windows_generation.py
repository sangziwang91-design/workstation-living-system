"""G1 Windows generation: provenance, serialized DB and hostile artifact tests."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/g1_hosted_windows_generation.py"
WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wls-g1-windows-generation.yml"
HEAD = "a" * 40


@pytest.fixture
def gen():
    spec = importlib.util.spec_from_file_location("wls_g1_hosted_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def synthetic_home(home: Path) -> None:
    (home / "state").mkdir(parents=True)
    (home / "secrets").mkdir()
    (home / "config.json").write_text(json.dumps({"home": str(home)}), encoding="utf-8")
    (home / "secrets/evidence.key").write_bytes(b"x" * 32)
    with sqlite3.connect(home / "state/wls.db") as db:
        db.execute("CREATE TABLE validated (value INT)")
        db.execute("INSERT INTO validated VALUES (1)")


def test_generation_artifact_roundtrip_checks_source_run_and_data(
    gen, tmp_path: Path,
) -> None:
    home = tmp_path / "source"
    synthetic_home(home)
    manifest = gen.checkpoint(
        home, tmp_path / "artifact", head=HEAD,
        run_id=101, parent=None, prior_manifest_raw=None, count=2,
    )
    assert manifest["generation"] == 0
    assert manifest["synthetic_only"] is True
    target = tmp_path / "restored"
    # Synthetic config contains an absolute home path and must not match a
    # different runner; the test simulates a stable hosted Windows path.
    (home / "config.json").write_text(json.dumps({"home": str(target)}))
    manifest = gen.checkpoint(
        home, tmp_path / "artifact-for-target", head=HEAD,
        run_id=101, parent=None, prior_manifest_raw=None, count=2,
    )
    restored, raw = gen.restore_checkpoint(
        tmp_path / "artifact-for-target/checkpoint.zip", target,
        expected_head=HEAD, expected_run=101,
    )
    assert restored["files_sha256"] == manifest["files_sha256"]
    assert isinstance(raw, bytes)
    with sqlite3.connect(target / "state/wls.db") as db:
        assert db.execute("SELECT value FROM validated").fetchone() == (1,)
    assert (target / "secrets/evidence.key").read_bytes() == b"x" * 32
    for new_id, head in [(999, HEAD), (101, "b" * 40)]:
        with pytest.raises(ValueError, match="provenance"):
            gen.restore_checkpoint(
                tmp_path / "artifact-for-target/checkpoint.zip",
                tmp_path / f"invalid-{new_id}", expected_run=new_id,
                expected_head=head,
            )


def test_artifact_tampering_and_extra_files_block_restoration(
    gen, tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    synthetic_home(source)
    target = tmp_path / "target"
    (source / "config.json").write_text(json.dumps({"home": str(target)}))
    gen.checkpoint(
        source, tmp_path / "checkpoint", head=HEAD,
        run_id=222, parent=None, prior_manifest_raw=None, count=3,
    )
    raw = tmp_path / "checkpoint/checkpoint.zip"
    with zipfile.ZipFile(raw) as zf:
        entries = {n: zf.read(n) for n in zf.namelist()}
    entries["state/wls.db"] = b"replaced"
    malicious = tmp_path / "tampered.zip"
    with zipfile.ZipFile(malicious, "w") as zf:
        for key, value in entries.items():
            zf.writestr(key, value)
    with pytest.raises(ValueError, match="digest"):
        gen.restore_checkpoint(malicious, target, expected_head=HEAD, expected_run=222)
    assert not target.exists()  # never partially write untrusted data

    entries["../../secrets.txt"] = b"secret"
    unexpected = tmp_path / "unexpected.zip"
    with zipfile.ZipFile(unexpected, "w") as zf:
        for key, value in entries.items():
            zf.writestr(key, value)
    with pytest.raises(ValueError, match="contents"):
        gen.restore_checkpoint(unexpected, target, expected_head=HEAD, expected_run=222)
    assert not target.exists()


def test_previous_frozen_source_lineage_does_not_silently_reset(
    gen, monkeypatch,
) -> None:
    assert gen.SCHEMA == "wls.g1.github_windows_generation.v1"
    monkeypatch.setattr(gen, "gh_json", lambda *args: [])
    assert gen.prior_successful_run("o/r", 404, HEAD) is None

    previous = [{
        "databaseId": 403, "status": "completed",
        "conclusion": "failure", "headSha": HEAD, "event": "schedule",
    }]
    monkeypatch.setattr(gen, "gh_json", lambda *args: previous)
    with pytest.raises(ValueError, match="previous complete generation failed"):
        gen.prior_successful_run("o/r", 404, HEAD)
    previous[0]["conclusion"] = "success"
    previous[0]["headSha"] = "b" * 40
    with pytest.raises(ValueError, match="source SHA changed"):
        gen.prior_successful_run("o/r", 404, HEAD)
    previous[0]["headSha"] = HEAD
    assert gen.prior_successful_run("o/r", 404, HEAD) == 403


def test_checkpoint_cannot_overwrite_existing_home(gen, tmp_path: Path) -> None:
    home = tmp_path / "source"
    synthetic_home(home)
    gen.checkpoint(
        home, tmp_path / "checkpoint", head=HEAD,
        run_id=101, parent=None, prior_manifest_raw=None, count=2,
    )
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "sentinel").write_text("do not touch")
    with pytest.raises(ValueError, match="overwrite"):
        gen.restore_checkpoint(
            tmp_path / "checkpoint/checkpoint.zip", existing,
            expected_head=HEAD, expected_run=101,
        )
    assert (existing / "sentinel").read_text() == "do not touch"


def test_workflow_is_main_only_for_generation_and_never_has_write_tokens() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert data["permissions"] == {"contents": "read"}
    job = data["jobs"]["generation"]
    assert job["runs-on"] == "windows-latest"
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert job["needs"] == "regression"
    assert "refs/heads/main" in job["if"]
    assert "pull_request" in job["if"]
    steps = job["steps"]
    for step in steps:
        assert "secrets." not in json.dumps(step)
        if "uses" in step and step["uses"].startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "source/scripts/g1_hosted_windows_generation.py" in text
    assert "wls-g1-windows-generation-state" in text
    assert "github.run_id" in text
    assert "github.sha" in text
    assert "OPENAI_API_KEY: ''" in text


def test_does_not_claim_real_learning_or_private_owner_deployment(gen) -> None:
    assert "autonomous_skill_gain_proven" in SCRIPT.read_text(encoding="utf-8")
    assert "synthetic_only" in SCRIPT.read_text(encoding="utf-8")
    assert "longitudinal_72h_proven" in SCRIPT.read_text(encoding="utf-8")
    assert "owner_context.json" not in SCRIPT.read_text(encoding="utf-8")
