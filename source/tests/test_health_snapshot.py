from __future__ import annotations

import json

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import WorkspaceItem


def test_health_snapshot_flags_database_over_daemon_limit(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    config.daemon_max_database_bytes = 1
    runtime = LivingSystem(config)

    health = runtime.health_snapshot()

    assert health["status"] == "BLOCKED"
    assert health["database"]["over_daemon_limit"] is True
    assert "database_size_over_daemon_limit" in health["critical"]
    assert health["locks"]["runtime_lock_present"] is False
    assert health["locks"]["runtime_lock_held"] is False
    assert health["pending_action_count"] == 0


def test_health_snapshot_does_not_create_lock_files(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    runtime_lock = config.home_path / "state" / "runtime.lock"
    daemon_lock = config.home_path / "state" / "daemon.lock"

    health = runtime.health_snapshot()

    assert health["status"] == "OK"
    assert runtime_lock.exists() is False
    assert daemon_lock.exists() is False


def test_health_snapshot_warns_about_stale_lock_files(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    runtime_lock = config.home_path / "state" / "runtime.lock"
    runtime_lock.write_text("999999", encoding="ascii")

    health = runtime.health_snapshot()

    assert health["status"] == "WARN"
    assert health["locks"]["runtime_lock_present"] is True
    assert health["locks"]["runtime_lock_held"] is False
    assert "stale_runtime_lock" in health["warnings"]


def test_daemon_stops_before_cycle_when_health_is_blocked(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    config.daemon_max_database_bytes = 1
    runtime = LivingSystem(config)

    def fail_if_called() -> None:
        raise AssertionError("daemon should stop before running a cycle")

    runtime.run_cycle = fail_if_called  # type: ignore[method-assign]

    runtime.run_daemon(max_cycles=1)

    stop = runtime.db.get_runtime("daemon_last_stop")
    assert stop["status"] == "BLOCKED"
    assert stop["critical"] == ["database_size_over_daemon_limit"]


def test_runtime_initialization_recovers_interrupted_cycles(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    runtime.db.execute(
        """
        INSERT INTO cycles(cycle_id,started_at,finished_at,status,workspace_json,metrics_json,error)
        VALUES ('cycle-stale','2026-07-10T00:00:00+00:00',NULL,'RUNNING','{}','{}',NULL)
        """
    )
    runtime.db.close_all()

    recovered = LivingSystem(config)
    row = recovered.db.query_one(
        "SELECT status,error FROM cycles WHERE cycle_id='cycle-stale'"
    )

    assert row["status"] == "INTERRUPTED"
    assert "mid-cycle" in row["error"]


def test_health_snapshot_accepts_succeeded_latest_cycle(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    runtime.db.execute(
        """
        INSERT INTO cycles(cycle_id,started_at,finished_at,status,workspace_json,metrics_json,error)
        VALUES ('cycle-ok','2026-07-10T00:00:00+00:00','2026-07-10T00:00:01+00:00','SUCCEEDED','[]','{}',NULL)
        """
    )

    health = runtime.health_snapshot()

    assert health["status"] == "OK"
    assert health["warnings"] == []


def test_health_snapshot_falls_back_to_latest_receipts(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    runtime.db.set_runtime(
        "garbage_audit_receipts",
        [{"audit_id": "garbage_audit_legacy", "status": "CLEAN"}],
    )
    runtime.db.set_runtime(
        "performance_budget_receipts",
        [{"audit_id": "performance_budget_legacy", "status": "PASSED"}],
    )
    runtime.db.set_runtime(
        "bounded_soak_receipts",
        [{"audit_id": "bounded_soak_legacy", "status": "PASSED"}],
    )
    runtime.db.set_runtime(
        "upgrade_drill_receipts",
        [{"drill_id": "upgrade_drill_legacy", "status": "PASSED"}],
    )
    runtime.db.set_runtime(
        "longitudinal_report_receipts",
        [{"report_id": "long_report_legacy", "status": "PASSED"}],
    )
    runtime.db.set_runtime(
        "retention_audit_receipts",
        [{"audit_id": "retention_audit_legacy", "status": "PASSED"}],
    )

    health = runtime.health_snapshot()

    assert health["garbage_audit"]["audit_id"] == "garbage_audit_legacy"
    assert health["performance_budget"]["audit_id"] == "performance_budget_legacy"
    assert health["bounded_soak"]["audit_id"] == "bounded_soak_legacy"
    assert health["upgrade_drill"]["drill_id"] == "upgrade_drill_legacy"
    assert health["longitudinal_report"]["report_id"] == "long_report_legacy"
    assert health["retention_audit"]["audit_id"] == "retention_audit_legacy"


def test_commercial_readiness_audit_separates_beta_from_rc(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    runtime.db.set_runtime(
        "garbage_audit_receipts",
        [{"audit_id": "garbage_audit_ok", "status": "CLEAN"}],
    )
    runtime.db.set_runtime(
        "performance_budget_receipts",
        [{"audit_id": "performance_budget_ok", "status": "PERFORMANCE_BUDGET_PASSED"}],
    )
    runtime.db.set_runtime(
        "bounded_soak_receipts",
        [{"audit_id": "bounded_soak_ok", "status": "BOUNDED_SOAK_PASSED"}],
    )
    runtime.db.set_runtime(
        "upgrade_drill_receipts",
        [{"drill_id": "upgrade_drill_ok", "status": "UPGRADE_DRILL_PASSED"}],
    )
    runtime.db.set_runtime(
        "retention_audit_receipts",
        [{"audit_id": "retention_audit_ok", "status": "RETENTION_AUDIT_PASSED"}],
    )
    runtime.db.set_runtime(
        "longitudinal_measurement_receipts",
        [
            {
                "measurement_id": "meas_1",
                "protocol_id": "long_1",
                "task_class": "commercial-readiness",
                "task_reference": "unit-task",
                "owner_review": "accepted",
            }
        ],
    )

    receipt = runtime.record_commercial_readiness_audit(reason="unit test")

    assert receipt["status"] == "BETA_PASSED_RC_BLOCKED"
    assert receipt["beta"]["status"] == "BETA_PASSED"
    assert receipt["rc"]["status"] == "RC_BLOCKED"
    assert "repeated_owner_task_measurements" in receipt["rc"]["missing"]
    assert receipt["evidence"]["qualified_measurement_count"] == 1


def test_commercial_readiness_audit_passes_rc_with_repeated_evidence(
    tmp_path, monkeypatch
) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    monkeypatch.chdir(tmp_path)
    runtime.db.set_runtime(
        "garbage_audit_receipts",
        [{"audit_id": "garbage_audit_ok", "status": "CLEAN"}],
    )
    runtime.db.set_runtime(
        "performance_budget_receipts",
        [{"audit_id": "performance_budget_ok", "status": "PERFORMANCE_BUDGET_PASSED"}],
    )
    runtime.db.set_runtime(
        "bounded_soak_receipts",
        [{"audit_id": "bounded_soak_ok", "status": "BOUNDED_SOAK_PASSED"}],
    )
    runtime.db.set_runtime(
        "upgrade_drill_receipts",
        [
            {
                "drill_id": "upgrade_drill_ok",
                "status": "UPGRADE_DRILL_PASSED",
                "disposable_clone_executed": True,
                "disposable_clone_rollback": {"passed": True},
            }
        ],
    )
    runtime.db.set_runtime(
        "retention_audit_receipts",
        [{"audit_id": "retention_audit_ok", "status": "RETENTION_AUDIT_PASSED"}],
    )
    runtime.db.set_runtime(
        "longitudinal_report_receipts",
        [{"report_id": "long_report_ok", "status": "LONGITUDINAL_REPORT_PASSED"}],
    )
    classes = ["maintenance", "cleanup", "rollback", "business"]
    runtime.db.set_runtime(
        "longitudinal_measurement_receipts",
        [
            {
                "measurement_id": f"meas_{index}",
                "protocol_id": "long_1",
                "task_class": classes[index % len(classes)],
                "task_reference": f"unit-task-{index}",
                "owner_review": "accepted",
            }
            for index in range(8)
        ],
    )

    receipt = runtime.record_commercial_readiness_audit(reason="unit test")

    assert receipt["status"] == "RC_PASSED"
    assert receipt["beta"]["status"] == "BETA_PASSED"
    assert receipt["rc"]["status"] == "RC_PASSED"
    assert receipt["evidence"]["qualified_measurement_count"] == 8
    assert runtime.health_snapshot()["commercial_readiness"]["audit_id"] == receipt[
        "audit_id"
    ]


def test_persisted_workspace_is_compacted() -> None:
    item = WorkspaceItem(
        item_type="memory",
        reference_id="mem-big",
        summary="large memory",
        salience=0.9,
        reasons=["test"],
        payload={"blob": "x" * 10000, "keep": "key"},
    )

    compact = LivingSystem._compact_workspace([item])
    encoded = json.dumps(compact, sort_keys=True)

    assert len(encoded) < 2500
    assert compact[0]["payload"]["truncated"] is True
    assert compact[0]["payload"]["digest"]
    assert compact[0]["payload"]["top_level_keys"] == ["blob", "keep"]
