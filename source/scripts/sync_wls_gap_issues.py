"""Create bounded, deduplicated WLS gap issues from observed scheduled checks.

This is a downstream *schedule-only* trusted job. Never execute proposal code
or provide any GitHub write token to the candidate/test job. Owner controls
issue closure, PR merge and scored-candidate promotion.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen


MAX_NEW_ISSUES = 3
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
HEAD = re.compile(r"[a-f0-9]{40}\Z")
DIGEST = re.compile(r"[a-f0-9]{16}\Z")
MARKER = "<!-- WLS_OBSERVED_GAP:"


def plan_issues(report: dict, existing_bodies: list[str], *, head: str) -> list[dict]:
    if report.get("schema") != "wls.gap_scan.v1":
        raise ValueError("invalid gap scan schema")
    if not HEAD.fullmatch(head) or report.get("head_sha") != head:
        raise ValueError("gap receipt belongs to another commit")
    if report.get("source_type") != "github_hosted_double_execution":
        raise ValueError("non-hosted gap claim")
    if report.get("status") in {"UNMEASURED", "NO_OBSERVED_GAP"}:
        return []
    if report.get("status") != "OBSERVED_GAPS":
        raise ValueError("unexpected gap scan status")
    comparison = report.get("comparison")
    if not isinstance(comparison, dict) or comparison.get("matched_tests", 0) < 1:
        raise ValueError("no verified matched test identities")
    tasks = comparison.get("tasks")
    if not isinstance(tasks, list) or len(tasks) > 20:
        raise ValueError("invalid observed task count")
    known = {body for body in existing_bodies if isinstance(body, str)}
    output = []
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("malformed observed task")
        kind = task.get("kind")
        test = task.get("test")
        gap_id = task.get("id")
        if kind not in {"REPRODUCIBLE_FAILURE", "FLAKY_CANDIDATE"}:
            continue
        if (not isinstance(test, str) or not 1 <= len(test) <= 160
                or any(ord(char) < 32 for char in test)
                or not isinstance(gap_id, str) or not DIGEST.fullmatch(gap_id)
                or gap_id != sha256(test.encode("utf-8")).hexdigest()[:16]):
            raise ValueError("invalid gap identity or test label")
        if (kind == "REPRODUCIBLE_FAILURE"
                and (task.get("run_one"), task.get("run_two")) != ("FAIL", "FAIL")):
            raise ValueError("unobserved reproducible failure")
        if (kind == "FLAKY_CANDIDATE"
                and {task.get("run_one"), task.get("run_two")} != {"FAIL", "PASS"}):
            raise ValueError("unobserved flaky failure")
        marker = MARKER + gap_id + " -->"
        if any(marker in body for body in known):
            continue
        output.append({
            "title": ("WLS observed gap: " + kind + " " + test)[:180],
            "marker": marker,
            "test": test,
            "kind": kind,
            "id": gap_id,
        })
        known.add(marker)
        if len(output) >= MAX_NEW_ISSUES:
            break
    return output


def github_json(url: str, token: str, payload: dict | None = None):
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "wls-scheduled-gap-triage",
    }
    kwargs: dict = {"headers": headers, "timeout": 15}
    if payload is not None:
        kwargs["data"] = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        kwargs["method"] = "POST"
    with urlopen(Request(url, **kwargs)) as response:  # nosec B310 fixed GitHub API URL
        return json.load(response)


def list_issue_bodies(repo: str, token: str) -> list[str]:
    bodies = []
    for page in range(1, 7):
        rows = github_json(
            f"https://api.github.com/repos/{repo}/issues?state=all&per_page=100&page={page}",
            token,
        )
        if not isinstance(rows, list):
            raise ValueError("invalid GitHub issues list")
        for item in rows:
            if not isinstance(item, dict):
                raise ValueError("malformed GitHub issue row")
            if "pull_request" not in item:
                bodies.append(str(item.get("body") or ""))
        if len(rows) < 100:
            return bodies
    # Fail closed instead of inventing duplicate issues due to pagination cap.
    raise ValueError("issue pagination limit reached")


def run(report: dict, *, repo: str, head: str, run_id: str, token: str) -> dict:
    if not REPO.fullmatch(repo) or not run_id.isdecimal():
        raise ValueError("invalid GitHub repository or run ID")
    if not token:
        raise ValueError("issues token unavailable")
    existing = list_issue_bodies(repo, token)
    proposals = plan_issues(report, existing, head=head)
    created = []
    for item in proposals:
        body = (
            item["marker"] + "\n"
            + "Machine-observed " + item["kind"] + " in WLS double-run hosted test subset.\n"
            + "Test: \\x60" + item["test"] + "\\x60\n"
            + "Exact SHA: \\x60" + head + "\\x60\n"
            + "Evidence: https://github.com/" + repo + "/actions/runs/" + run_id + "\n"
            + "Acceptance: same test passes twice at one pinned source SHA.\n"
            + "Source: candidate task only. No automatic code promotion or RSI claim.\n"
        )
        result = github_json(
            f"https://api.github.com/repos/{repo}/issues", token,
            {"title": item["title"], "body": body},
        )
        if not isinstance(result, dict) or not isinstance(result.get("number"), int):
            raise ValueError("GitHub issue creation not confirmed")
        created.append(result["number"])
    return {
        "status": "CREATED" if created else "NO_VERIFIED_NEW_GAPS",
        "issues_created": created,
        "max_per_run": MAX_NEW_ISSUES,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    # Write authority is disabled outside the owner's scheduled mainline.
    if (os.environ.get("GITHUB_EVENT_NAME") != "schedule"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise SystemExit("schedule/main required; no PR write permissions")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    output = run(
        report, repo=os.environ.get("GITHUB_REPOSITORY", ""),
        head=os.environ.get("GITHUB_SHA", ""),
        run_id=os.environ.get("GITHUB_RUN_ID", ""),
        token=os.environ.get("GH_TOKEN", ""),
    )
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
