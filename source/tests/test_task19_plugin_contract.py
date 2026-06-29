from __future__ import annotations

from importlib import import_module
from pathlib import Path

from wls.config import BUILTIN_PLUGIN_MODULES, RuntimeConfig, default_config
from wls.runtime import LivingSystem


EXPECTED = (
    "wls.task19_stabilization",
    "wls.task19_goal_guard",
    "wls.task19_execution_guard",
    "wls.task19_cycle_journal",
    "wls.task19_action_integrity",
    "wls.task19_priority_guard",
)


def test_builtin_plugins_are_unique_importable_and_ordered() -> None:
    assert BUILTIN_PLUGIN_MODULES == EXPECTED
    assert len(EXPECTED) == len(set(EXPECTED))
    for name in EXPECTED:
        assert callable(getattr(import_module(name), "register_wls", None))


def test_validation_keeps_one_copy_of_each_builtin(tmp_path: Path) -> None:
    config = RuntimeConfig(
        home=str(tmp_path),
        plugin_modules=[EXPECTED[0], "example.extension", "example.extension"],
    )
    config.validate()
    assert tuple(config.plugin_modules[: len(EXPECTED)]) == EXPECTED
    assert config.plugin_modules.count(EXPECTED[0]) == 1
    assert config.plugin_modules.count("example.extension") == 1


def test_default_runtime_installs_builtin_contract(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    flags = (
        "_task19_stabilization_installed",
        "_task19_goal_guard_installed",
        "_task19_execution_guard_installed",
        "_task19_cycle_journal_installed",
        "_task19_action_integrity_installed",
        "_task19_priority_guard_installed",
    )
    assert all(getattr(runtime, flag, False) for flag in flags)
    assert runtime.status()["cycle_journal"]["checkpoints"] >= 0
