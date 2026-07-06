from __future__ import annotations

from wls.benchmark import (
    BenchmarkCase,
    BenchmarkResult,
    BenchmarkReport,
    BenchmarkSuite,
    compare_reports,
)


class TestBenchmarkResult:
    def test_success_rate_delta_improvement(self):
        bl = BenchmarkResult("c1", "t", "cat", passed=False, elapsed_seconds=1.0)
        cur = BenchmarkResult("c1", "t", "cat", passed=True, elapsed_seconds=0.5)
        assert cur.success_rate_delta(bl) == 1.0

    def test_success_rate_delta_regression(self):
        bl = BenchmarkResult("c1", "t", "cat", passed=True, elapsed_seconds=1.0)
        cur = BenchmarkResult("c1", "t", "cat", passed=False, elapsed_seconds=0.5)
        assert cur.success_rate_delta(bl) == -1.0

    def test_to_dict(self):
        r = BenchmarkResult("c1", "alpha", "coding", passed=True, elapsed_seconds=2.5,
                            metrics={"accuracy": 0.95})
        d = r.to_dict()
        assert d["case_id"] == "c1"
        assert d["metrics"]["accuracy"] == 0.95


class TestBenchmarkReport:
    def test_empty_report(self):
        r = BenchmarkReport("r1", "suite")
        assert r.total_cases() == 0
        assert r.success_rate() == 0.0

    def test_counts(self):
        r = BenchmarkReport("r1", "suite", results=[
            BenchmarkResult("c1", "a", "cat1", passed=True, elapsed_seconds=1.0),
            BenchmarkResult("c2", "b", "cat1", passed=False, elapsed_seconds=1.0),
            BenchmarkResult("c3", "c", "cat2", passed=True, elapsed_seconds=1.0),
        ])
        assert r.total_cases() == 3
        assert r.passed_cases() == 2
        assert r.failed_cases() == 1
        assert abs(r.success_rate() - 2 / 3) < 0.01

    def test_by_category(self):
        r = BenchmarkReport("r1", "suite", results=[
            BenchmarkResult("c1", "a", "cat1", passed=True, elapsed_seconds=1.0),
            BenchmarkResult("c2", "b", "cat2", passed=True, elapsed_seconds=1.0),
        ])
        groups = r.by_category()
        assert len(groups) == 2
        assert len(groups["cat1"]) == 1
        assert len(groups["cat2"]) == 1

    def test_category_summary(self):
        r = BenchmarkReport("r1", "suite", results=[
            BenchmarkResult("c1", "a", "cat1", passed=True, elapsed_seconds=1.0),
            BenchmarkResult("c2", "b", "cat1", passed=False, elapsed_seconds=1.0),
        ])
        summary = r.category_summary()
        assert summary["cat1"]["rate"] == 0.5


class TestBenchmarkSuite:
    def test_run_suite(self):
        suite = BenchmarkSuite("test-suite")
        suite.add(
            BenchmarkCase("c1", "alpha", "coding", input_data={"x": 1}),
            lambda d: {"passed": True, "metrics": {"score": 0.9}},
        )
        suite.add(
            BenchmarkCase("c2", "beta", "coding", input_data={"x": 2}),
            lambda d: {"passed": False, "metrics": {}},
        )
        report = suite.run()
        assert report.total_cases() == 2
        assert report.passed_cases() == 1

    def test_stop_on_failure(self):
        suite = BenchmarkSuite("test-suite")
        suite.add(
            BenchmarkCase("c1", "alpha", "coding", input_data={}),
            lambda d: {"passed": False},
        )
        suite.add(
            BenchmarkCase("c2", "beta", "coding", input_data={}),
            lambda d: {"passed": True},
        )
        report = suite.run(stop_on_failure=True)
        assert report.total_cases() == 1

    def test_runner_exception(self):
        suite = BenchmarkSuite("test-suite")
        suite.add(
            BenchmarkCase("c1", "alpha", "coding", input_data={}),
            lambda d: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        report = suite.run()
        assert report.failed_cases() == 1
        assert "boom" in report.results[0].error

    def test_discriminator(self):
        suite = BenchmarkSuite("suite", discriminator="v2")
        report = suite.run()
        assert "v2" in report.suite_name


class TestCompareReports:
    def test_no_regression(self):
        bl = BenchmarkReport("bl", "baseline", results=[
            BenchmarkResult("c1", "a", "cat", passed=True, elapsed_seconds=1.0),
        ])
        cur = BenchmarkReport("cur", "current", results=[
            BenchmarkResult("c1", "a", "cat", passed=True, elapsed_seconds=0.5),
        ])
        comp = compare_reports(bl, cur)
        assert not comp["has_regression"]
        assert comp["rate_delta"] == 0.0

    def test_regression_detected(self):
        bl = BenchmarkReport("bl", "baseline", results=[
            BenchmarkResult("c1", "a", "cat", passed=True, elapsed_seconds=1.0),
        ])
        cur = BenchmarkReport("cur", "current", results=[
            BenchmarkResult("c1", "a", "cat", passed=False, elapsed_seconds=1.0),
        ])
        comp = compare_reports(bl, cur)
        assert comp["has_regression"]
        assert len(comp["regressions"]) == 1

    def test_new_and_removed(self):
        bl = BenchmarkReport("bl", "baseline", results=[
            BenchmarkResult("c1", "a", "cat", passed=True, elapsed_seconds=1.0),
        ])
        cur = BenchmarkReport("cur", "current", results=[
            BenchmarkResult("c2", "b", "cat", passed=True, elapsed_seconds=1.0),
        ])
        comp = compare_reports(bl, cur)
        assert comp["new_cases"] == ["c2"]
        assert comp["removed_cases"] == ["c1"]
