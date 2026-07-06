from __future__ import annotations


from wls.acceptance import (
    AcceptanceOracle,
    AcceptanceReport,
    AcceptanceVerdict,
    CheckResult,
    MissionAcceptance,
    hash_check,
    path_existence_check,
    regression_check,
    schema_check,
    threshold_check,
)


class TestAcceptanceOracle:
    def test_empty_oracle_passes(self):
        oracle = AcceptanceOracle()
        report = oracle.evaluate("target-1", "node")
        assert report.verdict == AcceptanceVerdict.PASS
        assert report.checks == []

    def test_oracle_collects_all_checks(self):
        oracle = AcceptanceOracle()
        oracle.register("alpha", "test", lambda ctx: CheckResult(
            check_id="c1", name="alpha", kind="test", verdict=AcceptanceVerdict.PASS))
        oracle.register("beta", "test", lambda ctx: CheckResult(
            check_id="c2", name="beta", kind="test", verdict=AcceptanceVerdict.FAIL_ADVISORY))
        report = oracle.evaluate("t", "node")
        assert len(report.checks) == 2
        assert report.verdict == AcceptanceVerdict.FAIL_ADVISORY

    def test_critical_failure_dominates(self):
        oracle = AcceptanceOracle()
        oracle.register("a", "test", lambda ctx: CheckResult(
            check_id="c1", name="a", kind="test", verdict=AcceptanceVerdict.PASS))
        oracle.register("b", "test", lambda ctx: CheckResult(
            check_id="c2", name="b", kind="test", verdict=AcceptanceVerdict.FAIL_CRITICAL))
        report = oracle.evaluate("t", "node")
        assert report.verdict == AcceptanceVerdict.FAIL_CRITICAL

    def test_check_error_becomes_blocked(self):
        oracle = AcceptanceOracle()
        def _raise(_ctx):
            raise RuntimeError("boom")
        oracle.register("err", "test", _raise)
        report = oracle.evaluate("t", "node")
        assert report.verdict == AcceptanceVerdict.BLOCKED
        assert "boom" in report.checks[0].detail

    def test_critical_failures_method(self):
        oracle = AcceptanceOracle()
        oracle.register("a", "test", lambda ctx: CheckResult(
            check_id="c1", name="a", kind="test", verdict=AcceptanceVerdict.FAIL_CRITICAL))
        oracle.register("b", "test", lambda ctx: CheckResult(
            check_id="c2", name="b", kind="test", verdict=AcceptanceVerdict.PASS))
        report = oracle.evaluate("t", "node")
        assert len(report.critical_failures()) == 1

    def test_advisory_failures_method(self):
        oracle = AcceptanceOracle()
        oracle.register("a", "test", lambda ctx: CheckResult(
            check_id="c1", name="a", kind="test", verdict=AcceptanceVerdict.FAIL_ADVISORY))
        report = oracle.evaluate("t", "node")
        assert len(report.advisory_failures()) == 1

    def test_to_dict(self):
        report = AcceptanceReport(
            report_id="r1",
            target_id="t1",
            target_kind="node",
            verdict=AcceptanceVerdict.PASS,
            checks=[CheckResult(
                check_id="c1", name="alpha", kind="test",
                verdict=AcceptanceVerdict.PASS, detail="ok")],
        )
        d = report.to_dict()
        assert d["report_id"] == "r1"
        assert d["verdict"] == "PASS"
        assert len(d["checks"]) == 1
        assert d["checks"][0]["kind"] == "test"


class TestSchemaCheck:
    def test_pass(self):
        r = schema_check({"a": 1, "b": 2}, ["a", "b"])
        assert r.verdict == AcceptanceVerdict.PASS

    def test_missing_key(self):
        r = schema_check({"a": 1}, ["a", "b"])
        assert r.verdict == AcceptanceVerdict.FAIL_CRITICAL
        assert "b" in r.detail

    def test_not_dict(self):
        r = schema_check("string", ["a"])
        assert r.verdict == AcceptanceVerdict.FAIL_CRITICAL
        assert "not a dict" in r.detail


class TestHashCheck:
    def test_pass(self):
        from wls.schemas import digest_json
        data = {"k": "v"}
        digest = digest_json(data)
        r = hash_check(digest, data)
        assert r.verdict == AcceptanceVerdict.PASS

    def test_fail(self):
        r = hash_check("abc123", {"x": 1})
        assert r.verdict == AcceptanceVerdict.FAIL_CRITICAL


class TestThresholdCheck:
    def test_le_pass(self):
        r = threshold_check("cpu", 0.5, 0.8, "le")
        assert r.verdict == AcceptanceVerdict.PASS

    def test_le_fail(self):
        r = threshold_check("cpu", 0.9, 0.8, "le")
        assert r.verdict == AcceptanceVerdict.FAIL_CRITICAL

    def test_ge_pass(self):
        r = threshold_check("score", 0.95, 0.9, "ge")
        assert r.verdict == AcceptanceVerdict.PASS

    def test_unknown_operator(self):
        r = threshold_check("x", 1.0, 1.0, "unknown")
        assert r.verdict == AcceptanceVerdict.BLOCKED


class TestRegressionCheck:
    def test_no_regression(self):
        r = regression_check(0.9, 0.85)
        assert r.verdict == AcceptanceVerdict.PASS

    def test_within_tolerance(self):
        r = regression_check(0.84, 0.85, max_regression=0.02)
        assert r.verdict == AcceptanceVerdict.PASS

    def test_regression(self):
        r = regression_check(0.7, 0.85)
        assert r.verdict == AcceptanceVerdict.FAIL_CRITICAL


class TestPathExistenceCheck:
    def test_existing_paths(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f1.write_text("hello")
        r = path_existence_check([str(f1)])
        assert r.verdict == AcceptanceVerdict.PASS

    def test_missing_path(self):
        r = path_existence_check(["/nonexistent/path/12345"])
        assert r.verdict == AcceptanceVerdict.FAIL_CRITICAL


class TestMissionAcceptance:
    def test_aggregate_all_pass(self):
        oracle = AcceptanceOracle()
        oracle.register("a", "test", lambda ctx: CheckResult(
            check_id="c1", name="a", kind="test", verdict=AcceptanceVerdict.PASS))
        mission = MissionAcceptance(oracle=oracle, mission_id="m1")
        report = oracle.evaluate("n1", "node")
        mission.add_node_result("n1", report)
        agg = mission.aggregate()
        assert agg.verdict == AcceptanceVerdict.PASS
        assert "r1" not in agg.evidence_refs  # uses generated ids

    def test_aggregate_critical(self):
        oracle = AcceptanceOracle()
        oracle.register("f", "test", lambda ctx: CheckResult(
            check_id="c2", name="f", kind="test", verdict=AcceptanceVerdict.FAIL_CRITICAL))
        mission = MissionAcceptance(oracle=oracle, mission_id="m2")
        report = oracle.evaluate("n2", "node")
        mission.add_node_result("n2", report)
        agg = mission.aggregate()
        assert agg.verdict == AcceptanceVerdict.FAIL_CRITICAL
