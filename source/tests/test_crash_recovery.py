import pytest
import tempfile
from pathlib import Path
from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionStatus

def test_crash_recovery_read_only():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        # Ensure 32-byte keys
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        config.secret_path.write_bytes(b"a" * 32)
        config.home_path.joinpath("secrets").mkdir(parents=True, exist_ok=True)
        config.home_path.joinpath("secrets/approval.key").write_bytes(b"b" * 32)

        runtime = LivingSystem(config)

        # Simulate a running action
        # SQL: action_id, plan_id, goal_id, skill_id, tool, arguments_json, purpose, expected_result, risk, acceptance_json, idempotency_key, status, ...
        runtime.db.execute(
            "INSERT INTO plans (plan_id, cycle_id, plan_json, status, created_at) VALUES (?, ?, ?, ?, ?)",
            ("p1", "c1", "{}", "RUNNING", "2024-01-01T00:00:00Z")
        )
        runtime.db.execute(
            "INSERT INTO actions (action_id, plan_id, tool, arguments_json, purpose, expected_result, risk, acceptance_json, idempotency_key, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("a1", "p1", "noop", "{}", "test", "ok", "low", "{}", "k1", ActionStatus.RUNNING.value)
        )

        # Restart
        runtime2 = LivingSystem(config)
        res = runtime2.db.query_all("SELECT status FROM actions WHERE action_id = 'a1'")
        assert res[0]["status"] != ActionStatus.RUNNING.value

def test_crash_recovery_side_effect():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        config.secret_path.write_bytes(b"a" * 32)
        config.home_path.joinpath("secrets").mkdir(parents=True, exist_ok=True)
        config.home_path.joinpath("secrets/approval.key").write_bytes(b"b" * 32)

        runtime = LivingSystem(config)

        runtime.db.execute(
            "INSERT INTO plans (plan_id, cycle_id, plan_json, status, created_at) VALUES (?, ?, ?, ?, ?)",
            ("p1", "c1", "{}", "RUNNING", "2024-01-01T00:00:00Z")
        )
        runtime.db.execute(
            "INSERT INTO actions (action_id, plan_id, tool, arguments_json, side_effect_class, purpose, expected_result, risk, acceptance_json, idempotency_key, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("a2", "p1", "write_file", "{}", "external", "test", "ok", "high", "{}", "k2", ActionStatus.RUNNING.value)
        )

        runtime2 = LivingSystem(config)
        res = runtime2.db.query_all("SELECT status FROM actions WHERE action_id = 'a2'")
        # Side-effect actions should fail to prevent replay
        assert res[0]["status"] == 'UNKNOWN_SIDE_EFFECT'
