from __future__ import annotations

from importlib import import_module
from pathlib import Path

from wls.config import BUILTIN_PLUGIN_MODULES, RuntimeConfig, default_config
from wls.runtime import LivingSystem


EXPECTED_ORDER = (
    "wls.task19_stabilization",
    "wls.task19_goal_guard",
    "wls.task19_execution_guard",
    "wls.task19_action_integrity",
    "wls.task19_priority_guard",
)
EXPECTED_FLAGS = (
    "_task19_stabilization_installed",
    "_task19_goal_guard_installed",
    "_task19_execution_guard_installed",
    "_task19_action_integrity_installed",
    "_task19_priority_guard_installed",
)


def test_builtin_plugin_manifest_is_unique_importable_and_ordered() -> None:
    assert BUILTIN_PLUGIN_MODULES == EXPECTED_ORDER
    assert len(BUILTIN_PLUGIN_MODULES) == len(set(BUILTIN_PLUGIN_MODULES))
    for module_name in BUILTIN_PLUGIN_MODULES:
        module = import_module(module_name)
        assert callable(getattr(module, "register_wls", None))


def test_validation_prepends_builtins_once_without_losing_extensions(tmp_path: Path) -> None:
    config = RuntimeConfig(
        home=str(tmp_path),
        plugin_modules=[
            "wls.task19_stabilization",
            "example.extension",
            "example.extension",
        ],
    )
    config.validate()
    assert tuple(config.plugin_modules[: len(EXPECTED_ORDER)]) == EXPECTED_ORDER
    assert config.plugin_modules.count("wls.task19_stabilization") == 1
    assert config.plugin_modules.count("example.extension") == 1


def test_default_runtime_installs_every_builtin_plugin(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    for flag in EXPECTED_FLAGS:
        assert getattr(runtime, flag, False) is True
