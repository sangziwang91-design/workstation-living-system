from __future__ import annotations

from pathlib import Path

from wls import cli
from wls.config import default_config
from wls.runtime import LivingSystem


def test_upgrade_drill_backs_up_and_verifies_restore_copy(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    wheel = tmp_path / "dist" / "wls-test.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"test-wheel")

    receipt = runtime.run_upgrade_drill(
        wheel_path=wheel,
        reason="unit test upgrade drill",
    )

    assert receipt["status"] == "UPGRADE_DRILL_PASSED"
    assert receipt["live_install_modified"] is False
    assert receipt["live_database_restored"] is False
    assert Path(receipt["backup"]["path"]).is_file()
    assert Path(receipt["restore_check"]["path"]).is_file()
    assert receipt["restore_check"]["passed"] is True
    assert runtime.health_snapshot()["upgrade_drill"]["drill_id"] == receipt["drill_id"]


def test_upgrade_drill_can_verify_disposable_clone_rollback(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    wheel = tmp_path / "dist" / "wls-test.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"test-wheel")

    receipt = runtime.run_upgrade_drill(
        wheel_path=wheel,
        reason="unit test disposable clone rollback",
        disposable_clone=True,
    )
    clone = receipt["disposable_clone_rollback"]

    assert receipt["status"] == "UPGRADE_DRILL_PASSED"
    assert receipt["disposable_clone_executed"] is True
    assert receipt["live_install_modified"] is False
    assert receipt["live_database_restored"] is False
    assert clone["passed"] is True
    assert clone["marker_written_before_rollback"] is True
    assert clone["marker_removed_after_rollback"] is True
    assert clone["rollback_matches_backup"] is True
    assert clone["clone_home_removed"] is True
    assert Path(clone["clone_home"]).exists() is False
    assert runtime.health_snapshot()["upgrade_drill"]["drill_id"] == receipt["drill_id"]


def test_cli_upgrade_drill_requires_existing_wheel(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "upgrade-drill",
                "--wheel",
                str(tmp_path / "missing.whl"),
            ]
        )
        == 1
    )


def test_upgrade_drill_requires_wheel_extension(tmp_path: Path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    artifact = tmp_path / "dist" / "not-a-wheel.txt"
    artifact.parent.mkdir()
    artifact.write_text("not a wheel", encoding="utf-8")

    try:
        runtime.run_upgrade_drill(wheel_path=artifact, reason="unit test")
    except ValueError as exc:
        assert ".whl" in str(exc)
    else:
        raise AssertionError("non-wheel artifact should be rejected")


def test_cli_upgrade_drill_accepts_disposable_clone_flag(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    wheel = tmp_path / "dist" / "wls-test.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"test-wheel")
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "upgrade-drill",
                "--wheel",
                str(wheel),
                "--disposable-clone",
                "--reason",
                "unit test cli disposable clone rollback",
            ]
        )
        == 0
    )
    assert '"disposable_clone_executed": true' in capsys.readouterr().out
