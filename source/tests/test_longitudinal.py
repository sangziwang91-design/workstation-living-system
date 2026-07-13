from __future__ import annotations

import json

from wls import cli
from wls.config import default_config
from wls.longitudinal import LongitudinalEvaluator, MeasurementPoint
from wls.runtime import LivingSystem


class TestLongitudinal:
    def test_start_protocol(self):
        evaluator = LongitudinalEvaluator()
        protocol = evaluator.start_protocol(
            "host-1", "abc123def", {"read_only": True},
            duration_days=30,
        )
        assert protocol.host_id == "host-1"
        assert protocol.baseline_commit == "abc123def"
        assert protocol.duration_days == 30
        assert protocol.frozen_baseline

    def test_record_measurement(self):
        evaluator = LongitudinalEvaluator()
        protocol = evaluator.start_protocol("h1", "abc", {})
        m = evaluator.record_measurement(
            protocol, "coding",
            success=True, corrections=1, cost=0.05,
            latency=3.0, memory_benefit=True, skill_reuse=True,
        )
        assert m.success
        assert m.memory_benefit
        assert m.skill_reuse
        assert m.correction_count == 1

    def test_compile_report(self):
        evaluator = LongitudinalEvaluator()
        protocol = evaluator.start_protocol("h1", "abc", {})
        measurements = [
            MeasurementPoint("m1", protocol.protocol_id, "coding", True, 0, 0.01, 2.0, True, True),
            MeasurementPoint("m2", protocol.protocol_id, "coding", False, 2, 0.02, 5.0, False, False),
        ]
        report = evaluator.compile_report(
            protocol, measurements,
            baseline_success_rate=0.8,
            claim_ceiling="workload-specific only",
        )
        assert report.success_rate == 0.5
        assert report.avg_correction == 1.0
        assert len(report.regressions) > 0  # below baseline

    def test_compile_report_empty(self):
        evaluator = LongitudinalEvaluator()
        protocol = evaluator.start_protocol("h1", "abc", {})
        report = evaluator.compile_report(protocol, [])
        assert report.success_rate == 0.0
        assert "no measurements" in report.claim_ceiling

    def test_compile_report_no_regression(self):
        evaluator = LongitudinalEvaluator()
        protocol = evaluator.start_protocol("h1", "abc", {})
        measurements = [
            MeasurementPoint("m1", protocol.protocol_id, "coding", True, 0, 0.01, 1.0, True, True),
        ]
        report = evaluator.compile_report(
            protocol, measurements, baseline_success_rate=0.5,
        )
        assert len(report.regressions) == 0

    def test_report_to_dict(self):
        evaluator = LongitudinalEvaluator()
        protocol = evaluator.start_protocol("h1", "abc", {})
        measurements = [
            MeasurementPoint("m1", protocol.protocol_id, "coding", True, 0, 0.01, 1.0, True, True),
        ]
        report = evaluator.compile_report(protocol, measurements, claim_ceiling="test")
        d = report.to_dict()
        assert d["success_rate"] == 1.0
        assert d["measurement_count"] == 1


def test_runtime_records_longitudinal_receipts(tmp_path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))

    protocol = runtime.start_longitudinal_protocol(
        host_id="owner-host",
        baseline_commit="abc123",
        duration_days=99999,
        reason="unit test longitudinal start",
    )
    measurement = runtime.record_longitudinal_measurement(
        protocol_id=protocol["protocol_id"],
        task_class="coding",
        success=True,
        corrections=1,
        cost=0.0,
        latency_seconds=42.0,
        memory_benefit=True,
        skill_reuse=False,
        task_reference="pytest://task" * 100,
        owner_review="completed with one correction" * 100,
        reason="unit test longitudinal measurement",
    )
    report = runtime.compile_longitudinal_report(
        protocol_id=protocol["protocol_id"],
        baseline_success_rate=0.5,
        reason="unit test longitudinal report",
    )

    assert measurement["protocol_id"] == protocol["protocol_id"]
    assert protocol["duration_days"] == 3650
    assert len(measurement["task_reference"]) == 500
    assert len(measurement["owner_review"]) == 1000
    assert report["status"] == "LONGITUDINAL_REPORT_PASSED"
    assert report["measurement_count"] == 1
    assert runtime.health_snapshot()["longitudinal_report"]["report_id"] == report["report_id"]


def test_cli_longitudinal_protocol_record_and_report(tmp_path, capsys) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "longitudinal-start",
                "--host-id",
                "owner-host",
                "--baseline-commit",
                "abc123",
                "--reason",
                "unit test cli longitudinal start",
            ]
        )
        == 0
    )
    protocol = json.loads(capsys.readouterr().out)

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "longitudinal-record",
                "--protocol-id",
                protocol["protocol_id"],
                "--task-class",
                "coding",
                "--success",
                "--latency-seconds",
                "3.5",
                "--task-reference",
                "pytest://owner-task",
                "--owner-review",
                "completed with review evidence",
                "--reason",
                "unit test cli longitudinal record",
            ]
        )
        == 0
    )
    measurement = json.loads(capsys.readouterr().out)
    assert measurement["success"] is True

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "longitudinal-report",
                "--protocol-id",
                protocol["protocol_id"],
                "--baseline-success-rate",
                "0.5",
                "--reason",
                "unit test cli longitudinal report",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "LONGITUDINAL_REPORT_PASSED"
    assert report["qualified_measurement_count"] == 1


def test_longitudinal_report_parses_string_booleans_from_legacy_receipts(tmp_path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    protocol = runtime.start_longitudinal_protocol(
        host_id="owner-host",
        baseline_commit="abc123",
        reason="unit test longitudinal legacy bools",
    )
    runtime.db.set_runtime(
        "longitudinal_measurement_receipts",
        [
            {
                "point_id": "meas_legacy",
                "protocol_id": protocol["protocol_id"],
                "task_class": "coding",
                "success": "false",
                "correction_count": 0,
                "cost": 0,
                "latency_seconds": 1,
                "memory_benefit": "false",
                "skill_reuse": "true",
                "recorded_at": "2026-07-10T00:00:00+00:00",
            }
        ],
    )

    report = runtime.compile_longitudinal_report(
        protocol_id=protocol["protocol_id"],
        baseline_success_rate=1.0,
        reason="unit test compile legacy bools",
    )

    assert report["status"] == "LONGITUDINAL_REPORT_NEEDS_MORE_EVIDENCE"
    assert report["success_rate"] == 0.0
    assert report["skill_reuse_rate"] == 1.0
    assert report["qualified_measurement_count"] == 0


def test_longitudinal_report_requires_owner_task_evidence(tmp_path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    protocol = runtime.start_longitudinal_protocol(
        host_id="owner-host",
        baseline_commit="abc123",
        reason="unit test longitudinal quality gate",
    )
    measurement = runtime.record_longitudinal_measurement(
        protocol_id=protocol["protocol_id"],
        task_class="commercial-readiness",
        success=True,
        reason="unit test partial owner evidence",
    )
    report = runtime.compile_longitudinal_report(
        protocol_id=protocol["protocol_id"],
        baseline_success_rate=0.5,
        reason="unit test report partial owner evidence",
    )

    assert measurement["evidence_quality"] == "PARTIAL_OWNER_TASK"
    assert measurement["owner_evidence_complete"] is False
    assert report["measurement_count"] == 1
    assert report["qualified_measurement_count"] == 0
    assert report["unqualified_measurement_count"] == 1
    assert report["status"] == "LONGITUDINAL_REPORT_NEEDS_MORE_EVIDENCE"
