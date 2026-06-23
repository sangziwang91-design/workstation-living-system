from __future__ import annotations

from pathlib import Path

import pytest

from wls.config import RuntimeConfig, SensorConfig
from wls.runtime import LivingSystem


def make_config(
    home: Path,
    sensors: list[SensorConfig] | None = None,
    *,
    read_only: bool = True,
    workspace_capacity: int = 8,
    max_events_per_cycle: int = 50,
    max_actions_per_cycle: int = 4,
    sleep_after_idle_cycles: int = 5,
) -> RuntimeConfig:
    return RuntimeConfig(
        home=str(home),
        cycle_seconds=0.01,
        workspace_capacity=workspace_capacity,
        max_events_per_cycle=max_events_per_cycle,
        max_actions_per_cycle=max_actions_per_cycle,
        read_only=read_only,
        allow_autonomous_read_actions=True,
        allow_autonomous_reversible_writes=False,
        full_integrity_check_every=10,
        memory_retrieval_limit=8,
        sleep_after_idle_cycles=sleep_after_idle_cycles,
        max_autonomous_goals=3,
        sensors=sensors or [],
        tool_policy={
            "allowed_read_roots": [str(home), str(home.parent)],
            "allowed_write_roots": [str(home / "sandbox"), str(home / "outbox")],
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "allowed_commands": ["git", "python", "python.exe"],
        },
        provider={"type": "deterministic"},
    )


@pytest.fixture
def runtime_factory(tmp_path):
    def factory(**kwargs):
        home = kwargs.pop("home", tmp_path / f"home-{len(list(tmp_path.iterdir()))}")
        config = make_config(home, **kwargs)
        return LivingSystem(config)
    return factory
