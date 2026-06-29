from __future__ import annotations

from importlib import import_module
from pathlib import Path

from wls.config import BUILTIN_PLUGIN_MODULES, default_config
from wls.runtime import LivingSystem


EXPECTED = (
    "wls.task19_stabilization",
    "wls.task19_goal_guard",
    "wls.task19_execution_guard",
    "wls.task19_event_queue",
    "wls.task19_cycle_journal",
    "wls.task19_action_integrity",
    "wls.task19_priority_guard",
)


def test_builtin_plugin_order_and_imports() -> None:
    assert BUILTIN_PLUGIN_MODULES == EXPECTED
    assert len(EXPECTED) == len(set(EXPECTED))
    assert all(
        callable(getattr(import_module(name), "register_wls", None))
        for name in EXPECTED
    )


def test_default_runtime_installs_builtin_contract(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    try:
        flags = (
            "_task19_stabilization_installed",
            "_task19_goal_guard_installed",
            "_task19_execution_guard_installed",
            "_task19_event_queue_installed",
            "_task19_cycle_journal_installed",
            "_task19_action_integrity_installed",
            "_task19_priority_guard_installed",
        )
        assert all(getattr(runtime, flag, False) for flag in flags)
        assert not hasattr(runtime, "examiner")
        status = runtime.status()
        assert status["event_queue"]["due"] >= 0
        assert status["cycle_journal"]["checkpoints"] >= 0
    finally:
        runtime.close()
