"""CI FEEDBACK contract tests: honest missing evidence and generated state."""
from __future__ import annotations

import importlib.util
import io
import zipfile
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_ci_feedback.py"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def feedback():
    spec = importlib.util.spec_from_file_location("tested_wls_ci_feedback", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _xml(root: Path, *, failing: bool):
    root.mkdir(parents=True, exist_ok=True)
    cases = [
        '<testcase classname="t" name="test_ok" />',
        '<testcase classname="t" name="test_skipped"><skipped/></testcase>',
    ]
    if failing:
        cases.append('<testcase classname="t" name="test_fault"><failure>oops</failure></testcase>')
    (root / "pytest.xml").write_text(
        "<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>",
        encoding="utf-8",
    )


def test_junit_failure_is_actionable_but_not_full_test_coverage(feedback, tmp_path):
    _xml(tmp_path / "evidence", failing=True)
    result = feedback.test_evidence(tmp_path / "evidence")
    assert result["status"] == "FAILED"
    assert result["failed"] == 1
    assert result["skipped"] == 1
    assert result["executed_cases"] == 3
    assert result["failed_tests"][0]["test"] == "t::test_fault"
    assert result["reports"][0]["coverage"] == "PARTIAL_ON_FAILURE"


def test_missing_junit_and_old_report_do_not_claim_success(feedback, tmp_path):
    result = feedback.test_evidence(tmp_path / "missing")
    assert result["status"] == "UNMEASURED"
    assert feedback.compare_previous({"evaluations": {}}, None)["delta"] is None


def test_changed_scoring_source_never_counts_as_previous_gain(feedback):
    base = {"evaluations": {"public_dev_risk": {
        "status": "MEASURED_PUBLIC_DEV_ONLY",
        "score": 0.5, "case_count": 17, "evaluator_source_sha256": "old",
    }}}
    changed = {"evaluations": {"public_dev_risk": {
        "status": "MEASURED_PUBLIC_DEV_ONLY",
        "score": 1.0, "case_count": 17, "evaluator_source_sha256": "new",
    }}}
    finding = feedback.compare_previous(changed, base)
    assert finding["status"] == "INVALID_STANDARDS_CHANGED"
    assert finding["delta"] is None


def test_same_scoring_source_exposes_only_public_dev_delta(feedback):
    base = {"evaluations": {"public_dev_risk": {
        "status": "MEASURED_PUBLIC_DEV_ONLY", "score": 0.5,
        "case_count": 17, "evaluator_source_sha256": "same",
    }}}
    changed = {"evaluations": {"public_dev_risk": {
        "status": "MEASURED_PUBLIC_DEV_ONLY", "score": 0.6,
        "case_count": 17, "evaluator_source_sha256": "same",
    }}}
    r = feedback.compare_previous(changed, base)
    assert r["status"] == "COMPARABLE_PUBLIC_DEV_ONLY"
    assert r["delta"] == 0.1
    assert "not RSI" in r["reason"]


def test_generated_yaml_derives_next_action_from_real_failure(feedback, tmp_path):
    evidence = tmp_path / "evidence"
    _xml(evidence, failing=True)
    result, rendered = feedback.generate(
        ROOT, evidence, None,
        run_id="123", head="a" * 40, branch="main",
    )
    assert result["schema"] == feedback.SCHEMA
    assert result["evaluations"]["public_dev_risk"]["case_count"] == 17
    assert result["evaluations"]["public_dev_risk"]["source_type"] == (
        "public_training_cases_not_holdout"
    )
    assert result["holdout"]["status"] == "EXTERNAL_NUOMI_ONLY"
    assert result["derived_next_action"]["reason"] == "CI_OBSERVED_FAILURE"
    parsed = yaml.safe_load(rendered)
    assert parsed["unique_next_action"] == result["derived_next_action"]["value"]
    assert parsed["ci_feedback_generated"]["status"] == "CI_DERIVED_ADVISORY_NOT_AUTOMERGED"
    assert "CI-derived" not in parsed["unique_next_action"]


def test_next_action_uses_declared_gap_without_ci_failure(feedback, tmp_path):
    evidence = tmp_path / "evidence"
    _xml(evidence, failing=False)
    report, _ = feedback.generate(ROOT, evidence, None,
                                  run_id="1", head="b" * 40, branch="main")
    assert report["derived_next_action"]["reason"] == "DECLARED_GAP_KU-003"
    assert "Patch Mission" in report["derived_next_action"]["value"]
    assert len(report["runtime_gaps_top5"]) == 5
    assert all(gap["source_type"] == "CURRENT_STATE_DECLARED_NOT_CI_OBSERVED"
               for gap in report["runtime_gaps_top5"])


def test_partial_platform_xml_cannot_report_green(
    feedback, tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    _xml(evidence / "hosted-full-wls-123", failing=False)
    result = feedback.test_evidence(
        evidence, require_hosted_matrix=True,
        ci_job_results={"hosted-linux": "success", "hosted-windows": "success"},
    )
    assert result["failed"] == 0
    assert result["status"] == "UNMEASURED"
    assert result["missing_platforms"] == ["windows-py311", "windows-py313"]
    output = {"tests": result}
    action, reason = feedback.next_action(output)
    assert reason == "CI_REQUIRED_PLATFORM_UNMEASURED"
    assert "windows-py311" in action


def test_failed_windows_install_job_never_masks_green_linux_xml(
    feedback, tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    _xml(evidence / "hosted-full-wls-123", failing=False)
    result = feedback.test_evidence(
        evidence, require_hosted_matrix=True,
        ci_job_results={"hosted-linux": "success", "hosted-windows": "failure"},
    )
    assert result["status"] == "FAILED"
    assert result["failed_ci_jobs"] == [
        {"job": "hosted-windows", "result": "failure"}
    ]
    _, reason = feedback.next_action({"tests": result})
    assert reason == "CI_REQUIRED_JOB_FAILED"


def test_full_three_platform_matrix_has_observed_pass(
    feedback, tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    for artifact in (
        "hosted-full-wls-123",
        "hosted-windows-full-wls-3.11-123",
        "hosted-windows-full-wls-3.13-123",
    ):
        _xml(evidence / artifact, failing=False)
    result = feedback.test_evidence(
        evidence, require_hosted_matrix=True,
        ci_job_results={"hosted-linux": "success", "hosted-windows": "success"},
    )
    assert result["status"] == "OBSERVED_PASS"
    assert result["report_count"] == 3
    assert result["missing_platforms"] == []
    assert result["failed_ci_jobs"] == []


def test_cancelled_required_job_never_looks_like_pass(
    feedback, tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    for artifact in (
        "hosted-full-wls-123",
        "hosted-windows-full-wls-3.11-123",
        "hosted-windows-full-wls-3.13-123",
    ):
        _xml(evidence / artifact, failing=False)
    result = feedback.test_evidence(
        evidence, require_hosted_matrix=True,
        ci_job_results={"hosted-linux": "success", "hosted-windows": "cancelled"},
    )
    assert result["status"] == "UNMEASURED"
    assert result["incomplete_ci_jobs"] == [
        {"job": "hosted-windows", "result": "cancelled"}
    ]
    _, reason = feedback.next_action({"tests": result})
    assert reason == "UNMEASURED_TEST_EVIDENCE"


def _feedback_zip(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in entries:
            archive.writestr(name, raw)
    return output.getvalue()


def test_previous_feedback_bound_reads_only_valid_single_record(feedback):
    valid = json.dumps({"schema": feedback.SCHEMA, "hello": "world"}).encode()
    data = _feedback_zip([("FEEDBACK.json", valid)])
    assert feedback.read_bounded_previous_feedback(data)["hello"] == "world"
    wrong = _feedback_zip([("FEEDBACK.json", b'{"schema":"unknown"}')])
    assert feedback.read_bounded_previous_feedback(wrong) is None
    missing = _feedback_zip([("unrelated.json", b'{}')])
    assert feedback.read_bounded_previous_feedback(missing) is None


def test_previous_feedback_rejects_compressed_zip_bomb(feedback):
    raw = b'{"schema":"' + feedback.SCHEMA.encode() + b'","pad":"' + b"A" * 550_000 + b'"}'
    payload = _feedback_zip([("FEEDBACK.json", raw)])
    assert len(payload) < 2_500_000
    with pytest.raises(ValueError, match="decompressed JSON too large"):
        feedback.read_bounded_previous_feedback(payload)


def test_previous_feedback_rejects_ambiguous_and_many_entries(feedback):
    valid = json.dumps({"schema": feedback.SCHEMA}).encode()
    duplicate = _feedback_zip([("FEEDBACK.json", valid), ("nested/FEEDBACK.json", valid)])
    with pytest.raises(ValueError, match="ambiguous"):
        feedback.read_bounded_previous_feedback(duplicate)
    many = _feedback_zip([(f"row-{n}.json", b"{}") for n in range(33)])
    with pytest.raises(ValueError, match="too many files"):
        feedback.read_bounded_previous_feedback(many)


def test_previous_feedback_refuses_nonobject_json(feedback):
    data = _feedback_zip([("FEEDBACK.json", b"[]")])
    with pytest.raises(ValueError, match="JSON object"):
        feedback.read_bounded_previous_feedback(data)
    with pytest.raises(json.JSONDecodeError):
        feedback.read_bounded_previous_feedback(
            _feedback_zip([("FEEDBACK.json", b"not json")])
        )
