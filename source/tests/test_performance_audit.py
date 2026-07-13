from __future__ import annotations

import json

from wls import cli
from wls.config import default_config
from wls.performance import PerformanceMeasurement, evaluate_performance_budget
from wls.runtime import LivingSystem


def test_performance_budget_evaluation_blocks_over_budget() -> None:
    report = evaluate_performance_budget(
        [
            PerformanceMeasurement("health_snapshot", [0.1, 0.2], 0.5),
            PerformanceMeasurement("runtime_init", [2.0], 1.5),
        ]
    )

    assert report["passed"] is False
    assert report["status"] == "FAILED"
    assert report["failures"] == [
        {"name": "runtime_init", "max_seconds": 2.0, "budget_seconds": 1.5}
    ]


def test_runtime_records_performance_budget_receipt(tmp_path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))

    receipt = runtime.record_performance_budget_audit(
        measurements=[
            PerformanceMeasurement("health_snapshot", [0.01], 0.5),
            PerformanceMeasurement("runtime_init", [0.2], 1.5),
        ],
        reason="unit test performance audit",
    )

    assert receipt["status"] == "PERFORMANCE_BUDGET_PASSED"
    assert receipt["daemon_started"] is False
    assert receipt["cleanup_executed"] is False
    assert runtime.performance_budget_receipts()[0]["audit_id"] == receipt["audit_id"]
    assert runtime.health_snapshot()["performance_budget"]["audit_id"] == receipt["audit_id"]


def test_cli_performance_audit_records_receipt(tmp_path, capsys) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "performance-audit",
                "--samples",
                "1",
                "--reason",
                "unit test cli performance audit",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["receipt_type"] == "PERFORMANCE_BUDGET_AUDIT"
    assert payload["measurement_count"] == 7
    assert {item["name"] for item in payload["evaluation"]["results"]} == {
        "health_snapshot",
        "runtime_init",
        "garbage_audit",
        "self_check",
        "ui_api_health",
        "ui_api_bootstrap",
        "ui_api_product",
    }


def test_cli_performance_audit_can_measure_disposable_write_workflows(
    tmp_path, capsys
) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "performance-audit",
                "--samples",
                "1",
                "--include-write-workflows",
                "--reason",
                "unit test disposable write workflow audit",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    names = {item["name"] for item in payload["evaluation"]["results"]}

    assert payload["receipt_type"] == "PERFORMANCE_BUDGET_AUDIT"
    assert payload["measurement_count"] == 10
    assert {
        "ui_api_garbage_scan",
        "ui_api_garbage_cleanup",
        "ui_api_garbage_clear",
    }.issubset(names)
    assert payload["evaluation"]["passed"] is True


def test_cli_performance_audit_can_measure_disposable_rollback_workflow(
    tmp_path, capsys
) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "performance-audit",
                "--samples",
                "1",
                "--include-rollback-workflow",
                "--reason",
                "unit test disposable rollback workflow audit",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    names = {item["name"] for item in payload["evaluation"]["results"]}

    assert payload["receipt_type"] == "PERFORMANCE_BUDGET_AUDIT"
    assert payload["measurement_count"] == 8
    assert "rollback_disposable_clone" in names
    assert payload["evaluation"]["passed"] is True


def test_cli_performance_audit_can_measure_all_optional_workflows(
    tmp_path, capsys
) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "performance-audit",
                "--samples",
                "1",
                "--include-write-workflows",
                "--include-rollback-workflow",
                "--reason",
                "unit test all optional workflow audit",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    names = {item["name"] for item in payload["evaluation"]["results"]}

    assert payload["measurement_count"] == 11
    assert {
        "ui_api_garbage_scan",
        "ui_api_garbage_cleanup",
        "ui_api_garbage_clear",
        "rollback_disposable_clone",
    }.issubset(names)
    assert payload["evaluation"]["passed"] is True
