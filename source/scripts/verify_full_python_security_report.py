"""Full Python source security inventory, independent of the scored RSI gate.

Bandit scans canonical WLS modules plus automation scripts; this verifier
limits what is asserted by the receipt and fails closed on scan errors.
A static tool report is not proof that a vulnerability is exploitable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def summarize(bandit: dict, sha: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("source must be pinned to exact Git SHA")
    if not isinstance(bandit, dict) or not isinstance(bandit.get("results"), list):
        raise ValueError("Bandit results missing")
    if not isinstance(bandit.get("errors"), list) or bandit["errors"]:
        raise ValueError("Bandit did not completely scan requested targets")
    metrics = bandit.get("metrics")
    if (not isinstance(metrics, dict)
            or not isinstance(metrics.get("_totals"), dict)
            or not isinstance(metrics["_totals"].get("loc"), int)
            or metrics["_totals"]["loc"] < 1):
        raise ValueError("Bandit source coverage absent")
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    critical: list[dict] = []
    for issue in bandit["results"]:
        if not isinstance(issue, dict):
            raise ValueError("malformed Bandit finding")
        severity, confidence = issue.get("issue_severity"), issue.get("issue_confidence")
        path, test_id, line = issue.get("filename"), issue.get("test_id"), issue.get("line_number")
        if (severity not in counts or confidence not in {"HIGH", "MEDIUM", "LOW"}
                or not isinstance(path, str) or not path.endswith(".py")
                or not isinstance(test_id, str) or not re.fullmatch(r"B[0-9]{3}", test_id)
                or not isinstance(line, int) or line < 1):
            raise ValueError("Bandit finding has invalid provenance")
        counts[severity] += 1
        if severity == "HIGH" and confidence in {"HIGH", "MEDIUM"}:
            critical.append({
                "path": path, "line": line, "rule": test_id,
                "severity": severity, "confidence": confidence,
            })
    return {
        "schema": "wls.full_python_security_inventory.v1",
        "source_type": "github_hosted_recursive_bandit",
        "head_sha": sha,
        "status": ("HIGH_PRIORITY_REVIEW" if critical else
                   "STATIC_FINDINGS_REQUIRE_REVIEW" if bandit["results"]
                   else "NO_STATIC_FINDINGS_DETECTED"),
        "scanned_loc": metrics["_totals"]["loc"],
        "finding_count": sum(counts.values()),
        "severity_counts": counts,
        "high_priority": critical[:50],
        "truncated": len(critical) > 50,
        "claim_ceiling": "static_code_security_review_only_no_RSI_or_no_bug_claim",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bandit-json", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, default=Path("hosted-full-security-summary.json"))
    args = parser.parse_args()
    try:
        report = summarize(json.loads(args.bandit_json.read_text(encoding="utf-8")), args.head)
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        report = {
            "schema": "wls.full_python_security_inventory.v1",
            "status": "UNMEASURED", "error_type": type(exc).__name__,
            "claim_ceiling": "no_scan_claim_without_valid_complete_evidence",
        }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "claim_ceiling")}))
    return 0 if report["status"] in {
        "STATIC_FINDINGS_REQUIRE_REVIEW", "NO_STATIC_FINDINGS_DETECTED"
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
