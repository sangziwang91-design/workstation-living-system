import pytest
import tempfile
import json
from pathlib import Path
from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, RiskLevel, ActionStatus
from datetime import datetime, UTC, timedelta

@pytest.fixture
def isolated_runtime():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        config.secret_path.write_bytes(b"a" * 32)
        config.home_path.joinpath("secrets").mkdir(parents=True, exist_ok=True)
        config.home_path.joinpath("secrets/approval.key").write_bytes(b"b" * 32)

        runtime = LivingSystem(config)
        yield runtime

def test_approval_expires(isolated_runtime):
    action = ActionSpec(
        tool="noop",
        arguments={},
        purpose="test",
        expected_result="ok",
        risk=RiskLevel.HIGH,
        status=ActionStatus.WAITING_APPROVAL
    )
    isolated_runtime.db.execute(
        "INSERT INTO plans (plan_id, cycle_id, plan_json, status, created_at) VALUES (?, ?, ?, ?, ?)",
        ("p1", "c1", "{}", "RUNNING", "2024-01-01T00:00:00Z")
    )
    isolated_runtime.db.execute(
        "INSERT INTO actions (action_id, plan_id, tool, arguments_json, purpose, expected_result, risk, acceptance_json, idempotency_key, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (action.action_id, "p1", action.tool, json.dumps(action.arguments), action.purpose, action.expected_result, action.risk.value, "[]", action.idempotency_key, action.status.value)
    )

    approval_id = isolated_runtime.approvals.issue(action.action_id, True, ttl_minutes=1)
    isolated_runtime.db.execute("UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
                 ((datetime.now(UTC) - timedelta(hours=1)).isoformat(), approval_id))

    assert isolated_runtime.approvals.validate_and_consume(action, approval_id) is False

def test_approval_consumed_once(isolated_runtime):
    action = ActionSpec(tool="noop", arguments={}, purpose="test", expected_result="ok", risk=RiskLevel.HIGH, status=ActionStatus.WAITING_APPROVAL)
    isolated_runtime.db.execute("INSERT INTO plans (plan_id, cycle_id, plan_json, status, created_at) VALUES ('p1', 'c1', '{}', 'RUNNING', '2024-01-01T00:00:00Z')")
    isolated_runtime.db.execute(
        "INSERT INTO actions (action_id, plan_id, tool, arguments_json, purpose, expected_result, risk, acceptance_json, idempotency_key, status) VALUES (?, 'p1', 'noop', '{}', 'test', 'ok', 'HIGH', '[]', ?, 'WAITING_APPROVAL')",
        (action.action_id, action.idempotency_key)
    )

    aid = isolated_runtime.approvals.issue(action.action_id, True)
    assert isolated_runtime.approvals.validate_and_consume(action, aid) is True
    assert isolated_runtime.approvals.validate_and_consume(action, aid) is False

def test_approval_rejects_modified_action(isolated_runtime):
    action = ActionSpec(tool="noop", arguments={"p": 1}, purpose="test", expected_result="ok", risk=RiskLevel.HIGH, status=ActionStatus.WAITING_APPROVAL)
    isolated_runtime.db.execute("INSERT INTO plans (plan_id, cycle_id, plan_json, status, created_at) VALUES ('p1', 'c1', '{}', 'RUNNING', '2024-01-01T00:00:00Z')")
    isolated_runtime.db.execute(
        "INSERT INTO actions (action_id, plan_id, tool, arguments_json, purpose, expected_result, risk, acceptance_json, idempotency_key, status) VALUES (?, 'p1', 'noop', ?, 'test', 'ok', 'HIGH', '[]', ?, 'WAITING_APPROVAL')",
        (action.action_id, json.dumps(action.arguments), action.idempotency_key)
    )

    aid = isolated_runtime.approvals.issue(action.action_id, True)

    modified_action = ActionSpec(action_id=action.action_id, tool="noop", arguments={"p": 2}, purpose="test", expected_result="ok", risk=RiskLevel.HIGH)
    assert isolated_runtime.approvals.validate_and_consume(modified_action, aid) is False
