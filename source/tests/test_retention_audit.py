from __future__ import annotations

import json

from wls import cli
from wls.config import default_config
from wls.runtime import LivingSystem


def test_runtime_retention_audit_flags_over_limit_receipts(tmp_path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    runtime.db.set_runtime("example_receipts", [{"id": "a"}, {"id": "b"}])

    receipt = runtime.record_retention_audit(
        reason="unit test retention audit",
        max_items=1,
        max_json_bytes=1_000_000,
    )

    assert receipt["status"] == "RETENTION_AUDIT_REVIEW_REQUIRED"
    assert receipt["cleanup_executed"] is False
    assert receipt["owner_review_required"] is True
    assert "example_receipts" in receipt["over_count_keys"]
    assert runtime.health_snapshot()["retention_audit"]["audit_id"] == receipt["audit_id"]


def test_cli_retention_audit_records_read_only_receipt(tmp_path, capsys) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "retention-audit",
                "--reason",
                "unit test cli retention audit",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["receipt_type"] == "RETENTION_AUDIT"
    assert payload["cleanup_executed"] is False
    assert payload["claim_ceiling"].startswith("read-only runtime receipt")

    assert cli.main(["--config", str(config_path), "health"]) == 0
    health = json.loads(capsys.readouterr().out)
    assert health["retention_audit"]["audit_id"] == payload["audit_id"]
