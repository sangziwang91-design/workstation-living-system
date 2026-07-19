from __future__ import annotations

import json

from wls import cli


def test_cli_world_query_returns_matching_relationships(tmp_path, capsys) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "add-event",
                "architecture_note",
                "--payload",
                '{"topic":"mission"}',
            ]
        )
        == 0
    )
    event = json.loads(capsys.readouterr().out)

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "add-goal",
                "Mission R0",
                "--criterion",
                "relationship query works",
            ]
        )
        == 0
    )
    goal = json.loads(capsys.readouterr().out)

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "add-relationship",
                "WLS",
                "architecture_correction",
                "--value",
                '{"to":"Mission Layer","reason":"restore collaborator layer"}',
                "--stability",
                "stable",
                "--confidence",
                "0.9",
                "--source-id",
                event["event_id"],
                "--source-id",
                goal["goal_id"],
            ]
        )
        == 0
    )
    relationship = json.loads(capsys.readouterr().out)

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "world",
                "--query",
                "architecture_correction Mission Layer",
                "--limit",
                "5",
            ]
        )
        == 0
    )
    results = json.loads(capsys.readouterr().out)

    assert any(
        item["kind"] == "relationship"
        and item["relation_id"] == relationship["relation_id"]
        for item in results
    )
