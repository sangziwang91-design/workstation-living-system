"""Negative controls for hosted canonical WLS self-run, no fake RSI score."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_hosted_living_loop.py"


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("wls_life_hosted_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cli_env_suppresses_credentials(probe, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "sentinel")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("GH_TOKEN", "secret")
    home = tmp_path / "home"
    env = probe.safe_cli_env(home)
    assert env["HOME"] == str(home)
    assert env["WLS_HOME"] == str(home)
    assert not any(key in env for key in
                   ("GITHUB_TOKEN", "GH_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"))


def test_validate_requires_real_persisted_cycle(probe):
    cycle = {"status": "SUCCEEDED", "cycle_id": "cycle_" + "f" * 32}
    snapshot = {"latest_cycle": {"cycle_id": cycle["cycle_id"], "status": "SUCCEEDED"},
                "cycle_count": 1, "paused": False, "killed": False,
                "read_only": True}
    assert probe.validate_cycle(cycle, snapshot, 0) == cycle["cycle_id"]
    for field, bad in [("read_only", False), ("cycle_count", 0),
                       ("paused", True), ("killed", True)]:
        modified = dict(snapshot)
        modified[field] = bad
        with pytest.raises(ValueError):
            probe.validate_cycle(cycle, modified, 0)
    with pytest.raises(ValueError, match="restart"):
        probe.validate_cycle(cycle, {**snapshot, "latest_cycle": {}}, 0)
    with pytest.raises(ValueError, match="failed"):
        probe.validate_cycle({**cycle, "status": "FAILED"}, snapshot, 0)


def test_rejects_unfrozen_head_and_existing_home(probe, tmp_path):
    with pytest.raises(ValueError, match="immutable"):
        probe.execute(tmp_path / "new", head="main")
    occupied = tmp_path / "existing"
    occupied.mkdir()
    (occupied / "private.json").write_text("must not overwrite")
    with pytest.raises(ValueError, match="pre-existing"):
        probe.execute(occupied, head="a" * 40)
    assert (occupied / "private.json").exists()


def test_three_independent_process_cycles_must_have_distinct_ids(probe, tmp_path, monkeypatch):
    count = 0
    def fake_cli(home, command, *args):
        nonlocal count
        if command == "init":
            return {"status": {"cycle_count": 0, "read_only": True}}
        if command == "once":
            count += 1
            return {"status": "SUCCEEDED", "cycle_id": f"cycle_{count}"}
        if command == "status":
            return {"latest_cycle": {"cycle_id": f"cycle_{count}", "status": "SUCCEEDED"},
                    "cycle_count": count, "paused": False, "killed": False,
                    "read_only": True}
        if command == "verify":
            return {"ok": True}
        if command == "life-state":
            return {"system": {"cycle_count": count, "read_only": True}}
        if command == "sleep":
            return {"completed": True}
        raise AssertionError(command)
    monkeypatch.setattr(probe, "cli", fake_cli)
    report = probe.execute(tmp_path / "life", head="a" * 40)
    assert report["status"] == "HOSTED_EPHEMERAL_LIFE_CYCLES_PASS"
    assert report["cycle_count"] == 3
    assert report["home_persisted_across_runs"] is False
    assert report["autonomous_skill_gain_proven"] is False
    assert count == 3
