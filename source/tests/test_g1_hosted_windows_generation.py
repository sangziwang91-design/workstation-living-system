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
    # Direct script entrypoint has source/scripts on sys.path; the
    # importlib-based unit fixture must reproduce that legitimate path.
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPT.parent))
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


def test_previous_run_cannot_inherit_future_generation_on_replay(gen, monkeypatch):
    # Running the same old GitHub workflow again must never attach a newer
    # artifact as a parent, even when the future run has the same source SHA.
    observed = [
        {"databaseId": 405, "status": "completed",
         "conclusion": "success", "headSha": HEAD, "event": "schedule"},
        {"databaseId": 403, "status": "completed",
         "conclusion": "success", "headSha": HEAD, "event": "push"},
    ]
    monkeypatch.setattr(gen, "gh_json", lambda *args: observed)
    assert gen.prior_successful_run("o/r", 404, HEAD) == 403
    assert gen.prior_successful_run("o/r", 403, HEAD) is None
    observed[1]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="previous complete generation failed"):
        gen.prior_successful_run("o/r", 404, HEAD)


def test_g1_serializes_trusted_generations_but_not_pull_requests():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    group = doc["concurrency"]["group"]
    assert "github.event_name == 'pull_request'" in group
    assert "github.event.pull_request.number" in group
    assert "trusted-main" in group
    assert doc["concurrency"]["queue"] == "max"
    assert doc["concurrency"]["cancel-in-progress"] is False
    assert doc["jobs"]["generation"]["needs"] == "regression"
    assert "refs/heads/main" in doc["jobs"]["generation"]["if"]


def test_verified_prior_metadata_can_name_old_source_without_silent_reset(
    gen, monkeypatch,
) -> None:
    previous = [{"databaseId": 123, "status": "completed",
                 "conclusion": "success", "headSha": HEAD,
                 "event": "schedule"}]
    monkeypatch.setattr(gen, "gh_json", lambda *args: previous)
    assert gen.previous_verified_run("owner/repo", 124) == {
        "run_id": 123, "head_sha": HEAD, "skipped_failed_run_ids": [],
    }
    with pytest.raises(ValueError, match="source SHA changed"):
        gen.prior_successful_run("owner/repo", 124, "b" * 40)
    previous[0]["headSha"] = "mutable-main"
    with pytest.raises(ValueError, match="invalid source provenance"):
        gen.previous_verified_run("owner/repo", 124)


