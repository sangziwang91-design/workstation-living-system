"""A5 GitHub-hosted gap observation. Extends existing WLS feedback, not a brain.

A scheduled, read-only job runs a bounded real test subset twice and records
observed flaky/reproducible failures as *candidate tasks*. No code changes,
GitHub issue writes, model calls, private holdouts, or RSI promotion.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

MAX_JUNIT_XML_BYTES = 8_000_000


def junit_outcomes(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise ValueError("JUnit evidence must be a regular file, not a symlink")
    with path.open("rb") as stream:
        raw = stream.read(MAX_JUNIT_XML_BYTES + 1)
    if len(raw) > MAX_JUNIT_XML_BYTES:
        raise ValueError("JUnit evidence exceeds bounded XML budget")
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("JUnit evidence must not declare entities or a DTD")
    # Bounded input and explicit DTD/entity rejection before XML parsing.
    root = ET.fromstring(raw)  # nosec B314
    outcomes: dict[str, str] = {}
    for node in root.iter("testcase"):
        identifier = node.get("classname", "") + "::" + node.get("name", "")
        if identifier == "::" or identifier in outcomes:
            raise ValueError("duplicate or missing JUnit test identity")
        failure = node.find("failure") is not None or node.find("error") is not None
        skipped = node.find("skipped") is not None
        if failure and skipped:
            raise ValueError("ambiguous test outcome")
        outcomes[identifier] = "FAIL" if failure else ("SKIP" if skipped else "PASS")
    if not outcomes:
        raise ValueError("no testcases observed")
    return outcomes


def compare_runs(first: dict[str, str], second: dict[str, str]) -> dict:
    # Never compare different test case sets as though their scores matched.
    if first.keys() != second.keys():
        return {
            "status": "UNMEASURED",
            "reason": "test case identities differed between runs",
            "tasks": [],
            "matched_tests": len(first.keys() & second.keys()),
        }
    tasks = []
    for test in sorted(first):
        a, b = first[test], second[test]
        if a not in {"PASS", "FAIL", "SKIP"} or b not in {"PASS", "FAIL", "SKIP"}:
            raise ValueError("invalid test status")
        kind = None
        if a == "FAIL" and b == "FAIL":
            kind = "REPRODUCIBLE_FAILURE"
        elif a != b and "FAIL" in {a, b}:
            kind = "FLAKY_CANDIDATE"
        elif a != b:
            kind = "INCONSISTENT_SKIP"
        if kind:
            tasks.append({
                "id": sha256(test.encode("utf-8")).hexdigest()[:16],
                "test": test,
                "kind": kind,
                "run_one": a,
                "run_two": b,
                "acceptance": "same frozen test case PASS twice at one exact source SHA",
                "authority": "candidate_task_only_no_auto_fix",
            })
    order = {"REPRODUCIBLE_FAILURE": 0, "FLAKY_CANDIDATE": 1, "INCONSISTENT_SKIP": 2}
    tasks.sort(key=lambda item: (order[item["kind"]], item["test"]))
    return {
        "status": "OBSERVED_GAPS" if tasks else "NO_OBSERVED_GAP",
        "reason": "two real executions of one bounded test selection",
        "tasks": tasks[:20],
        "matched_tests": len(first),
        "truncated": len(tasks) > 20,
    }


def lint_debt(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {"status": "UNMEASURED"}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("ruff result must be JSON list")
    counts: dict[str, int] = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            raise ValueError("unexpected ruff issue")
        code = item["code"]
        counts[code] = counts.get(code, 0) + 1
    return {
        "status": "OBSERVED_LEGACY_DEBT_NOT_GAIN",
        "count": len(data),
        "by_code": dict(sorted(counts.items())),
    }


def largest_python_sources(repo: Path) -> list[dict]:
    folder = repo / "source/src/wls"
    files: list[tuple[int, str]] = []
    for path in folder.glob("*.py"):
        if path.is_symlink() or not path.is_file():
            continue
        files.append((
            len(path.read_text(encoding="utf-8").splitlines()),
            path.relative_to(repo).as_posix(),
        ))
    return [
        {"path": name, "lines": count}
        for count, name in sorted(files, key=lambda row: (-row[0], row[1]))[:5]
    ]


def generate(*, repo: Path, first: Path, second: Path, head: str,
             lint: Path | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("head must be exact git commit")
    observed = compare_runs(junit_outcomes(first), junit_outcomes(second))
    return {
        "schema": "wls.gap_scan.v1",
        "head_sha": head,
        "source_type": "github_hosted_double_execution",
        "status": observed["status"],
        "comparison": observed,
        "junit_sha256": {
            "first": sha256(first.read_bytes()).hexdigest(),
            "second": sha256(second.read_bytes()).hexdigest(),
        },
        "lint": lint_debt(lint),
        "size_advisory_not_defect": largest_python_sources(repo),
        "claim_ceiling": "candidate_task_only_no_RSI_or_holdout_claim",
        "private_nuomi_data_seen": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--ruff-json", type=Path)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, default=Path("wls-gap-scan.json"))
    args = parser.parse_args()
    try:
        report = generate(
            repo=args.repo.resolve(), first=args.first, second=args.second,
            head=args.head, lint=args.ruff_json,
        )
    except (OSError, ValueError, ET.ParseError, UnicodeError, json.JSONDecodeError) as error:
        # A failed probe is UNMEASURED; never treat missing data as a healthy run.
        report = {
            "schema": "wls.gap_scan.v1", "status": "UNMEASURED",
            "error_type": type(error).__name__,
            "claim_ceiling": "no_gap_claim_without_complete_evidence",
        }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "candidate_gap_count": len(report.get("comparison", {}).get("tasks", [])),
    }, sort_keys=True))
    return 2 if report["status"] == "UNMEASURED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
