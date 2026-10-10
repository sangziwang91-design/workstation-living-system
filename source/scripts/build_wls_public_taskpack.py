"""Mine bounded PUBLIC training tasks from WLS's own merged Git history.

The pre-fix checkout is the task starting point. The merge/fix SHA is stored
only as an auditor reference, NEVER passed as a candidate's task prompt.
Nothing mined from public repository history is a private benchmark or proof
of transfer. The script reads git objects only and executes no mined code.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Any

SCHEMA = "wls.public_repair_taskpack.v1"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
SAFE_PATH = re.compile(r"[A-Za-z0-9_.\-/]+\Z")
PR_NUMBER = re.compile(r"Merge pull request #(\d+)\b")
MAX_MERGES = 120
MAX_TASKS = 24
MAX_STDOUT = 250_000
MAX_TITLE = 180


def git(repo: Path, *args: str, allow_missing: bool = False) -> str | None:
    """Query fixed local Git object operations without shell/network hooks."""
    try:
        proc = subprocess.run(  # nosec B603: fixed git program, no shell
            ["git", "-C", str(repo), *args],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("local git history unavailable") from exc
    if proc.returncode:
        if allow_missing:
            return None
        raise ValueError("git history query failed")
    if len(proc.stdout) > MAX_STDOUT:
        raise ValueError("git response exceeds bounded scan")
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("git result is not UTF-8") from exc


def _paths(text: str) -> list[str]:
    files = text.splitlines()
    if len(files) > 180:
        raise ValueError("merge changes too many files")
    for name in files:
        if (not SAFE_PATH.fullmatch(name)
                or ".." in Path(name).parts
                or name.startswith("/")):
            raise ValueError("unsafe path in historical merge")
    return sorted(set(files))


def _grader_paths(repo: Path) -> set[str]:
    """Read the canonical frozen scoring catalog, rather than a shadow list."""
    import runpy

    catalog = repo / "source/scripts/verify_rsi_evaluator_change_boundary.py"
    if not catalog.is_file():
        raise ValueError("trusted grader catalog unavailable")
    data = runpy.run_path(str(catalog), run_name="wls_taskpack_catalog")
    paths = data.get("EVALUATORS")
    if not isinstance(paths, (set, frozenset)) or not all(
        isinstance(p, str) for p in paths
    ):
        raise ValueError("invalid trusted grader catalog")
    return set(paths)


def _title(message: str) -> str | None:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if len(lines) < 2 or not PR_NUMBER.search(lines[0]):
        return None
    title = lines[1].replace("\x00", "")
    if (not title or len(title) > MAX_TITLE
            or any(ord(c) < 32 for c in title)):
        return None
    return title


def _merge_task(repo: Path, merge_sha: str, grader_paths: set[str]) -> dict[str, Any] | None:
    if not HEX40.fullmatch(merge_sha):
        raise ValueError("invalid historical merge SHA")
    parents = git(repo, "rev-list", "--parents", "-n", "1", merge_sha)
    if parents is None:
        raise ValueError("missing merge ancestry")
    words = parents.strip().split()
    if len(words) != 3 or words[0] != merge_sha:
        return None  # No ordinary two-parent merge means no pre-fix baseline.
    pre_fix = words[1]
    if not HEX40.fullmatch(pre_fix) or not HEX40.fullmatch(words[2]):
        raise ValueError("invalid immutable merge ancestry")
    message = git(repo, "show", "-s", "--format=%B", merge_sha)
    title = _title(message or "")
    if title is None:
        return None
    diff = git(repo, "diff", "--name-only", "--no-ext-diff", pre_fix, merge_sha)
    if diff is None:
        raise ValueError("missing merge diff")
    changed = _paths(diff)
    # Score-authority mutations must not become the agent's practice target.
    if set(changed) & grader_paths:
        return None
    source = [
        path for path in changed
        if (path.startswith("source/src/wls/") or path.startswith("source/scripts/"))
        and path.endswith(".py")
    ]
    tests = [
        path for path in changed
        if path.startswith("source/tests/test_") and path.endswith(".py")
    ]
    if not source or not tests:
        return None
    preexisting_tests = []
    for path in tests:
        result = git(repo, "cat-file", "-e", f"{pre_fix}:{path}", allow_missing=True)
        # A successful 'git cat-file -e' prints nothing.
        if result is not None:
            preexisting_tests.append(path)
    number = PR_NUMBER.search(message.splitlines()[0])
    if number is None:
        return None
    task_id = sha256((merge_sha + ":" + title).encode("utf-8")).hexdigest()[:20]
    return {
        "task_id": task_id,
        "pr_number": int(number.group(1)),
        "title": title,
        "pre_fix_sha": pre_fix,
        "fix_merge_sha": merge_sha,
        "source_files": source[:20],
        "changed_test_files": tests[:20],
        "preexisting_test_files": preexisting_tests[:20],
        "replay_status": "UNMEASURED",
        "split": "PUBLIC_DEVELOPMENT_ONLY",
        "hint_scope": "title_and_pre_fix_checkout_only",
        "claim_ceiling": "candidate_replay_task_not_verified_red_to_green",
    }


def build_taskpack(repo: Path, *, head: str, limit: int = MAX_TASKS) -> dict[str, Any]:
    if not HEX40.fullmatch(head):
        raise ValueError("head must be an immutable commit SHA")
    if not 1 <= limit <= MAX_TASKS:
        raise ValueError("task limit outside budget")
    repo = repo.resolve()
    if git(repo, "rev-parse", "HEAD").strip() != head:
        raise ValueError("taskpack source HEAD changed")
    protected = _grader_paths(repo)
    history = git(
        repo, "log", "--first-parent", "--merges", "--format=%H",
        "-n", str(MAX_MERGES), head,
    )
    if history is None:
        raise ValueError("missing first-parent history")
    tasks: list[dict[str, Any]] = []
    seen_pr: set[int] = set()
    for commit in history.splitlines():
        task = _merge_task(repo, commit, protected)
        if task is None or task["pr_number"] in seen_pr:
            continue
        seen_pr.add(task["pr_number"])
        tasks.append(task)
        if len(tasks) >= limit:
            break
    return {
        "schema": SCHEMA, "head_sha": head,
        "task_count": len(tasks), "tasks": tasks,
        "status": "PUBLIC_TASKS_DISCOVERED" if tasks else "NO_ELIGIBLE_HISTORY",
        "source_type": "git_first_parent_two_parent_merge_history",
        "authority": "training_only_no_code_execution_or_promotion",
        "sealing": {
            "independent_holdout": False,
            "solutions_publicly_accessible": True,
            "pre_fix_tests_replayed": False,
            "eligible_for_rsi_gain_claim": False,
        },
    }


def candidate_view(task: dict[str, Any]) -> dict[str, Any]:
    """Expose no fix commit or code patch to the *normal* worker prompt."""
    return {
        "task_id": task["task_id"], "title": task["title"],
        "checkout_sha": task["pre_fix_sha"],
        "candidate_test_paths": task["preexisting_test_files"],
        "source_type": "public_historical_training",
        "answer_provided": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--head", required=True)
    parser.add_argument("--limit", type=int, default=MAX_TASKS)
    parser.add_argument("--output", type=Path, default=Path("wls-public-taskpack.json"))
    parser.add_argument("--worker-output", type=Path, default=Path("wls-public-worker-tasks.json"))
    args = parser.parse_args()
    try:
        report = build_taskpack(args.repo, head=args.head, limit=args.limit)
    except ValueError as error:
        report = {"schema": SCHEMA, "status": "UNMEASURED",
                  "error_type": type(error).__name__, "tasks": []}
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.worker_output.write_text(json.dumps({
        "schema": "wls.public_worker_tasks.v1",
        "head_sha": report.get("head_sha"),
        "status": report["status"],
        "tasks": [candidate_view(task) for task in report["tasks"]],
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "task_count": len(report["tasks"])}))
    return 2 if report["status"] == "UNMEASURED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
