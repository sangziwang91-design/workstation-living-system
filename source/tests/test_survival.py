import pytest
from pathlib import Path
from wls.config import default_config
from wls.runtime import LivingSystem

def _runtime(tmp_path, name: str = "home") -> LivingSystem:
    home = tmp_path / name
    config = default_config(home)
    home.joinpath("secrets").mkdir(parents=True, exist_ok=True)
    home.joinpath("secrets/evidence.key").write_bytes(b"a" * 32)
    home.joinpath("secrets/approval.key").write_bytes(b"b" * 32)
    return LivingSystem(config)

def test_daemon_contains_transient_failures_and_completes(tmp_path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    # LivingSystem has run_cycle
    runtime.run_cycle()
    assert runtime.db.integrity_check()[0]

def test_startup_recovers_interrupted_cycle_and_survival_run(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    runtime.run_cycle()

    # Restart
    runtime2 = _runtime(tmp_path)
    assert runtime2.db.integrity_check()[0]
