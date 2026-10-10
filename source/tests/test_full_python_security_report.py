"""Independent positive and negative evidence controls for full Python scan."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_full_python_security_report.py"


@pytest.fixture
def audit():
    spec = importlib.util.spec_from_file_location("wls_full_bandit", SCRIPT)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result


def data(items=None):
    return {"results": items or [], "errors": [],
            "metrics": {"_totals": {"loc": 20000}}}


def finding(severity="MEDIUM", confidence="HIGH"):
    return {
        "filename": "source/src/wls/runtime.py", "line_number": 45,
        "issue_severity": severity, "issue_confidence": confidence,
        "test_id": "B603", "issue_text": "UNTRUSTED_TOOL_SOURCE_CONTENT",
    }


def test_clean_result_never_claims_all_code_is_bug_free(audit):
    result = audit.summarize(data(), "a" * 40)
    assert result["status"] == "NO_STATIC_FINDINGS_DETECTED"
    assert result["scanned_loc"] == 20000
    assert "no_bug_claim" in result["claim_ceiling"]


def test_medium_finding_is_visible_but_not_misclassified_as_high(audit):
    result = audit.summarize(data([finding()]), "a" * 40)
    assert result["status"] == "STATIC_FINDINGS_REQUIRE_REVIEW"
    assert result["severity_counts"]["MEDIUM"] == 1
    assert result["high_priority"] == []
    assert "UNTRUSTED_TOOL_SOURCE_CONTENT" not in str(result)


def test_high_confidence_serious_finding_blocks_gate(audit):
    result = audit.summarize(data([finding("HIGH", "HIGH")]), "a" * 40)
    assert result["status"] == "HIGH_PRIORITY_REVIEW"
    assert result["high_priority"] == [{
        "path": "source/src/wls/runtime.py", "line": 45,
        "rule": "B603", "severity": "HIGH", "confidence": "HIGH",
    }]


def test_missing_scan_or_hidden_errors_reject_as_unmeasured(audit):
    for report in (
        {"results": [], "errors": []},
        data() | {"errors": [{"filename": "runtime.py", "reason": "parse failure"}]},
        data() | {"results": [{"issue_severity": "HIGH"}]},
        data() | {"metrics": {"_totals": {"loc": 0}}},
    ):
        with pytest.raises(ValueError):
            audit.summarize(report, "a" * 40)


def test_unpinned_source_cannot_be_assigned_verdict(audit):
    with pytest.raises(ValueError, match="exact Git SHA"):
        audit.summarize(data(), "main")
