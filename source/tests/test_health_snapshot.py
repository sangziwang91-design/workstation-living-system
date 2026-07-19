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


def test_commercial_readiness_runbook_uses_install_receipt_source_root(
    tmp_path, monkeypatch
) -> None:
    install_root = tmp_path / "install"
    source_root = tmp_path / "source-root"
    config = default_config(install_root / "home")
    runtime = LivingSystem(config)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    docs = source_root / "docs"
    docs.mkdir(parents=True)
    (docs / "COMMERCIAL_READINESS_GATES.md").write_text("# gates\n", encoding="utf-8")
    (install_root / "INSTALL_RECEIPT_UI_20260715.json").write_text(
        json.dumps(
            {
                "install_root": str(install_root),
                "source_root": str(source_root),
            }
        ),
        encoding="utf-8-sig",
    )
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
    assert receipt["rc"]["gates"]["readiness_runbook_present"] is True


def test_external_product_audit_passes_single_user_scope(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    wheel = tmp_path / "dist" / "workstation_living_system-0.9.0.dev2-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"wheel")
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in (
        "COMMERCIAL_READINESS_GATES.md",
        "EXTERNAL_SINGLE_USER_DELIVERY.md",
        "SUPPORT_AND_DIAGNOSTICS.md",
        "SECURITY_BOUNDARY.md",
        "RECOVERY_RUNBOOK.md",
    ):
        (docs / name).write_text("# doc\n", encoding="utf-8")
    runtime.db.set_runtime(
        "commercial_readiness_audit_last",
        {"audit_id": "commercial_ok", "status": "RC_PASSED"},
    )
    runtime.db.set_runtime(
        "performance_budget_last",
        {"audit_id": "performance_ok", "status": "PERFORMANCE_BUDGET_PASSED"},
    )
    runtime.db.set_runtime(
        "retention_audit_last",
        {"audit_id": "retention_ok", "status": "RETENTION_AUDIT_PASSED"},
    )
    runtime.db.set_runtime(
        "upgrade_drill_last",
        {
            "drill_id": "upgrade_ok",
            "status": "UPGRADE_DRILL_PASSED",
            "disposable_clone_executed": True,
            "disposable_clone_rollback": {"passed": True},
        },
    )

    receipt = runtime.record_external_product_audit(
        reason="unit test external product",
        wheel_path=wheel,
        exclude_multi_user=True,
    )

    assert receipt["status"] == "EXTERNAL_SINGLE_USER_READY"
    assert receipt["missing"] == []
    assert receipt["scope"]["multi_user_or_regional_tenant_module"] == (
        "EXCLUDED_BY_OWNER"
    )
    assert runtime.health_snapshot()["external_product"]["audit_id"] == receipt[
        "audit_id"
    ]


def test_external_product_audit_finds_docs_and_wheel_from_install_receipt(
    tmp_path, monkeypatch
) -> None:
    install_root = tmp_path / "install"
    source_root = tmp_path / "source-root"
    config = default_config(install_root / "home")
    runtime = LivingSystem(config)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    docs = source_root / "docs"
    docs.mkdir(parents=True)
    for name in (
        "COMMERCIAL_READINESS_GATES.md",
        "EXTERNAL_SINGLE_USER_DELIVERY.md",
        "SUPPORT_AND_DIAGNOSTICS.md",
        "SECURITY_BOUNDARY.md",
        "RECOVERY_RUNBOOK.md",
    ):
        (docs / name).write_text("# doc\n", encoding="utf-8")
    wheel = source_root / "dist" / "workstation_living_system-0.9.0.dev2-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"wheel")
    (install_root / "INSTALL_RECEIPT_UI_20260715.json").write_text(
        json.dumps(
            {
                "install_root": str(install_root),
                "source_root": str(source_root),
                "wheel": str(wheel),
            }
        ),
        encoding="utf-8-sig",
    )
    runtime.db.set_runtime(
        "commercial_readiness_audit_last",
        {"audit_id": "commercial_ok", "status": "RC_PASSED"},
    )
    runtime.db.set_runtime(
        "performance_budget_last",
        {"audit_id": "performance_ok", "status": "PERFORMANCE_BUDGET_PASSED"},
    )
    runtime.db.set_runtime(
        "retention_audit_last",
        {"audit_id": "retention_ok", "status": "RETENTION_AUDIT_PASSED"},
    )
    runtime.db.set_runtime(
        "upgrade_drill_last",
        {
            "drill_id": "upgrade_ok",
            "status": "UPGRADE_DRILL_PASSED",
            "disposable_clone_executed": True,
            "disposable_clone_rollback": {"passed": True},
        },
    )

    receipt = runtime.record_external_product_audit(
        reason="unit test install receipt discovery"
    )

    assert receipt["status"] == "EXTERNAL_SINGLE_USER_READY"
    assert receipt["missing"] == []
    assert receipt["evidence"]["wheel"]["path"] == str(wheel.resolve())
    assert receipt["evidence"]["wheel"]["version_matches_runtime"] is True
    assert receipt["evidence"]["docs"]["security_boundary"] == str(
        docs / "SECURITY_BOUNDARY.md"
    )


def test_external_product_audit_blocks_wheel_version_mismatch(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    wheel = tmp_path / "dist" / "workstation_living_system-0.1.0-py3-none-any.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"wheel")
    runtime.db.set_runtime(
        "commercial_readiness_audit_last",
        {"audit_id": "commercial_ok", "status": "RC_PASSED"},
    )
    runtime.db.set_runtime(
        "performance_budget_last",
        {"audit_id": "performance_ok", "status": "PERFORMANCE_BUDGET_PASSED"},
    )
    runtime.db.set_runtime(
        "retention_audit_last",
        {"audit_id": "retention_ok", "status": "RETENTION_AUDIT_PASSED"},
    )
    runtime.db.set_runtime(
        "upgrade_drill_last",
        {
            "drill_id": "upgrade_ok",
            "status": "UPGRADE_DRILL_PASSED",
            "disposable_clone_executed": True,
            "disposable_clone_rollback": {"passed": True},
        },
    )

    receipt = runtime.record_external_product_audit(
        reason="unit test wheel mismatch",
        wheel_path=wheel,
    )

    assert receipt["status"] == "EXTERNAL_SINGLE_USER_BLOCKED"
    assert "wheel_version_matches_runtime" in receipt["missing"]
    assert receipt["evidence"]["wheel"]["version"] == "0.1.0"


def test_external_product_audit_blocks_when_multi_user_is_required(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    receipt = runtime.record_external_product_audit(
        reason="unit test external product block",
        exclude_multi_user=False,
    )

    assert receipt["status"] == "EXTERNAL_SINGLE_USER_BLOCKED"
    assert "multi_user_scope_excluded" in receipt["missing"]
    assert receipt["scope"]["multi_user_or_regional_tenant_module"] == (
        "REQUIRED_AND_NOT_IMPLEMENTED"
    )


def test_m7_self_check_blocks_without_reference_root(tmp_path) -> None:
    install_root = tmp_path / "install"
    source_root = tmp_path / "source-root"
    runtime = _m7_ready_runtime(install_root, source_root)

    receipt = runtime.record_m7_self_check_audit(
        reason="unit test M7 missing reference",
        m7_root=tmp_path / "missing-m7",
    )

    assert receipt["status"] == "M7_BLOCKED"
    assert "m7_reference_found" in receipt["hard_gate_result"]["missing"]


def test_m7_self_check_passes_personal_single_user_gate(tmp_path) -> None:
    install_root = tmp_path / "install"
    source_root = tmp_path / "source-root"
    runtime = _m7_ready_runtime(install_root, source_root)
    m7_root = _write_m7_reference(tmp_path / "M7家族")

    receipt = runtime.record_m7_self_check_audit(
        reason="unit test M7 ready",
        m7_root=m7_root,
    )

    assert receipt["status"] == "M7_PERSONAL_SINGLE_USER_READY"
    assert receipt["hard_gate_result"]["missing"] == []
    assert receipt["final_level"] == "M7_PERSONAL_CANDIDATE"
    assert receipt["evidence"]["qualified_measurement_count"] == 8
    assert receipt["profile"]["excluded_profile"] == "multiparty_enterprise"
    assert runtime.health_snapshot()["m7_self_check"]["audit_id"] == receipt[
        "audit_id"
    ]


def _m7_ready_runtime(install_root, source_root) -> LivingSystem:
    config = default_config(install_root / "home")
    runtime = LivingSystem(config)
    _write_m7_source_evidence(source_root)
    (install_root / "INSTALL_RECEIPT_UI_20260715.json").write_text(
        json.dumps(
            {
                "install_root": str(install_root),
                "source_root": str(source_root),
            }
        ),
        encoding="utf-8-sig",
    )
    runtime.db.set_runtime("cycle_count", 1)
    runtime.db.set_runtime(
        "commercial_readiness_audit_last",
        {"audit_id": "commercial_ready", "status": "RC_PASSED"},
    )
    runtime.db.set_runtime(
        "external_product_audit_last",
        {
            "audit_id": "external_ready",
            "status": "EXTERNAL_SINGLE_USER_READY",
            "evidence": {
                "wheel": {"version_matches_runtime": True},
                "docs": {
                    "commercial_readiness_gates": "doc",
                    "external_delivery_runbook": "doc",
                    "support_runbook": "doc",
                    "security_boundary": "doc",
                    "recovery_runbook": "doc",
                },
            },
        },
    )
    runtime.db.set_runtime(
        "longitudinal_report_last",
        {"report_id": "long_ready", "status": "LONGITUDINAL_REPORT_PASSED"},
    )
    classes = [
        "release-gate-repair",
        "commercial-readiness-gate",
        "static-quality-remediation",
        "installed-package-validation",
        "ui-owner-console-validation",
        "runtime-health-verification",
        "acceptance-regression-validation",
        "commercial-readiness-iteration",
    ]
    runtime.db.set_runtime(
        "longitudinal_measurement_receipts",
        [
            {
                "measurement_id": f"m7_meas_{index}",
                "protocol_id": "long_m7",
                "task_class": task_class,
                "task_reference": f"m7-task-{index}",
                "owner_review": "accepted",
            }
            for index, task_class in enumerate(classes)
        ],
    )
    runtime.db.set_runtime(
        "upgrade_drill_last",
        {
            "drill_id": "upgrade_ready",
            "status": "UPGRADE_DRILL_PASSED",
            "disposable_clone_executed": True,
            "disposable_clone_rollback": {"passed": True},
        },
    )
    runtime.db.set_runtime(
        "performance_budget_last",
        {"audit_id": "performance_ready", "status": "PERFORMANCE_BUDGET_PASSED"},
    )
    runtime.db.set_runtime(
        "retention_audit_last",
        {"audit_id": "retention_ready", "status": "RETENTION_AUDIT_PASSED"},
    )
    runtime.db.set_runtime(
        "garbage_audit_last",
        {"audit_id": "garbage_ready", "status": "CLEAN"},
    )
    return runtime


def _write_m7_reference(root):
    files = [
        "M7_Runtime_Gate_v2.0_optimized.md",
        "M7_Runtime_Gate_Master_Library_v1.2_absorbed.md",
        "m7-runtime-gate-template/07_evolution/m7_gate_result.yaml",
        "m7-runtime-gate-template/05_risk/risk_gate.yaml",
        "m7-runtime-gate-template/03_authority/permission_matrix.yaml",
        "m7-runtime-gate-template/tests/hash_chain_tests.md",
        "m7-runtime-gate-template/tests/conflict_lock_tests.md",
        "m7-runtime-gate-template/tests/permission_tests.md",
    ]
    for relative in files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("M7 gate evidence\n", encoding="utf-8")
    return root


def _write_m7_source_evidence(root) -> None:
    payloads = {
        "source/tests/test_agentic_harness.py": (
            "test_high_risk_node_waits_for_approval_without_lease\n"
            "test_worker_registry_rejects_unknown_and_overrisk_workers\n"
            "policy approval missing\n"
        ),
        "source/tests/test_garbage_audit.py": (
            "test_garbage_cleanup_requires_approval_reference\n"
            "test_cli_garbage_clear_quarantine_requires_approval_reference\n"
        ),
        "source/tests/test_keyfiles.py": (
            "test_approval_key_round_trips_binary_bytes_across_restart\n"
        ),
        "source/src/wls/task_admission.py": (
            "owner_gate\nRiskLevel.IRREVERSIBLE\n"
        ),
        "source/src/wls/task_classifier.py": (
            "effective_risk\nSideEffectClass.IRREVERSIBLE\n"
        ),
        "source/src/wls/runtime.py": (
            "requires_approval\nUNKNOWN_SIDE_EFFECT\ndef pause\ndef kill\n"
            "def freeze_holdout_epoch\nidempotency_key\n"
        ),
        "source/tests/test_life_campaign_30.py": (
            "test_campaign_lock_is_exclusive\nPAUSED\nKILLED\n"
        ),
        "source/src/wls/db.py": "idx_actions_idempotency_success\n",
        "source/tests/test_living_agent_os_capabilities.py": (
            "freeze_holdout_epoch\n"
        ),
        "source/src/wls/merge_node.py": "UNRESOLVED\n",
        "source/src/wls/reviewer.py": "confidence\n",
        "source/src/wls/cognition.py": "minimum_confidence\n",
        "pyproject.toml": (
            'license = "MIT"\n'
            "dependencies = []\n"
            'wls-ui = "wls.ui_server:main"\n'
        ),
    }
    for relative, text in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")



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