def test_cross_source_synthetic_continuity_is_verified_and_explicit(
    gen, tmp_path: Path, monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "old-synthetic"
    home = workspace / ".g1-windows" / "home"
    synthetic_home(source)
    (source / "config.json").write_text(
        json.dumps({"home": str(home)}), encoding="utf-8"
    )
    old = gen.checkpoint(
        source, tmp_path / "prior-artifact", head=HEAD,
        run_id=123, parent=None, prior_manifest_raw=None, count=2,
    )
    archive = tmp_path / "prior-artifact" / "checkpoint.zip"
    monkeypatch.setattr(gen, "previous_verified_run",
                        lambda *args: {"run_id": 123, "head_sha": HEAD,
                                       "skipped_failed_run_ids": [122]})
    monkeypatch.setattr(gen, "download_checkpoint",
                        lambda *args: archive)
    newer_head = "b" * 40
    events: list[str] = []
    count = 2

    def fake_cli(_home, command, *args):
        nonlocal count
        events.append(command)
        if command == "verify":
            return {"ok": True}
        if command == "status":
            return {"cycle_count": count, "paused": False, "killed": False,
                    "read_only": True,
                    "latest_cycle": {
                        "cycle_id": f"cycle_{count}", "status": "SUCCEEDED",
                    }}
        if command == "once":
            count += 1
            return {"status": "SUCCEEDED", "cycle_id": f"cycle_{count}"}
        if command == "sleep":
            return {"status": "COMPLETED"}
        raise AssertionError(command)

    monkeypatch.setattr(gen, "cli", fake_cli)
    report = gen.run("owner/repo", 124, newer_head, workspace)
    assert events[0] == "verify", "pre-upgrade validation must precede any cycle"
    assert report["status"] == "HOSTED_WINDOWS_GENERATION_PASS"
    assert report["parent_run_id"] == 123
    assert report["source_transition"] is True
    assert report["failed_attempts_not_in_lineage"] == [122]
    assert report["parent_head_sha"] == HEAD
    assert report["generation"] == old["generation"] + 1
    assert report["cycle_count"] == 4
    with zipfile.ZipFile(workspace / "g1-generation-checkpoint/checkpoint.zip") as zf:
        current = json.loads(zf.read("manifest.json"))
    assert current["head_sha"] == newer_head
    assert current["parent_head_sha"] == HEAD
    assert current["source_transition"] is True
    assert current["previous_manifest_sha256"] is not None


def test_incompatible_synthetic_source_transition_never_executes_cycle(
    gen, tmp_path: Path, monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "old-synthetic"
    home = workspace / ".g1-windows" / "home"
    synthetic_home(source)
    (source / "config.json").write_text(
        json.dumps({"home": str(home)}), encoding="utf-8"
    )
    gen.checkpoint(
        source, tmp_path / "old-artifact", head=HEAD, run_id=123,
        parent=None, prior_manifest_raw=None, count=2,
    )
    monkeypatch.setattr(gen, "previous_verified_run",
                        lambda *args: {"run_id": 123, "head_sha": HEAD,
                                       "skipped_failed_run_ids": [122]})
    monkeypatch.setattr(
        gen, "download_checkpoint",
        lambda *args: tmp_path / "old-artifact" / "checkpoint.zip",
    )
    events: list[str] = []
    def reject_verify(home, command, *args):
        events.append(command)
        if command == "verify":
            return {"ok": False}
        raise AssertionError("WLS must not continue after failed verification")
    monkeypatch.setattr(gen, "cli", reject_verify)
    with pytest.raises(ValueError, match="incompatible"):
        gen.run("owner/repo", 124, "b" * 40, workspace)
    assert events == ["verify"]
    assert not (workspace / "g1-generation-checkpoint").exists()


def test_failed_attempts_are_bounded_and_never_silent(
    gen, monkeypatch,
) -> None:
    success = {"databaseId": 100, "status": "completed",
               "conclusion": "success", "headSha": HEAD, "event": "schedule"}
    failures = [
        {"databaseId": 104 - i, "status": "completed",
         "conclusion": "failure", "headSha": "b" * 40, "event": "push"}
        for i in range(4)
    ]
    monkeypatch.setattr(gen, "gh_json", lambda *args: failures[:2] + [success])
    record = gen.previous_verified_run("owner/repo", 105)
    assert record == {
        "run_id": 100, "head_sha": HEAD,
        "skipped_failed_run_ids": [104, 103],
    }
    # Strict frozen admission still refuses to hide an incomplete iteration.
    with pytest.raises(ValueError, match="previous complete generation failed"):
        gen.prior_successful_run("owner/repo", 105, HEAD)
    monkeypatch.setattr(gen, "gh_json", lambda *args: failures + [success])
    with pytest.raises(ValueError, match="too many failed generation attempts"):
        gen.previous_verified_run("owner/repo", 105)
    monkeypatch.setattr(gen, "gh_json", lambda *args: failures[:1])
    with pytest.raises(ValueError, match="no silent reset"):
        gen.previous_verified_run("owner/repo", 105)


def test_sqlite_wal_snapshot_preserves_committed_uncheckpointed_pages(
    gen, tmp_path: Path,
) -> None:
    source = tmp_path / "src-wal"
    target = tmp_path / "target"
    synthetic_home(source)
    (source / "config.json").write_text(
        json.dumps({"home": str(target)}), encoding="utf-8"
    )
    # Keep the writer open: the new row exists in -wal, not necessarily
    # in the main database file. Raw copying wls.db would lose it.
    writer = sqlite3.connect(source / "state/wls.db")
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("INSERT INTO validated VALUES (99)")
        writer.commit()
        artifact = gen.checkpoint(
            source, tmp_path / "snapshot", head=HEAD,
            run_id=405, parent=None, prior_manifest_raw=None, count=2,
        )
        assert artifact["files_sha256"]["state/wls.db"]
    finally:
        writer.close()
    with zipfile.ZipFile(tmp_path / "snapshot/checkpoint.zip") as zf:
        portable = zf.read("state/wls.db")
    assert portable.startswith(b"SQLite format 3\x00")
    assert portable[18:20] == b"\x01\x01", "backup must use portable rollback journal"
    gen.restore_checkpoint(
        tmp_path / "snapshot/checkpoint.zip", target,
        expected_head=HEAD, expected_run=405,
    )
    with sqlite3.connect(target / "state/wls.db") as conn:
        assert conn.execute("SELECT value FROM validated ORDER BY value").fetchall() == [
            (1,), (99,),
        ]


def test_historical_wal_header_checkpoint_is_normalized_after_provenance(
    gen, tmp_path: Path,
) -> None:
    source = tmp_path / "old-wal"
    target = tmp_path / "old-wal-restored"
    synthetic_home(source)
    (source / "config.json").write_text(
        json.dumps({"home": str(target)}), encoding="utf-8"
    )
    with sqlite3.connect(source / "state/wls.db") as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("INSERT INTO validated VALUES (42)")
    legacy_raw = (source / "state/wls.db").read_bytes()
    assert legacy_raw[18:20] == b"\x02\x02"
    gen.checkpoint(
        source, tmp_path / "new-snapshot", head=HEAD,
        run_id=444, parent=None, prior_manifest_raw=None, count=2,
    )
    with zipfile.ZipFile(tmp_path / "new-snapshot/checkpoint.zip") as zf:
        entries = {name: zf.read(name) for name in zf.namelist()}
    manifest = json.loads(entries["manifest.json"])
    # Recreate the historical direct-file copy, with its own *valid* digest.
    entries["state/wls.db"] = legacy_raw
    manifest["files_sha256"]["state/wls.db"] = gen.sha(legacy_raw)
    entries["manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
    archive = tmp_path / "legacy-source-sha-checkpoint.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    old_manifest, original_manifest_raw = gen.restore_checkpoint(
        archive, target, expected_head=HEAD, expected_run=444,
    )
    assert old_manifest["synthetic_only"] is True
    assert original_manifest_raw == entries["manifest.json"]
    normalized = (target / "state/wls.db").read_bytes()
    assert normalized[18:20] == b"\x01\x01"
    with sqlite3.connect(target / "state/wls.db") as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("SELECT value FROM validated ORDER BY value").fetchall() == [
            (1,), (42,),
        ]
    # Invalid bytes may never enter the WLS home.
    with pytest.raises(ValueError, match="bounded SQLite"):
        gen.validated_portable_sqlite(b"this is not SQLite")
