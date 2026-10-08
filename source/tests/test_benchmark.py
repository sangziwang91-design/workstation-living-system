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


def test_benchmark_requires_explicit_boolean_verdict():
    for raw in ({}, {"passed": "false"}, {"passed": 1}, {"passed": None}):
        suite = BenchmarkSuite("frozen", discriminator="dev")
        suite.add(BenchmarkCase("a", "test", "coding", {}), lambda _, v=raw: v)
        report = suite.run()
        assert report.passed_cases() == 0
        assert "explicit boolean" in report.results[0].error


def _frozen_report(items, answers):
    suite = BenchmarkSuite("wls-capability", discriminator="dev")
    for case_id, task in items:
        suite.add(
            BenchmarkCase(case_id, case_id, "coding", {"task": task}),
            lambda _, ok=answers[case_id]: {"passed": ok},
        )
    return suite.run()


def test_rsi_frozen_comparison_detects_unchanged_tasks_and_gain():
    from wls.benchmark import paired_rsi_metric

    tasks = [("easy", "noop"), ("hard", "parse")]
    before = _frozen_report(tasks, {"easy": True, "hard": False})
    after = _frozen_report(tasks, {"easy": True, "hard": True})
    verdict = compare_reports(before, after)
    assert before.suite_digest == after.suite_digest
    assert verdict["measurement_valid"] is True
    assert verdict["eligible_for_promotion"] is True
    assert verdict["matched_rate_delta"] == 0.5
    result = paired_rsi_metric(before, after, evaluator_digest="a" * 64)
    assert result.primary == 1.0
    assert result.gates["critical_regressions"] == 0.0


def test_deleted_failed_task_is_not_an_improvement():
    import pytest
    from wls.benchmark import paired_rsi_metric

    before = _frozen_report(
        [("easy", 1), ("hard", 2)], {"easy": True, "hard": False}
    )
    after = _frozen_report([("easy", 1)], {"easy": True})
    comparison = compare_reports(before, after)
    assert comparison["rate_delta"] == 0.5  # misleading raw score
    assert comparison["matched_rate_delta"] is None
    assert comparison["removed_cases"] == ["hard"]
    assert comparison["eligible_for_promotion"] is False
    with pytest.raises(ValueError, match="measurement invalid"):
        paired_rsi_metric(before, after, evaluator_digest="a" * 64)


def test_candidate_cannot_edit_or_duplicate_its_own_test():
    import pytest
    from wls.benchmark import paired_rsi_metric

    tasks = [("one", 1), ("two", 2)]
    before = _frozen_report(tasks, {"one": False, "two": True})
    altered_input = _frozen_report(
        [("one", "weaker"), ("two", 2)], {"one": True, "two": True}
    )
    assert not compare_reports(before, altered_input)["measurement_valid"]

    dup = _frozen_report(tasks, {"one": True, "two": True})
    dup.results.append(dup.results[0])
    finding = compare_reports(before, dup)
    assert finding["duplicate_case_ids"] == ["one"]
    assert finding["eligible_for_promotion"] is False
    with pytest.raises(ValueError, match="measurement invalid"):
        paired_rsi_metric(before, dup, evaluator_digest="a" * 64)


def test_old_task_regression_blocks_promotion_despite_net_gain():
    tasks = [("a", 1), ("b", 2), ("c", 3), ("d", 4)]
    old = _frozen_report(tasks, {"a": True, "b": False, "c": False, "d": False})
    current = _frozen_report(
        tasks, {"a": False, "b": True, "c": True, "d": False}
    )
    finding = compare_reports(old, current)
    assert finding["measurement_valid"] is True
    assert finding["rate_delta"] == 0.25
    assert finding["has_regression"] is True
    assert finding["eligible_for_promotion"] is False


def test_old_unfrozen_reports_can_be_read_but_not_promoted():
    old = BenchmarkReport("old", "legacy", results=[
        BenchmarkResult("a", "task", "code", passed=False, elapsed_seconds=1)
    ])
    current = BenchmarkReport("cur", "legacy", results=[
        BenchmarkResult("a", "task", "code", passed=True, elapsed_seconds=1)
    ])
    finding = compare_reports(old, current)
    assert finding["measurement_valid"] is False
    assert finding["eligible_for_promotion"] is False


def test_duplicate_registered_benchmark_case_rejected():
    import pytest

    suite = BenchmarkSuite("frozen")
    case = BenchmarkCase("same", "a", "coding", {})
    suite.add(case, lambda _: {"passed": True})
    with pytest.raises(ValueError, match="duplicate benchmark case_id"):
        suite.add(case, lambda _: {"passed": True})


def test_frozen_benchmark_can_feed_existing_wls_rsi_without_live_promotion(tmp_path):
    from wls.benchmark import paired_rsi_metric
    from wls.db import Database
    from wls.evidence import EvidenceLedger
    from wls.experiment_decision import ExperimentPolicy, MetricResult
    from wls.rsi_evolution import RsiEvolutionPilot

    tasks = [("easy", "noop"), ("hard", "parse")]
    before = _frozen_report(tasks, {"easy": True, "hard": False})
    after = _frozen_report(tasks, {"easy": True, "hard": True})
    digest = "a" * 64
    policy = ExperimentPolicy(
        direction="maximize",
        minimum_gain=0.01,
        hard_gates={"critical_regressions": 0.0},
        max_rounds=1,
        max_failures=0,
        evaluator_digest=digest,
    )
    db = Database(tmp_path / "wls.sqlite")
    ledger = EvidenceLedger(db, tmp_path / "evidence.key")
    try:
        pilot = RsiEvolutionPilot(db, ledger)
        pilot.start(
            run_id="actual-measured-cases",
            policy=policy,
            baseline_id="frozen",
            baseline=MetricResult(
                primary=before.success_rate(),
                gates={"critical_regressions": 0.0},
                evaluator_digest=digest,
            ),
            branches=1,
        )
        state = pilot.advance(
            "actual-measured-cases",
            policy=policy,
            propose=lambda parent, generation, branch: "candidate",
            evaluate=lambda candidate: paired_rsi_metric(
                before, after, evaluator_digest=digest
            ),
        )
        assert state["champion_id"] == "candidate"
        assert state["live_promotion"] is False
        assert ledger.verify()[0] is True
    finally:
        db.close_all()
