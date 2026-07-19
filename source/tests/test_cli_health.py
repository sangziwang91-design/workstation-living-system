from __future__ import annotations

import json

from wls import cli


def test_cli_health_uses_lightweight_snapshot(tmp_path, monkeypatch, capsys) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    def fail_runtime_init(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("health command should not build full LivingSystem")

    monkeypatch.setattr(cli, "runtime_from_args", fail_runtime_init)

    assert cli.main(["--config", str(config_path), "health"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "OK"
    assert payload["projection_only"] is False


def test_cli_capabilities_reports_user_facing_truth_table(capsys) -> None:
    assert cli.main(["capabilities"]) == 0
    payload = json.loads(capsys.readouterr().out)

    labels = {item["id"]: item["label"] for item in payload["capabilities"]}

    assert payload["schema_version"] == 1
    assert payload["counts"]["AVAILABLE"] >= 10
    assert labels["goal_tracking"] == "AVAILABLE"
    assert labels["owner_console"] == "AVAILABLE"
    assert labels["learned_skills"] == "PARTIAL"
    assert labels["cognition_dashboard"] == "INTERNAL"
    assert labels["browser_computer_control"] == "NOT_PRODUCTIZED"
    assert labels["multi_worker_agent_os"] == "NOT_PRODUCTIZED"
