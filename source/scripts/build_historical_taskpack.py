"""C2: mine real WLS merge history for a provisional *public-development* curriculum.

This produces worker-facing tasks and a separate trusted replay inventory.
It never mistakes a changed test for a sealed oracle or counts a historical
fix as a newly measured improvement.
"""
from __future__ import annotations

import argparse
import ast
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess  # nosec B404 -- fixed Git argv, no shell

SHA = re.compile(r"[0-9a-f]{40}\Z")
PR = re.compile(r"Merge pull request #(\d+)\b")
MAX_COMMITS = 300
MAX_TASKS = 40


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # nosec B603 -- fixed Git command
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=False, timeout=20,
    )
    if result.returncode:
        raise RuntimeError("git command failed: " + " ".join(args[:2]))
    return result.stdout.strip()


def _real_path(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path:
        return False
    parts = Path(path).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def trusted_grader_paths(repo: Path) -> frozenset[str]:
    """Parse the current frozen scorer catalog as data: do not execute it.

    A missing/dynamically constructed catalog fails closed instead of letting
    a scorer-altering public PR become a normal worker training task.
    """
    path = repo / "source/scripts/verify_rsi_evaluator_change_boundary.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ValueError("trusted evaluator catalog unavailable") from exc
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "EVALUATORS"
            for target in stmt.targets
        ):
            continue
        value = stmt.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and len(value.args) == 1 and not value.keywords
        ):
            raise ValueError("trusted evaluator catalog not a literal set")
        try:
            paths = ast.literal_eval(value.args[0])
        except (ValueError, TypeError, SyntaxError) as exc:
            raise ValueError("trusted evaluator catalog not literal") from exc
        if not isinstance(paths, set) or not paths or not all(
            isinstance(p, str) and _real_path(p) for p in paths
        ):
            raise ValueError("trusted evaluator catalog invalid")
        return frozenset(paths)
    raise ValueError("trusted evaluator catalog missing EVALUATORS")


def classify(paths: list[str]) -> tuple[list[str], list[str]]:
    if len(paths) != len(set(paths)) or not all(_real_path(p) for p in paths):
        raise ValueError("non-canonical or duplicate git paths")
    sources = sorted(p for p in paths if p.endswith(".py") and (
        p.startswith("source/src/wls/") or p.startswith("source/scripts/")))
    tests = sorted(
        p for p in paths if p.startswith("source/tests/test_") and p.endswith(".py")
    )
    return sources, tests


def mine(repo: Path, *, head: str, max_commits: int = 120,
         max_tasks: int = 20) -> dict:
    if not SHA.fullmatch(head):
        raise ValueError("freeze a full 40-character commit SHA")
    if not 1 <= max_commits <= MAX_COMMITS or not 1 <= max_tasks <= MAX_TASKS:
        raise ValueError("scan budgets exceeded")
    if git(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("shallow history is not adequate for task provenance")
    if git(repo, "rev-parse", "HEAD") != head:
        raise ValueError("HEAD moved relative to frozen taskpack")
    protected = trusted_grader_paths(repo)
    commits = git(
        repo, "log", "--first-parent", "--format=%H", "-n", str(max_commits), head
    )
    tasks: list[dict] = []
    skips: dict[str, int] = {}
    for commit in commits.splitlines():
        ancestry = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
        if len(ancestry) != 3:
            skips["not_two_parent_merge"] = skips.get("not_two_parent_merge", 0) + 1
            continue
        parent = ancestry[1]
        subject = git(repo, "log", "-1", "--format=%s", commit)
        match = PR.search(subject)
        if match is None:
            skips["not_pr_merge"] = skips.get("not_pr_merge", 0) + 1
            continue
        paths = git(
            repo, "diff", "--name-only", "--diff-filter=AM", parent, commit
        ).splitlines()
        sources, tests = classify(paths)
        if set(paths) & protected:
            skips["scorer_authority_changed"] = skips.get("scorer_authority_changed", 0) + 1
            continue
        if not sources or not tests:
            skips["no_source_and_test_pair"] = (
                skips.get("no_source_and_test_pair", 0) + 1
            )
            continue
        if len(sources) > 12 or len(tests) > 15:
            skips["oversized_change"] = skips.get("oversized_change", 0) + 1
            continue
        # Only tests already present before the fix can identify a historic
        # regression. New tests need separate independent replay.
        existing_tests = [
            p for p in tests
            if git(repo, "ls-tree", "--name-only", parent, "--", p) == p
        ]
        if not existing_tests:
            skips["test_added_after_fix"] = skips.get("test_added_after_fix", 0) + 1
            continue
        task_id = sha256((parent + ":" + commit).encode()).hexdigest()[:16]
        tasks.append({
            "task_id": task_id, "pr_number": int(match.group(1)),
            "base_sha": parent, "fix_sha": commit,
            "source_paths": sources, "oracle_test_paths": existing_tests,
            "status": "REPLAY_NOT_YET_VERIFIED",
            "oracle_access": "trusted_replay_only",
        })
        if len(tasks) >= max_tasks:
            break
    public_tasks = [{
        "task_id": task["task_id"],
        "base_sha": task["base_sha"],
        "source_paths": task["source_paths"],
        "instruction": "Diagnose and repair a documented WLS regression in these source paths.",
        "oracle_visibility": "withheld_from_candidate",
    } for task in tasks]
    return {
        "schema": "wls.c2_historical_taskpack.v1",
        "head_sha": head,
        "status": "PROVISIONAL_HISTORY_MINED" if tasks else "NO_ELIGIBLE_HISTORY",
        "source_type": "real_first_parent_github_history",
        "public_development_only": True,
        "independent_successes": 0,
        "tasks": public_tasks,
        "trusted_oracles": tasks,
        "skips": dict(sorted(skips.items())),
        "claim_ceiling": "curriculum_candidates_only_no_benchmark_or_RSI_claim",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--head", required=True)
    parser.add_argument("--max-commits", type=int, default=120)
    parser.add_argument("--max-tasks", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("wls-c2-worker-tasks.json"))
    parser.add_argument("--trusted-output", type=Path,
                        default=Path("wls-c2-trusted-oracles.json"))
    args = parser.parse_args()
    report = mine(
        args.repo.resolve(), head=args.head,
        max_commits=args.max_commits, max_tasks=args.max_tasks,
    )
    # Public-history oracles are discoverable, not *sealed*. Nonetheless
    # separate worker input from trusted comparison metadata to prevent
    # accidental answer injection through the normal execution channel.
    worker = {key: val for key, val in report.items() if key != "trusted_oracles"}
    trusted = {
        "schema": report["schema"], "head_sha": report["head_sha"],
        "trusted_oracles": report["trusted_oracles"],
        "access": "public_history_not_a_secret_holdout",
    }
    args.output.write_text(
        json.dumps(worker, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    args.trusted_output.write_text(
        json.dumps(trusted, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": report["status"], "candidate_tasks": len(report["tasks"]),
        "source_type": report["source_type"],
    }, sort_keys=True))
    return 0 if report["tasks"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
