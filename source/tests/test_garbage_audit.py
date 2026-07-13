from __future__ import annotations

from pathlib import Path

import pytest

from wls import cli
from wls.config import default_config
from wls.runtime import LivingSystem


def test_garbage_audit_records_candidates_without_deleting(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    cache_dir = config.home_path / "tmp" / "__pycache__"
    cache_dir.mkdir(parents=True)
    cached_file = cache_dir / "module.cpython-313.pyc"
    cached_file.write_bytes(b"cache")
    temp_file = config.home_path / "tmp" / "scratch.tmp"
    temp_file.write_text("temporary", encoding="utf-8")
    lock_file = config.home_path / "state" / "stale.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("999999", encoding="ascii")

    receipt = runtime.garbage_audit(reason="unit test garbage scan")

    assert receipt["cleanup_executed"] is False
    assert receipt["owner_review_required"] is True
    assert receipt["candidate_count"] >= 3
    assert cache_dir.exists() is True
    assert cached_file.exists() is True
    assert temp_file.exists() is True
    assert lock_file.exists() is True
    kinds = {candidate["kind"] for candidate in receipt["candidates"]}
    assert {"cache_dir", "temporary_file", "stale_lock_file"} <= kinds
    assert all(
        candidate["owner_review_required"] is True
        for candidate in receipt["candidates"]
    )

    saved = runtime.garbage_audit_receipts()
    assert saved[0]["audit_id"] == receipt["audit_id"]
    assert saved[0]["cleanup_executed"] is False
    assert runtime.health_snapshot()["garbage_audit"]["audit_id"] == receipt["audit_id"]


def test_garbage_audit_clean_home_needs_no_owner_cleanup(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)

    receipt = runtime.garbage_audit(max_candidates=10)

    assert receipt["status"] == "CLEAN"
    assert receipt["candidate_count"] == 0
    assert receipt["owner_review_required"] is False


def test_owner_approved_garbage_cleanup_quarantines_only_candidates(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    cache_dir = config.home_path / "tmp" / "__pycache__"
    cache_dir.mkdir(parents=True)
    (cache_dir / "module.pyc").write_bytes(b"cache")
    temp_file = config.home_path / "tmp" / "scratch.tmp"
    temp_file.write_text("temporary", encoding="utf-8")
    keep_file = config.home_path / "tmp" / "keep.txt"
    keep_file.write_text("keep", encoding="utf-8")

    receipt = runtime.garbage_audit(
        reason="unit test owner cleanup",
        execute_cleanup=True,
        approval_reference="pytest://owner-approved-cleanup",
    )

    assert receipt["cleanup_executed"] is True
    assert receipt["status"] == "CLEANUP_EXECUTED"
    assert receipt["deleted_candidate_count"] == 0
    assert receipt["quarantined_candidate_count"] >= 2
    assert cache_dir.exists() is False
    assert temp_file.exists() is False
    assert keep_file.exists() is True
    quarantine_root = config.home_path / ".wls_quarantine" / receipt["audit_id"]
    assert quarantine_root.exists() is True
    assert any(path.name.endswith("__pycache__") for path in quarantine_root.iterdir())
    assert any(path.name.endswith("scratch.tmp") for path in quarantine_root.iterdir())
    assert runtime.health_snapshot()["garbage_audit"]["audit_id"] == receipt["audit_id"]

    clear = runtime.clear_garbage_quarantine(
        audit_id=receipt["audit_id"],
        approval_reference="pytest://owner-approved-quarantine-clear",
        reason="unit test quarantine clear",
    )
    assert clear["receipt_type"] == "GARBAGE_QUARANTINE_CLEARED"
    assert clear["status"] == "QUARANTINE_CLEARED"
    assert clear["cleared_bytes"] >= receipt["quarantined_bytes"]
    assert quarantine_root.exists() is False
    assert keep_file.exists() is True
    assert runtime.health_snapshot()["garbage_quarantine_clear"]["clear_id"] == clear["clear_id"]


def test_garbage_cleanup_requires_approval_reference(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    temp_file = config.home_path / "tmp" / "scratch.tmp"
    temp_file.parent.mkdir(parents=True)
    temp_file.write_text("temporary", encoding="utf-8")

    with pytest.raises(PermissionError):
        runtime.garbage_audit(execute_cleanup=True)

    assert temp_file.exists() is True


def test_cli_garbage_cleanup_requires_approval_reference(tmp_path) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    temp_file = home / "tmp" / "scratch.tmp"
    temp_file.parent.mkdir(parents=True)
    temp_file.write_text("temporary", encoding="utf-8")

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "garbage-audit",
                "--execute-cleanup",
            ]
        )
        == 1
    )

    assert temp_file.exists() is True


def test_cli_garbage_clear_quarantine_requires_approval_reference(tmp_path) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "garbage-clear-quarantine",
                "--audit-id",
                "garbage_audit_missing",
                "--approval-reference",
                "",
            ]
        )
        == 1
    )


def test_cli_garbage_cleanup_help_describes_quarantine(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["garbage-audit", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "Quarantine only audited cleanup candidates" in output
    assert "Delete only audited cleanup candidates" not in output


def test_garbage_audit_rejects_negative_candidate_limit(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)

    with pytest.raises(ValueError):
        runtime.garbage_audit(max_candidates=-1)


def test_garbage_cleanup_rejects_non_list_candidates(tmp_path) -> None:
    from wls.garbage_audit import GarbageAuditor

    root = tmp_path / "home"
    root.mkdir()
    auditor = GarbageAuditor()

    with pytest.raises(ValueError):
        auditor.execute_cleanup(
            {
                "audit_id": "garbage_audit_test",
                "roots": [str(root)],
                "candidates": "not-a-list",
            },
            approval_reference="pytest://approval",
        )


def test_garbage_cleanup_sanitizes_quarantine_audit_id(tmp_path) -> None:
    from wls.garbage_audit import GarbageAuditor

    root = tmp_path / "home"
    root.mkdir()
    temp_file = root / "scratch.tmp"
    temp_file.write_text("temporary", encoding="utf-8")
    auditor = GarbageAuditor()
    receipt = auditor.audit([root])
    receipt["audit_id"] = "../bad/audit"

    cleaned = auditor.execute_cleanup(
        receipt, approval_reference="pytest://approval"
    )

    quarantine_root = Path(cleaned["quarantine_root"])
    assert quarantine_root.exists()
    assert quarantine_root.parent.name == ".wls_quarantine"
    assert quarantine_root.name.endswith("bad_audit")
    assert "/" not in quarantine_root.name
    assert "\\" not in quarantine_root.name
    assert (quarantine_root / "manifest.json").exists()


def test_clear_quarantine_rejects_tampered_non_quarantine_path(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    victim = config.home_path / "sandbox" / "not-quarantine" / "deep"
    victim.mkdir(parents=True)
    runtime.db.set_runtime(
        "garbage_audit_receipts",
        [
            {
                "audit_id": "garbage_audit_tampered",
                "roots": [str(config.home_path / "sandbox")],
                "quarantine_root": str(victim),
            }
        ],
    )

    with pytest.raises(PermissionError):
        runtime.clear_garbage_quarantine(
            audit_id="garbage_audit_tampered",
            approval_reference="pytest://approval",
            reason="unit test",
        )

    assert victim.exists()
