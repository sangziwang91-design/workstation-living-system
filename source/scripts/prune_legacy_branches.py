"""One-time, fail-closed retirement of WLS legacy GitHub branch references.

This file is only run on a trusted push to main, never on a PR checkout.
An immutable audit manifest locks every retiring branch to its observed SHA.
Before deleting ANY branch, preserve every historical tip under one Git tag
through a chain of no-source-change, two-parent archival commits.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess  # nosec B404 - fixed git/gh argv, shell=False
import sys
from typing import Any
from urllib.parse import quote

REPO = "sangziwang91-design/workstation-living-system"
SCHEMA = "wls.branch_retirement.v1"
MANIFEST = "docs/audits/BRANCH_RETIREMENT_2026-10-16.json"
EXPECTED_TAG = "wls-legacy-branches-20261016"
CLEANUP_BRANCH = "maintenance/prune-legacy-refs-20261016"
ROOT = Path(__file__).resolve().parents[2]
RECEIPT = "branch-retirement-receipt.json"


class RetirementBlocked(ValueError):
    pass


def cmd(*argv: str, check: bool = True, input_text: str | None = None) -> str:
    result = subprocess.run(  # nosec B603
        argv, cwd=ROOT, input=input_text, text=True, encoding="utf-8",
        errors="replace", capture_output=True, check=False, timeout=180,
    )
    if check and result.returncode:
        raise RetirementBlocked(f"command failed: {argv[0]} {argv[1] if len(argv)>1 else ''} "
                                f"(return={result.returncode})")
    return result.stdout.strip()


def git(*argv: str, check: bool = True, input_text: str | None = None) -> str:
    return cmd("git", *argv, check=check, input_text=input_text)


def api(endpoint: str, *, method: str = "GET") -> Any:
    if not endpoint.startswith(f"repos/{REPO}/"):
        raise RetirementBlocked("GitHub API endpoint outside owner repository")
    if method not in {"GET", "DELETE"}:
        raise RetirementBlocked("unapproved GitHub API method")
    out = cmd("gh", "api", "--method", method, endpoint)
    if method == "DELETE":
        return None
    return json.loads(out)


def read_manifest() -> dict[str, Any]:
    data = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    if (
        type(data) is not dict or data.get("schema") != SCHEMA
        or data.get("repository") != REPO or data.get("archive_tag") != EXPECTED_TAG
        or data.get("workflow_branch") != CLEANUP_BRANCH
    ):
        raise RetirementBlocked("invalid retirement manifest identity")
    rows = data.get("branches")
    if not isinstance(rows, list) or len(rows) != 120:
        raise RetirementBlocked("retirement manifest missing 120 historical refs")
    names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"branch", "sha", "disposition", "prs"}:
            raise RetirementBlocked("invalid manifest branch structure")
        name, sha = row["branch"], row["sha"]
        if (not isinstance(name, str) or not name or name in names
            or name in {"main", CLEANUP_BRANCH}
            or "\\" in name or ".." in name or name.startswith("/")
            or any(c in name for c in " \t\r\n?#")
            or not isinstance(sha, str) or len(sha) != 40
            or any(c not in "0123456789abcdef" for c in sha)
            or row["disposition"] not in {
                "PR_MERGED", "PR_CLOSED_UNMERGED", "NO_PR",
            }):
            raise RetirementBlocked("unsafe or duplicate retirement branch")
        names.add(name)
    if len(data.get("expected_open_pr_branches", [])) != 3:
        raise RetirementBlocked("expected active PR branch seal missing")
    if names & set(data["expected_open_pr_branches"]):
        raise RetirementBlocked("manifest attempts to retire an active PR branch")
    return data


def all_branches() -> dict[str, dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for page in range(1, 5):
        response = api(f"repos/{REPO}/branches?per_page=100&page={page}")
        if not isinstance(response, list):
            raise RetirementBlocked("unexpected branch inventory response")
        pages.extend(response)
        if len(response) < 100:
            break
    return {row["name"]: row for row in pages}


def open_pr_branches() -> set[str]:
    result: list[dict[str, Any]] = []
    for page in range(1, 3):
        response = api(f"repos/{REPO}/pulls?state=open&per_page=100&page={page}")
        if not isinstance(response, list):
            raise RetirementBlocked("unexpected open PR response")
        result.extend(response)
        if len(response) < 100:
            break
    return {pr["head"]["ref"] for pr in result if pr["head"]["repo"]
            and pr["head"]["repo"]["full_name"] == REPO}


def ancestor(old: str, new: str) -> bool:
    result = subprocess.run(  # nosec B603
        ["git", "merge-base", "--is-ancestor", old, new], cwd=ROOT,
        capture_output=True, check=False, timeout=30,
    )
    if result.returncode not in {0, 1}:
        raise RetirementBlocked("Git ancestry probe failed")
    return result.returncode == 0


def preflight(data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME") != "push"):
        raise RetirementBlocked("only a trusted main push may retire GitHub refs")
    head = git("rev-parse", "HEAD")
    if head != os.environ.get("GITHUB_SHA"):
        raise RetirementBlocked("checkout does not match signed Actions HEAD")
    if not ancestor(data["baseline_main_sha"], head):
        raise RetirementBlocked("baseline main SHA is not a parent of current main")
    active = open_pr_branches()
    if active != set(data["expected_open_pr_branches"]):
        raise RetirementBlocked("open PR inventory changed; fail closed, redo audit")
    current = all_branches()
    allowed = {row["branch"]: row for row in data["branches"]}
    expected = {*allowed, "main", CLEANUP_BRANCH, *active}
    if set(current) != expected:
        raise RetirementBlocked("unreviewed Git branch added/removed since audit")
    if current["main"]["commit"]["sha"] != head:
        raise RetirementBlocked("default branch tip moved during cleanup")
    if any(current[name]["protected"] for name in expected if name in current):
        # main itself may gain protection; this is safe. Check targets only.
        for name in [*allowed, CLEANUP_BRANCH]:
            if current[name]["protected"]:
                raise RetirementBlocked("ref unexpectedly protected")
    for name, row in allowed.items():
        if current[name]["commit"]["sha"] != row["sha"]:
            raise RetirementBlocked(f"branch SHA changed since audit: {name}")
    extra = current[CLEANUP_BRANCH]["commit"]["sha"]
    if not ancestor(extra, head):
        raise RetirementBlocked("cleanup branch is not already merged into main")
    return current, data["branches"]


def archive_all(data: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, str, int]:
    # Fetch all historic refs locally; no model-generated/untrusted code executes.
    git("fetch", "--no-tags", "origin", "+refs/heads/*:refs/remotes/origin/*")
    head = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    tips = sorted({row["sha"] for row in rows})
    unique = [tip for tip in tips if not ancestor(tip, head)]
    tag = data["archive_tag"]
    remote_tag = git("ls-remote", "--tags", "origin", f"refs/tags/{tag}", check=False)
    if not remote_tag:
        prior = head
        for sha in unique:
            prior = git(
                "commit-tree", tree, "-p", prior, "-p", sha,
                input_text=f"archive: preserve historic WLS branch {sha}\n\n"
                           "No source change; preserve original git objects only.\n",
            )
        git("tag", "-a", tag, prior, "-m",
            f"Preserve original Git histories of {len(unique)} branch-tip DAGs "
            "before WLS ref cleanup")
        git("push", "origin", f"refs/tags/{tag}")
    else:
        # Resume is only possible if every branch is demonstrably reachable
        # from the existing immutable archive tag.
        git("fetch", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    commit = git("rev-parse", f"refs/tags/{tag}^{{}}")
    for sha in tips:
        if not ancestor(sha, commit) and not ancestor(sha, head):
            raise RetirementBlocked("permanent tag does not retain every branch commit")
    return tag, commit, len(unique)


def verify_remote_tip(name: str, expected_sha: str) -> None:
    # GitHub REST preserves slash-containing refs as wildcards in the ref suffix.
    response = api(f"repos/{REPO}/git/ref/heads/{quote(name, safe='/')}")
    if response["object"]["sha"] != expected_sha:
        raise RetirementBlocked(f"branch moved immediately before delete: {name}")


def retire(name: str, expected_sha: str) -> None:
    verify_remote_tip(name, expected_sha)
    api(f"repos/{REPO}/git/refs/heads/{quote(name, safe='/')}", method="DELETE")


def execute(data: dict[str, Any]) -> dict[str, Any]:
    current, rows = preflight(data)
    archive_tag, archive_sha, unique = archive_all(data, rows)
    result: dict[str, Any] = {
        "schema": "wls.branch_retirement_receipt.v1",
        "baseline_main": data["baseline_main_sha"],
        "main": git("rev-parse", "HEAD"),
        "archive_tag": archive_tag,
        "archive_commit": archive_sha,
        "unique_preserved_divergent_tips": unique,
        "preserved_open_pr_refs": sorted(data["expected_open_pr_branches"]),
        "requested_stale_refs": len(rows),
        "removed": [],
        "status": "IN_PROGRESS",
    }
    def receipt() -> None:
        (ROOT / RECEIPT).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    receipt()
    for row in rows:
        retire(row["branch"], row["sha"])
        result["removed"].append(row["branch"])
        receipt()
    # The cleanup PR is already merged by this trusted push. Retire it too,
    # only after proving the tip is a current-main ancestor.
    retire(CLEANUP_BRANCH, current[CLEANUP_BRANCH]["commit"]["sha"])
    result["removed"].append(CLEANUP_BRANCH)
    result["status"] = "RETIRED"
    receipt()
    remaining = all_branches()
    expected = {"main", *data["expected_open_pr_branches"]}
    if set(remaining) != expected:
        result["status"] = "POSTCHECK_FAILED"
        result["remaining"] = sorted(remaining)
        receipt()
        raise RetirementBlocked("post-delete inventory does not match allowed refs")
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("operation", choices=["validate", "execute"])
    args = p.parse_args()
    try:
        data = read_manifest()
        if args.operation == "validate":
            print(json.dumps({
                "status": "VALIDATED", "branches": len(data["branches"]),
                "manifest_sha256": sha256((ROOT / MANIFEST).read_bytes()).hexdigest(),
            }))
        else:
            result = execute(data)
            print(json.dumps({k: v for k, v in result.items() if k != "removed"}))
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)[:220]}))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
