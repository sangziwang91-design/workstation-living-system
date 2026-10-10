"""A5 real observed-gap comparison controls (not synthetic RSI gain)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_wls_gap_scan.py"


@pytest.fixture
def scan():
    spec = importlib.util.spec_from_file_location("wls_test_gap_scan", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_junit(path: Path, outcomes: dict[str, str]) -> None:
    import xml.etree.ElementTree as ET

    root = ET.Element("testsuite")
    for test, status in outcomes.items():
        case = ET.SubElement(root, "testcase", classname="wls", name=test)
        if status == "FAIL":
            ET.SubElement(case, "failure")
        if status == "SKIP":
            ET.SubElement(case, "skipped")
    path.write_bytes(ET.tostring(root, encoding="utf-8"))


def test_detects_repeated_failure_and_flaky_without_changing_grader(scan, tmp_path):
    one, two = tmp_path / "first.xml", tmp_path / "second.xml"
    write_junit(one, {"good": "PASS", "bad": "FAIL", "flaky": "PASS"})
    write_junit(two, {"good": "PASS", "bad": "FAIL", "flaky": "FAIL"})
    result = scan.compare_runs(scan.junit_outcomes(one), scan.junit_outcomes(two))
    assert result["status"] == "OBSERVED_GAPS"
    assert result["matched_tests"] == 3
    assert [x["kind"] for x in result["tasks"]] == [
        "REPRODUCIBLE_FAILURE", "FLAKY_CANDIDATE",
    ]
    assert all(x["authority"] == "candidate_task_only_no_auto_fix"
               for x in result["tasks"])


def test_no_issues_does_not_invent_a_growth_cycle(scan, tmp_path):
    one, two = tmp_path / "first.xml", tmp_path / "second.xml"
    write_junit(one, {"good": "PASS", "skipped": "SKIP"})
    write_junit(two, {"good": "PASS", "skipped": "SKIP"})
    result = scan.compare_runs(scan.junit_outcomes(one), scan.junit_outcomes(two))
    assert result["status"] == "NO_OBSERVED_GAP"
    assert result["tasks"] == []


def test_changed_task_identity_invalidates_comparison(scan):
    result = scan.compare_runs({"a": "PASS"}, {"b": "FAIL"})
    assert result["status"] == "UNMEASURED"
    assert result["tasks"] == []


def test_duplicate_junit_case_fails_closed(scan, tmp_path):
    source = tmp_path / "dup.xml"
    source.write_text(
        '<testsuite><testcase classname="a" name="x"/>'
        '<testcase classname="a" name="x"/></testsuite>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        scan.junit_outcomes(source)


def test_lint_debt_is_advisory_not_a_discovered_regression(scan, tmp_path):
    source = tmp_path / "ruff.json"
    source.write_text(json.dumps([{"code": "F401"}, {"code": "F401"}]),
                      encoding="utf-8")
    result = scan.lint_debt(source)
    assert result["count"] == 2
    assert result["status"] == "OBSERVED_LEGACY_DEBT_NOT_GAIN"
    assert result["by_code"] == {"F401": 2}


def test_full_gap_receipt_has_exact_hash_and_no_private_holdout(scan, tmp_path):
    one, two = tmp_path / "one.xml", tmp_path / "two.xml"
    write_junit(one, {"good": "PASS"})
    write_junit(two, {"good": "PASS"})
    report = scan.generate(repo=tmp_path, first=one, second=two, head="a" * 40)
    assert report["status"] == "NO_OBSERVED_GAP"
    assert report["private_nuomi_data_seen"] is False
    assert report["comparison"]["matched_tests"] == 1
    assert len(report["junit_sha256"]["first"]) == 64
    with pytest.raises(ValueError, match="exact git commit"):
        scan.generate(repo=tmp_path, first=one, second=two, head="main")


def test_junit_entity_expansion_and_dtd_are_not_accepted(scan, tmp_path):
    path = tmp_path / "hostile.xml"
    path.write_bytes(
        b'<!DOCTYPE testsuite [ <!ENTITY secret "expanded"> ]>'
        b'<testsuite><testcase classname="x" name="&secret;"/></testsuite>'
    )
    with pytest.raises(ValueError, match="DTD"):
        scan.junit_outcomes(path)


def test_junit_oversize_and_symlink_fail_closed(scan, tmp_path):
    path = tmp_path / "large.xml"
    with path.open("wb") as stream:
        stream.write(b"<testsuite>")
        stream.write(b"A" * (scan.MAX_JUNIT_XML_BYTES + 1))
    with pytest.raises(ValueError, match="bounded"):
        scan.junit_outcomes(path)
    alias = tmp_path / "alias.xml"
    try:
        alias.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this CI runner")
    with pytest.raises(ValueError, match="symlink"):
        scan.junit_outcomes(alias)


def test_normal_bounded_junit_still_reports_real_failure(scan, tmp_path):
    path = tmp_path / "valid.xml"
    write_junit(path, {"passed": "PASS", "broken": "FAIL"})
    assert scan.junit_outcomes(path) == {
        "wls::passed": "PASS", "wls::broken": "FAIL",
    }


def test_junit_utf16_entity_declaration_cannot_bypass_bytes_guard(scan, tmp_path):
    xml = (
        '<?xml version="1.0" encoding="utf-16"?>'
        '<!DOCTYPE testsuite [<!ENTITY exploit "expanded">]>'
        '<testsuite><testcase classname="x" name="&exploit;"/></testsuite>'
    ).encode("utf-16")
    path = tmp_path / "utf16.xml"
    path.write_bytes(xml)
    with pytest.raises(UnicodeError):
        scan.junit_outcomes(path)
