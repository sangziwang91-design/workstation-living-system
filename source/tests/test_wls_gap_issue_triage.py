"""Trusted scheduler must only triage observed matched tests, without PR tokens."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/sync_wls_gap_issues.py"


@pytest.fixture
def triage():
    spec = importlib.util.spec_from_file_location("wls_gap_issue_triage", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def report(tasks, *, status="OBSERVED_GAPS"):
    return {
        "schema": "wls.gap_scan.v1", "source_type": "github_hosted_double_execution",
        "head_sha": "a" * 40, "status": status,
        "comparison": {"matched_tests": 10, "tasks": tasks},
    }


def task(test, kind, first, second):
    return {
        "test": test, "id": sha256(test.encode("utf-8")).hexdigest()[:16],
        "kind": kind, "run_one": first, "run_two": second,
    }


def test_report_creates_one_bounded_candidate_issue(triage):
    observed = report([task("wls::fails", "REPRODUCIBLE_FAILURE", "FAIL", "FAIL")])
    result = triage.plan_issues(observed, [], head="a" * 40)
    assert len(result) == 1
    assert result[0]["marker"].startswith("<!-- WLS_OBSERVED_GAP:")
    assert "wls::fails" in result[0]["title"]
    assert triage.plan_issues(observed, [result[0]["marker"]], head="a" * 40) == []


@pytest.mark.parametrize("status", ["UNMEASURED", "NO_OBSERVED_GAP"])
def test_clean_or_incomplete_evidence_creates_no_issues(triage, status):
    assert triage.plan_issues(report([], status=status), [], head="a" * 40) == []


def test_wrong_head_and_forged_failure_are_rejected(triage):
    item = task("wls::fails", "REPRODUCIBLE_FAILURE", "PASS", "FAIL")
    with pytest.raises(ValueError, match="unobserved"):
        triage.plan_issues(report([item]), [], head="a" * 40)
    with pytest.raises(ValueError, match="another commit"):
        triage.plan_issues(report([]), [], head="b" * 40)


def test_flaky_skip_is_not_issue_without_actual_pass(triage):
    item = task("wls::skip", "FLAKY_CANDIDATE", "FAIL", "SKIP")
    assert triage.plan_issues(report([item]), [], head="a" * 40) == []


def test_issue_count_bounded_and_invalid_identity_refused(triage):
    many = [task("test::" + str(i), "FLAKY_CANDIDATE", "FAIL", "PASS")
            for i in range(10)]
    assert len(triage.plan_issues(report(many), [], head="a" * 40)) == 3
    many[0]["id"] = "bad"
    with pytest.raises(ValueError, match="identity"):
        triage.plan_issues(report(many), [], head="a" * 40)


def test_live_publish_posts_only_valid_tasks_and_cannot_duplicate(triage, monkeypatch):
    observed = report([task("wls::regression", "REPRODUCIBLE_FAILURE", "FAIL", "FAIL")])
    calls = []
    monkeypatch.setattr(triage, "list_issue_bodies", lambda repo, token: [])
    def fake_api(url, token, payload=None):
        calls.append((url, payload))
        return {"number": 63}
    monkeypatch.setattr(triage, "github_json", fake_api)
    result = triage.run(
        observed, repo="example/workstation-living-system", head="a" * 40,
        run_id="1234", token="fixture",
    )
    assert result["issues_created"] == [63]
    assert len(calls) == 1
    assert calls[0][0].endswith("/issues")
    assert "candidate task only" in calls[0][1]["body"]
