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
