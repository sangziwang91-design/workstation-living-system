from __future__ import annotations

from wls.longitudinal import LongitudinalEvaluator, LongitudinalProtocol, MeasurementPoint


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
