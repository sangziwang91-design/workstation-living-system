"""Generate advisory GitHub CI feedback and a derived WLS CURRENT_STATE snapshot.

Inputs: GitHub-hosted JUnit XML, the PUBLIC development risk cases and
CURRENT_STATE declarations. Never access or report Nuomi's private holdout.
No change to runtime authority, Git refs, policy, or model promotion.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET
import zipfile

import yaml

SCHEMA = "wls.ci_feedback.v1"
EVALUATOR_FILES = (
    "source/src/wls/task_admission.py",
    "source/src/wls/benchmark.py",
    "source/src/wls/evaluator.py",
    "source/src/wls/experiment_decision.py",
    "source/scripts/github_rsi_replay_model.py",
)


def test_evidence(root: Path) -> dict:
    reports = sorted(root.rglob("*.xml")) if root.is_dir() else []
    observed: list[dict] = []
    for file in reports:
        parsed = ET.parse(file).getroot()
        nodes = parsed.findall(".//testcase")
        if parsed.tag == "testcase":
            nodes = [parsed]
        failed = []
        skipped = 0
        for case in nodes:
            failure = case.find("failure")
            error = case.find("error")
            if failure is not None or error is not None:
                failed.append({
                    "test": f"{case.get('classname', '')}::{case.get('name', '')}",
                    "kind": "failure" if failure is not None else "error",
                })
            elif case.find("skipped") is not None:
                skipped += 1
        observed.append({
            "source": str(file.relative_to(root)).replace("\\", "/"),
            "cases_observed": len(nodes),
            "passed": len(nodes) - len(failed) - skipped,
            "failed": len(failed),
            "skipped": skipped,
            "failed_tests": failed[:50],
            # --maxfail=1 prevents interpreting observed cases as full coverage
            "coverage": "PARTIAL_ON_FAILURE" if failed else "OBSERVED",
        })
    failures = [item for report in observed for item in report["failed_tests"]]
    return {
        "status": "UNMEASURED" if not reports else (
            "FAILED" if failures else "OBSERVED_PASS"
        ),
        "reports": observed,
        "report_count": len(observed),
        "executed_cases": sum(r["cases_observed"] for r in observed),
        "passed": sum(r["passed"] for r in observed),
        "failed": sum(r["failed"] for r in observed),
        "skipped": sum(r["skipped"] for r in observed),
        "failed_tests": failures[:50],
        "aggregation": "per-platform test executions, not unique tests",
    }


def public_dev_risk_metric(repo: Path) -> dict:
    from wls.task_admission import (
        RsiRiskAdmissionEvaluator,
        _RSI_RISK_CASES,
        admit_with_rsi_risk_strategy,
    )

    score_files = []
    for name in EVALUATOR_FILES:
        file = repo / name
        if not file.is_file():
            raise FileNotFoundError(name)
        score_files.append([name, hashlib.sha256(file.read_bytes()).hexdigest()])
    digest = hashlib.sha256(json.dumps(
        score_files, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest()
    outcomes = [
        admit_with_rsi_risk_strategy(text, {}).risk_floor == expected
        for text, expected in _RSI_RISK_CASES
    ]
    return {
        "status": "MEASURED_PUBLIC_DEV_ONLY",
        "kind": "risk_admission_static_baseline",
        "case_count": len(outcomes),
        "passed_cases": sum(outcomes),
        "score": round(sum(outcomes) / len(outcomes), 8) if outcomes else None,
        "evaluator_digest_self_reported": RsiRiskAdmissionEvaluator.digest(),
        "evaluator_source_sha256": digest,
        "source_type": "public_training_cases_not_holdout",
        "claim_ceiling": "advisory; cannot prove RSI, migration or task transfer",
    }


def declared_gaps(state: dict) -> list[dict]:
    raw = state.get("known_unknowns", [])
    if not isinstance(raw, list):
        return []
    priority = {"KU-003": 0, "KU-001": 1, "KU-004": 2, "KU-005": 3}
    gaps = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "UNKNOWN"))
        if status in {"VERIFIED", "DONE", "COMPLETE"}:
            continue
        gaps.append({
            "id": str(item.get("id", "")),
            "item": str(item.get("item", "")),
            "status": status,
            "source_type": "CURRENT_STATE_DECLARED_NOT_CI_OBSERVED",
        })
    return sorted(gaps, key=lambda x: (
        priority.get(x["id"], 100), x["id"]
    ))[:5]


def compare_previous(current: dict, previous: dict | None) -> dict:
    if not previous:
        return {"status": "UNMEASURED", "delta": None,
                "reason": "No prior comparable FEEDBACK artifact"}
    old = previous.get("evaluations", {}).get("public_dev_risk", {})
    new = current.get("evaluations", {}).get("public_dev_risk", {})
    if not isinstance(old, dict) or not isinstance(new, dict) or not all([
        old.get("status") == "MEASURED_PUBLIC_DEV_ONLY",
        new.get("status") == "MEASURED_PUBLIC_DEV_ONLY",
        old.get("case_count") == new.get("case_count"),
        old.get("evaluator_source_sha256") == new.get("evaluator_source_sha256"),
        isinstance(old.get("score"), (int, float)),
        isinstance(new.get("score"), (int, float)),
    ]):
        return {"status": "INVALID_STANDARDS_CHANGED", "delta": None,
                "reason": "Evaluation code or case count differs; no growth claim"}
    delta = round(float(new["score"]) - float(old["score"]), 8)
    return {
        "status": "COMPARABLE_PUBLIC_DEV_ONLY", "delta": delta,
        "prior_score": old["score"], "current_score": new["score"],
        "reason": "Matched evaluator version; public-dev score only, not RSI",
    }


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        next_request = super().redirect_request(
            request, fp, code, msg, headers, newurl
        )
        if next_request is not None and urlparse(newurl).hostname != "api.github.com":
            next_request.remove_header("Authorization")
        return next_request


def fetch_previous_feedback(repo: str, branch: str, current_run: str,
                            token: str) -> dict | None:
    """Best-effort previous GitHub artifact; no private task data is fetched."""
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return None
    opener = build_opener(_SafeRedirect())
    def get(url: str) -> bytes:
        request = Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "WLS-CI-feedback",
        })
        with opener.open(request, timeout=15) as response:
            raw = response.read(2_500_001)
        if len(raw) > 2_500_000:
            raise ValueError("artifact download exceeds bound")
        return raw

    # Previous artifacts may be unavailable to forks or after retention expiry.
    listing = json.loads(get(
        f"https://api.github.com/repos/{repo}/actions/artifacts?per_page=100"
    ))
    candidates = []
    for item in listing.get("artifacts", []):
        meta = item.get("workflow_run") or {}
        if (
            item.get("name", "").startswith("wls-feedback-")
            and not item.get("expired")
            and str(meta.get("id")) != str(current_run)
            and meta.get("head_branch") == branch
        ):
            candidates.append(item)
    for item in sorted(candidates, key=lambda x: x.get("created_at", ""), reverse=True)[:3]:
        payload = get(item["archive_download_url"])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.endswith("FEEDBACK.json")]
            if not names:
                continue
            with archive.open(names[0]) as stream:
                previous = json.load(stream)
            if previous.get("schema") == SCHEMA:
                return previous
    return None


def next_action(feedback: dict) -> tuple[str, str]:
    failures = feedback["tests"]["failed_tests"]
    if failures:
        return (
            f"Repair the first observed CI failure: {failures[0]['test']}; "
            "rerun all Linux and Windows checks before claiming recovery.",
            "CI_OBSERVED_FAILURE",
        )
    if feedback["tests"]["status"] == "UNMEASURED":
        return ("Restore JUnit CI evidence before selecting a next task.",
                "UNMEASURED_TEST_EVIDENCE")
    if feedback["previous_round"]["status"] == "INVALID_STANDARDS_CHANGED":
        return (
            "Rebaseline the changed public development evaluator; do not count "
            "cross-version score movement as RSI improvement.",
            "EVALUATOR_STANDARDS_MOVED",
        )
    gaps = feedback["runtime_gaps_top5"]
    if gaps:
        gap = gaps[0]
        if gap["id"] == "KU-003":
            return (
                "Execute the next owner-authorized GitHub Patch Mission on a "
                "genuine task; preserve action outcomes and compare with the "
                "frozen version before claiming longitudinal learning.",
                "DECLARED_GAP_" + gap["id"],
            )
        return (f"Obtain concrete evidence for {gap['id']}: {gap['item']}",
                "DECLARED_GAP_" + gap["id"])
    return ("Inspect measured regressions and select a bounded real task.",
            "NO_DECLARED_GAP")


def generate(root: Path, evidence_root: Path, previous: dict | None,
             *, run_id: str, head: str, branch: str) -> tuple[dict, str]:
    state_path = root / "CURRENT_STATE.yaml"
    state_source = state_path.read_text(encoding="utf-8")
    state = yaml.safe_load(state_source)
    if not isinstance(state, dict):
        raise ValueError("CURRENT_STATE must be YAML mapping")
    feedback: dict = {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "ci": {"run_id": run_id, "head_sha": head, "branch": branch},
        "authority": "CI_OBSERVATION_NON_PROMOTABLE",
        "tests": test_evidence(evidence_root),
        "evaluations": {"public_dev_risk": public_dev_risk_metric(root)},
        "runtime_gaps_top5": declared_gaps(state),
        "holdout": {"status": "EXTERNAL_NUOMI_ONLY", "cases_in_repository": 0},
    }
    feedback["previous_round"] = compare_previous(feedback, previous)
    next_step, reason = next_action(feedback)
    feedback["derived_next_action"] = {
        "value": next_step, "reason": reason, "claim": "candidate_next_task_only"
    }
    # Preserve the canonical YAML layout and all human history. The CI output
    # is a generated snapshot, not a silent write to protected main.
    lines = state_source.splitlines(keepends=True)
    out: list[str] = []
    idx = 0
    replaced = False
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("unique_next_action:"):
            out.append("unique_next_action: " + json.dumps(next_step, ensure_ascii=False) + "\n")
            replaced = True
            idx += 1
            while idx < len(lines) and (
                lines[idx].startswith("  ") or not lines[idx].strip()
            ):
                idx += 1
            continue
        out.append(line)
        idx += 1
    if not replaced:
        raise ValueError("CURRENT_STATE has no unique_next_action")
    out.append("\nci_feedback_generated:\n")
    out.append(f"  schema: {SCHEMA}\n")
    out.append(f"  run_id: {json.dumps(str(run_id))}\n")
    out.append(f"  head_sha: {json.dumps(head)}\n")
    out.append("  status: CI_DERIVED_ADVISORY_NOT_AUTOMERGED\n")
    return feedback, "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-previous-fetch", action="store_true")
    args = parser.parse_args()
    root = args.repo.resolve()
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    branch = os.getenv("GITHUB_HEAD_REF") or os.getenv("GITHUB_REF_NAME") or "local"
    previous = None
    # Network failure never manufactures an improvement; it stays UNMEASURED.
    if not args.no_previous_fetch:
        try:
            previous = fetch_previous_feedback(
                os.getenv("GITHUB_REPOSITORY", ""), branch, run_id,
                os.getenv("GH_TOKEN", ""),
            )
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
            previous = None
    feedback, rendered = generate(
        root, args.evidence, previous, run_id=run_id,
        head=os.getenv("GITHUB_SHA", "local"), branch=branch,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "FEEDBACK.json").write_text(
        json.dumps(feedback, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output / "CURRENT_STATE.yaml").write_text(rendered, encoding="utf-8")
    print(json.dumps({
        "schema": SCHEMA, "tests": feedback["tests"]["status"],
        "next_reason": feedback["derived_next_action"]["reason"],
        "score": feedback["evaluations"]["public_dev_risk"]["score"],
        "previous_status": feedback["previous_round"]["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
