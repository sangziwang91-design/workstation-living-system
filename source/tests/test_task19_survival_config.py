from __future__ import annotations

from pathlib import Path
import json

import pytest

from wls.config import RuntimeConfig, default_config, load_config, save_config
from wls.runtime import LivingSystem


def test_default_survival_contract_is_explicit_and_runtime_status_works(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    assert config.daemon_max_pending_events > 0
    assert config.daemon_max_database_bytes >= 1024 * 1024
    assert config.daemon_max_cycle_seconds > 0
    assert config.daemon_max_consecutive_failures >= 1
    assert config.daemon_failure_backoff_max_seconds >= config.daemon_failure_backoff_seconds
    assert config.daemon_heartbeat_every_cycles >= 1
    assert config.daemon_heartbeat_retention >= 1
    runtime = LivingSystem(config)
    status = runtime.status()
    assert status["survival"]["budgets"]["max_pending_events"] == config.daemon_max_pending_events


def test_old_config_without_daemon_fields_loads_with_safe_defaults(tmp_path: Path) -> None:
    home = tmp_path / "legacy-home"
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "home": str(home),
                "identity_name": "legacy",
                "cycle_seconds": 1.0,
                "workspace_capacity": 8,
                "max_events_per_cycle": 50,
                "max_actions_per_cycle": 1,
                "read_only": True,
                "allow_autonomous_read_actions": True,
                "allow_autonomous_reversible_writes": False,
                "full_integrity_check_every": 50,
                "memory_retrieval_limit": 8,
                "memory_decay_days": 30,
                "sleep_after_idle_cycles": 5,
                "max_autonomous_goals": 3,
                "sensors": [],
                "tool_policy": {},
                "provider": {"type": "cognitive", "fallback": "deterministic"},
                "plugin_modules": [],
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.daemon_max_consecutive_failures == 3
    assert "wls.task19_stabilization" in config.plugin_modules
    roundtrip = tmp_path / "roundtrip.json"
    save_config(config, roundtrip)
    saved = json.loads(roundtrip.read_text(encoding="utf-8"))
    assert saved["daemon_heartbeat_retention"] == 1000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("daemon_max_pending_events", 0),
        ("daemon_max_database_bytes", 10),
        ("daemon_max_cycle_seconds", 0),
        ("daemon_max_consecutive_failures", 0),
        ("daemon_heartbeat_every_cycles", 0),
        ("daemon_heartbeat_retention", 0),
    ],
)
def test_invalid_survival_limits_fail_closed(tmp_path: Path, field: str, value: int) -> None:
    config = RuntimeConfig(home=str(tmp_path))
    setattr(config, field, value)
    with pytest.raises(ValueError):
        config.validate()


def test_backoff_ceiling_cannot_be_below_initial_backoff(tmp_path: Path) -> None:
    config = RuntimeConfig(
        home=str(tmp_path),
        daemon_failure_backoff_seconds=10.0,
        daemon_failure_backoff_max_seconds=1.0,
    )
    with pytest.raises(ValueError, match="cannot be below"):
        config.validate()


def test_survival_preflight_and_failure_budget(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.daemon_max_consecutive_failures = 2
    runtime = LivingSystem(config)
    run_id = runtime.survival.start_run(3)
    assert runtime.survival.preflight(run_id, 0)["allowed"] is True
    first = runtime.survival.record_failure(run_id, 0, RuntimeError("first"))
    second = runtime.survival.record_failure(run_id, 1, RuntimeError("second"))
    assert first["stop"] is False
    assert second["stop"] is True
    assert second["backoff_seconds"] <= config.daemon_failure_backoff_max_seconds
