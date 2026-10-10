"""Real WLS read_file error-to-goal validation, not mock-row autonomy."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

from wls.config import default_config
from wls.runtime import LivingSystem

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_g1_windows_endogenous_growth.py"


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("wls_g1_endogenous_windows", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_real_io_failure_is_observed_and_goal_is_not_fake(probe) -> None:
    receipt = probe.execute()  # real canonical WLS + actual OS file I/O
    assert receipt["status"] == "REAL_WINDOWS_FAILURE_TO_AUTONOMOUS_GOAL"
    assert receipt["independently_failed_real_actions"] == 3
    assert receipt["negative_control_two_failures_created_candidates"] == 0
    assert receipt["autonomous_growth_goals"] == 1
    assert receipt["candidate_source_references_verified"] is True
    assert receipt["restart_preserves_provenance"] is True
    assert receipt["external_task_repaired"] is False
    assert receipt["skill_improvement_proven"] is False
    assert receipt["model_calls"] == 0


def test_failing_fixture_must_be_actually_missing(probe, tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    existing = config.sandbox_path / "real-file.txt"
    existing.write_text("exists", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpectedly exists"):
        probe.recorded_failure(runtime, existing)
    assert runtime.db.query_all("SELECT action_id FROM actions") == []
    runtime.db.close_all()


def test_claim_ceiling_does_not_pretend_improvement(probe) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"skill_improvement_proven": False' in source
    assert '"external_task_repaired": False' in source
    assert '"model_calls": 0' in source
    assert 'runtime._execute_plan(plan)' in source
    assert 'source_action_ids' in source
    assert 'runtime.verify_integrity(full=True)' in source
