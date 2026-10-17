"""One-time retirement of three already-merged WLS PR refs and this PR ref.

Called ONLY by main-push, pinned GitHub-hosted Windows workflow. No user PC,
secrets, owner data or model evaluation. Git DAG is permanently reachable
from main before the references are deleted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess  # nosec B404 - only fixed gh/git commands
import sys
from typing import Any
from urllib.parse import quote

REPO = "sangziwang91-design/workstation-living-system"
SELF = "maintenance/finalize-merged-pr-refs-20261017"
SOURCES = {
    "refactor/s1a-proven-unreferenced-modules-20261011": 107,
    "feat/r1-frozen-contract-preflight-20261011": 118,
    "feat/r1-real-windows-mutation-discovery-20261016": 119,
}
RECEIPT = Path("wls-final-branch-ref-cleanup-receipt.json")


class RefCleanupBlocked(ValueError):
    pass


def command(*args: str) -> str:
    try:
        proc = subprocess.run(  # nosec B603 fixed subprocess arguments
            args, capture_output=True, text=True, check=False, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RefCleanupBlocked("GitHub or local git request unavailable") from exc
    if proc.returncode:
        raise RefCleanupBlocked(
            f"command {args[0]} failed (exit {proc.returncode})"
        )
    return proc.stdout.strip()


def gh(path: str, *, method: str = "GET") -> Any:
    if not path.startswith(f"repos/{REPO}/") or method not in {"GET", "DELETE"}:
        raise RefCleanupBlocked("invalid GitHub API scope")
    output = command("gh", "api", "--method", method, path)
    return None if method == "DELETE" else json.loads(output)


def is_ancestor(sha: str, head: str) -> bool:
    try:
        proc = subprocess.run(  # nosec B603
            ["git", "merge-base", "--is-ancestor", sha, head],
            check=False, capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RefCleanupBlocked("could not inspect Git ancestry") from exc
    if proc.returncode not in (0, 1):
        raise RefCleanupBlocked("Git ancestry unavailable")
    return proc.returncode == 0


def preflight() -> tuple[str, dict[str, str]]:
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPO
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or os.environ.get("GITHUB_EVENT_NAME") != "push"
    ):
        raise RefCleanupBlocked("branch deletion requires trusted main push")
    head = command("git", "rev-parse", "HEAD")
    if head != os.environ.get("GITHUB_SHA"):
        raise RefCleanupBlocked("source is not the exact trusted main commit")
    all_branches = gh(f"repos/{REPO}/branches?per_page=100&page=1")
    expected_names = {*SOURCES, "main", SELF}
    if (not isinstance(all_branches, list)
            or {row["name"] for row in all_branches} != expected_names):
        raise RefCleanupBlocked("unexpected branch ref remains or appeared")
    if gh(f"repos/{REPO}/pulls?state=open&per_page=100&page=1"):
        raise RefCleanupBlocked("an active PR must never be pruned")
    if gh(f"repos/{REPO}/branches/main")["commit"]["sha"] != head:
        raise RefCleanupBlocked("main advanced since this action started")
    branches = {row["name"]: row for row in all_branches}
    archived = gh(f"repos/{REPO}/git/ref/tags/wls-legacy-branches-20261016")
    if archived.get("ref") != "refs/tags/wls-legacy-branches-20261016":
        raise RefCleanupBlocked("legacy permanent archive missing")

    result: dict[str, str] = {}
    for branch, pr_number in SOURCES.items():
        pr = gh(f"repos/{REPO}/pulls/{pr_number}")
        sha = pr["head"]["sha"]
        if (
            not pr.get("merged_at")
            or pr["head"]["ref"] != branch
            or pr["base"]["ref"] != "main"
            or branches[branch]["commit"]["sha"] != sha
            or branches[branch]["protected"]
            or not is_ancestor(sha, head)
        ):
            raise RefCleanupBlocked(f"PR #{pr_number} not safely merged")
        result[branch] = sha
    self_sha = branches[SELF]["commit"]["sha"]
    if branches[SELF]["protected"] or not is_ancestor(self_sha, head):
        raise RefCleanupBlocked("the cleanup PR is not merged and safe")
    result[SELF] = self_sha
    return head, result


def delete_exact(name: str, expected_sha: str) -> None:
    suffix = quote(name, safe="/")
    current = gh(f"repos/{REPO}/git/ref/heads/{suffix}")
    if current["object"]["sha"] != expected_sha:
        raise RefCleanupBlocked(f"ref moved during deletion: {name}")
    gh(f"repos/{REPO}/git/refs/heads/{suffix}", method="DELETE")


def main() -> int:
    receipt: dict[str, Any] = {
        "schema": "wls.final_pr_ref_cleanup.v1",
        "status": "BLOCKED",
        "deleted": [],
        "retained_main_only": False,
    }

    def store() -> None:
        RECEIPT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    try:
        head, refs = preflight()
        receipt["main"] = head
        receipt["candidate_refs"] = refs
        receipt["status"] = "VERIFIED_BEFORE_DELETION"
        store()
        for name, sha in refs.items():
            delete_exact(name, sha)
            receipt["deleted"].append(name)
            store()
        names = gh(f"repos/{REPO}/branches?per_page=100&page=1")
        if not isinstance(names, list) or [row["name"] for row in names] != ["main"]:
            raise RefCleanupBlocked("post-deletion branch inventory not main-only")
        receipt["status"] = "ALL_LEGACY_AND_PR_REFS_RETIRED"
        receipt["retained_main_only"] = True
        store()
    except (OSError, TypeError, KeyError, ValueError) as exc:
        receipt["status"] = "BLOCKED"
        receipt["reason"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        store()
        print(json.dumps({"status": "BLOCKED", "reason": receipt["reason"]}))
        return 2
    print(json.dumps({
        "status": receipt["status"], "deleted_count": len(receipt["deleted"]),
        "main_only": receipt["retained_main_only"],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
