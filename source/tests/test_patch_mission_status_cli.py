from __future__ import annotations

import json
from pathlib import Path

from wls.config import default_config, save_config
from wls.patch_mission_status_cli import main
from wls.runtime import LivingSystem


def test_patch_mission_status_cli_reports_persisted_continuity(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    (repo / "demo.py").write_text("print('hello')\n", encoding="utf-8")

    home = tmp_path / "home"
    config_path = home / "config.json"
    config = default_config(home)
    config.sensors = []
    save_config(config, config_path)

    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Inspect the demo project before patching",
        execute_first_action=False,
    )
    runtime.db.close_all()

    exit_code = main([
        "--config",
        str(config_path),
        "--mission-id",
        mission["mission_id"],
        "--continuity-only",
    ])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["count"] == 1
    assert payload["authority"] == {
        "projection_only": True,
        "mutates_runtime_state": False,
        "next_actions_require_existing_patch_mission_policy": True,
    }
    item = payload["patch_missions"][0]
    assert item["mission_id"] == mission["mission_id"]
    assert item["mission"] == "Inspect the demo project before patching"
    assert item["repo_path"] == str(repo.resolve())
    assert item["continuity"]["state"] == "started"
    assert item["continuity"]["latest_action_id"] == mission["action_id"]
