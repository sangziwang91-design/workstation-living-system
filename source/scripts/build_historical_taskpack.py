"""C2: mine real WLS merge history for a provisional *public-development* curriculum.

This produces worker-facing tasks and a separate trusted replay inventory.
It never mistakes a changed test for a sealed oracle or counts a historical
fix as a newly measured improvement.
"""
from __future__ import annotations

import argparse
import ast
import os
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess  # nosec B404 -- fixed Git argv, no shell
import sys
from tempfile import TemporaryDirectory

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


def public_issue_hint(message: str) -> str | None:
    """A bounded, untrusted PR title as task context, never an instruction.

    Do not inject fix patches, test oracles, links, or PR numbers into the
    worker prompt. Ambiguous or potentially directive titles are withheld.
    """
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if len(lines) < 2 or PR.search(lines[0]) is None:
        return None
    candidate = " ".join(lines[1].split())
    if not 8 <= len(candidate) <= 140:
        return None
    # No line breaks, markdown blocks, shell/template syntax, or directives.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ,.:;()/+_-]*", candidate):
        return None
    blocked = ("ignore previous", "system prompt", "api key", "secret",
               "password", "execute this", "run this", "follow these", "rm -rf")
    if any(phrase in candidate.casefold() for phrase in blocked):
        return None
    return candidate


def classify(paths: list[str]) -> tuple[list[str], list[str]]:
    if len(paths) != len(set(paths)) or not all(_real_path(p) for p in paths):
        raise ValueError("non-canonical or duplicate git paths")
    sources = sorted(p for p in paths if p.endswith(".py") and (
        p.startswith("source/src/wls/") or p.startswith("source/scripts/")))
    tests = sorted(
        p for p in paths if p.startswith("source/tests/test_") and p.endswith(".py")
    )
    return sources, tests


def unchanged_preexisting_oracles(
    repo: Path, *, parent: str, fix: str, sources: list[str],
    limit: int = 3,
) -> list[str]:
    """Discover tests which reference a changed module but were NOT edited.

    Git grep reads immutable fixed-revision test text only; no untrusted test
    execution, oracle disclosure to workers, or heuristic success claims.
    The three-way replay remains the sole qualification authority.
    """
    if (not SHA.fullmatch(parent) or not SHA.fullmatch(fix)
            or not 1 <= limit <= 5 or not 1 <= len(sources) <= 2):
        raise ValueError("invalid source-only discovery budget or provenance")
    candidates: set[str] = set()
    for source in sources:
        if not _real_path(source) or not source.endswith(".py"):
            raise ValueError("invalid candidate source path")
        module = Path(source).stem
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", module):
            continue
        needle = ("wls." + module if source.startswith("source/src/wls/")
                  else module)
        query = subprocess.run(  # nosec B603 -- fixed read-only git argv
            ["git", "-C", str(repo), "grep", "-l", "-F", "-e", needle,
             fix, "--", "source/tests"],
            check=False, capture_output=True, text=True, timeout=20,
        )
        if query.returncode == 1:  # a legitimate absence of matches
            continue
        if query.returncode != 0:
            raise RuntimeError("read-only historical test grep failed")
        for item in query.stdout.splitlines():
            prefix = fix + ":"
            if not item.startswith(prefix):
                raise ValueError("historical git grep revision drift")
            test = item[len(prefix):]
            if not (test.startswith("source/tests/test_")
                    and test.endswith(".py") and _real_path(test)):
                continue
            if git(repo, "ls-tree", "--name-only", parent, "--", test) != test:
                continue
            # The test is present before the change and its exact Git blob
            # remains unchanged. Avoid classifying a newly invented test as
            # a pre-existing independent regression.
            if git(repo, "rev-parse", parent + ":" + test) != git(
                repo, "rev-parse", fix + ":" + test
            ):
                continue
            candidates.add(test)
            if len(candidates) >= limit:
                return sorted(candidates)[:limit]
    return sorted(candidates)[:limit]


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
    source_only_count = 0
    for commit in commits.splitlines():
        ancestry = git(repo, "rev-list", "--parents", "-n", "1", commit).split()
        if len(ancestry) != 3:
            skips["not_two_parent_merge"] = skips.get("not_two_parent_merge", 0) + 1
            continue
        parent = ancestry[1]
        subject = git(repo, "log", "-1", "--format=%s", commit)
        message = git(repo, "log", "-1", "--format=%B", commit)
        match = PR.search(subject)
        if match is None:
            skips["not_pr_merge"] = skips.get("not_pr_merge", 0) + 1
            continue
        # Classify *all* historical edits before restricting to files that
        # still exist. A deletion or rename of a trusted scorer also changes
        # the evaluation standard, even though diff-filter=AM hides removals.
        all_paths = git(
            repo, "diff", "--name-only", "--no-renames", parent, commit
        ).splitlines()
        if set(all_paths) & protected:
            skips["scorer_authority_changed"] = skips.get("scorer_authority_changed", 0) + 1
            continue
        paths = git(
            repo, "diff", "--name-only", "--diff-filter=AM", parent, commit
        ).splitlines()
        sources, tests = classify(paths)
        if not sources:
            skips["no_source_changes"] = skips.get("no_source_changes", 0) + 1
            continue
        # Do not require a changed test in a true source-only repair:
        # an unmodified pre-existing test can be *stronger* qualification
        # evidence than a new test authored in the same PR as its fix.
        source_only = not tests
        if source_only:
            if source_only_count >= 4 or len(sources) > 2:
                skips["source_only_budget_or_scope"] = (
                    skips.get("source_only_budget_or_scope", 0) + 1
                )
                continue
            tests = unchanged_preexisting_oracles(
                repo, parent=parent, fix=commit, sources=sources, limit=3,
            )
            if not tests:
                skips["source_only_no_existing_test"] = (
                    skips.get("source_only_no_existing_test", 0) + 1
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
        if source_only:
            source_only_count += 1
        tasks.append({
            "task_id": task_id, "pr_number": int(match.group(1)),
            "base_sha": parent, "fix_sha": commit,
            "source_paths": sources, "oracle_test_paths": existing_tests,
            "oracle_origin": (
                "unchanged_preexisting_test" if source_only
                else "preexisting_but_modified_in_fix"
            ),
            "issue_hint": public_issue_hint(message),
            "status": "REPLAY_NOT_YET_VERIFIED",
            "oracle_access": "trusted_replay_only",
        })
        if len(tasks) >= max_tasks:
            break
    public_tasks = [{
        "task_id": task["task_id"],
        "base_sha": task["base_sha"],
        "source_paths": task["source_paths"],
        "instruction": "Reproduce the documented public WLS behavior change in these source paths.",
        "preexisting_regression_proven": False,
        "task_kind": "PUBLIC_HISTORICAL_CHANGE_NOT_HOLDOUT",
        "untrusted_public_issue_summary": task["issue_hint"],
        "oracle_visibility": "withheld_from_candidate",
    } for task in tasks]
    return {
        "schema": "wls.c2_historical_taskpack.v1",
        "head_sha": head,
        "status": "PROVISIONAL_HISTORY_MINED" if tasks else "NO_ELIGIBLE_HISTORY",
        "source_type": "real_first_parent_github_history",
        "public_development_only": True,
        "independent_successes": 0,
        "source_only_candidates": source_only_count,
        "tasks": public_tasks,
        "trusted_oracles": tasks,
        "skips": dict(sorted(skips.items())),
        "claim_ceiling": "curriculum_candidates_only_no_benchmark_or_RSI_claim",
    }


def replay_two_sided(repo: Path, taskpack: dict, *, limit: int = 3,
                     test_timeout: int = 45) -> dict:
    """Three-way public regression qualification, no hidden holdout.

    Detached Git worktrees and tests run without API credentials; the stored
    reference commit remains public and is NOT an unseen model holdout.
    """
    if not 1 <= limit <= 40 or not 5 <= test_timeout <= 90:
        raise ValueError("unbounded historical replay budget")
    if taskpack.get("schema") != "wls.c2_historical_taskpack.v1":
        raise ValueError("unsupported frozen taskpack")
    if git(repo, "rev-parse", "HEAD") != taskpack.get("head_sha"):
        raise ValueError("HEAD moved since C2 taskpack was frozen")
    output = []
    for task in taskpack["trusted_oracles"][:limit]:
        oracle = task["oracle_test_paths"][0]
        if not oracle.startswith("source/tests/test_") or not _real_path(oracle):
            raise ValueError("invalid public historical oracle")
        if not all(SHA.fullmatch(task[key]) for key in ("base_sha", "fix_sha")):
            raise ValueError("invalid frozen public historical SHA")
        row = {"task_id": task["task_id"], "qualification": "BLOCKED",
               "same_oracle_bytes": False, "public_development_only": True}
        with TemporaryDirectory(prefix="wls-public-c2-") as temp:
            root = Path(temp)
            pre, reference = root / "pre", root / "reference"
            attached = []
            try:
                for revision, target in (
                    (task["base_sha"], pre), (task["fix_sha"], reference)
                ):
                    x = subprocess.run(  # nosec B603 -- fixed argv
                        ["git", "-C", str(repo), "worktree", "add", "--detach",
                         str(target), revision],
                        check=False, capture_output=True, text=True, timeout=30,
                    )
                    if x.returncode:
                        raise ValueError("historic worktree checkout failed")
                    attached.append(target)
                if not (pre / oracle).is_file() or not (reference / oracle).is_file():
                    raise ValueError("missing preexisting oracle")
                if ((pre / oracle).stat().st_size > 150_000
                        or (reference / oracle).stat().st_size > 150_000):
                    raise ValueError("oversized historical oracle")
                oracle_bytes = (reference / oracle).read_bytes()
                original_test = (pre / oracle).read_bytes()
                row["original_test_sha256"] = sha256(original_test).hexdigest()
                row["reference_test_sha256"] = sha256(oracle_bytes).hexdigest()
                row["oracle_changed"] = original_test != oracle_bytes
                row["test_process_invocations"] = 0
                def execute(target: Path) -> int:
                    row["test_process_invocations"] += 1
                    env = {key: value for key, value in os.environ.items()
                           if key in {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT",
                                      "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
                    env["PYTHONPATH"] = str(target / "source/src")
                    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
                    env["PYTHONDONTWRITEBYTECODE"] = "1"
                    for key in list(env):
                        if key.endswith("_TOKEN") or key.endswith("_API_KEY"):
                            env.pop(key, None)
                    try:
                        run = subprocess.run(  # nosec B603 -- trusted test argv
                            [sys.executable, "-m", "pytest", "-q", "--maxfail=1",
                             oracle], cwd=target, env=env, check=False,
                            capture_output=True, text=True, timeout=test_timeout,
                        )
                        return run.returncode
                    except subprocess.TimeoutExpired:
                        return 124

                # Test the ORIGINAL historical oracle against the ORIGINAL
                # code first; new post-fix expectations alone do not establish
                # that a pre-existing regression was repaired.
                original_exit = execute(pre)
                (pre / oracle).write_bytes(oracle_bytes)
                row["same_oracle_bytes"] = (pre / oracle).read_bytes() == oracle_bytes
                baseline_exit = execute(pre)
                reference_exit = execute(reference)
                row["original_baseline_exit"] = original_exit
                row["baseline_exit"] = baseline_exit
                row["reference_exit"] = reference_exit
                if (row["same_oracle_bytes"] and original_exit == 1
                        and baseline_exit == 1 and reference_exit == 0):
                    row["qualification"] = "PREEXISTING_REGRESSION"
                elif original_exit == 0 and baseline_exit == 1 and reference_exit == 0:
                    row["qualification"] = "NEW_TEST_EXPECTATION_ONLY"
                elif baseline_exit == 0:
                    row["qualification"] = "BASELINE_ALREADY_PASSES"
                else:
                    row["qualification"] = "REFERENCE_UNQUALIFIED"
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                row["qualification"] = "REPLAY_BLOCKED"
                row["error_type"] = type(exc).__name__
            finally:
                for target in reversed(attached):
                    subprocess.run(  # nosec B603 -- ephemeral worktree cleanup
                        ["git", "-C", str(repo), "worktree", "remove", "--force",
                         str(target)],
                        check=False, capture_output=True, text=True, timeout=30,
                    )
        output.append(row)
    qualified = sum(r["qualification"] == "PREEXISTING_REGRESSION"
                    for r in output)
    new_only = sum(r["qualification"] == "NEW_TEST_EXPECTATION_ONLY" for r in output)
    return {"schema": "wls.c2_public_three_way.v2",
            "status": "PREEXISTING_REGRESSIONS_FOUND" if qualified else "NO_VERIFIED_PREEXISTING_REGRESSIONS",
            "qualified": qualified, "new_test_expectation_only": new_only,
            "total": len(output), "test_process_invocations": sum(
                row.get("test_process_invocations", 0) for row in output),
            "independent_holdout": False, "model_calls": 0,
            "claim_ceiling": "PUBLIC_HISTORY_ONLY_NO_MODEL_OR_RSI_GAIN",
            "tasks": output}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--head", required=True)
    parser.add_argument("--max-commits", type=int, default=120)
    parser.add_argument("--max-tasks", type=int, default=20)
    parser.add_argument("--two-sided", action="store_true")
    parser.add_argument("--two-sided-limit", type=int, default=3)
    parser.add_argument("--two-sided-timeout", type=int, default=45)
    parser.add_argument("--two-sided-output", type=Path,
                        default=Path("wls-c2-two-sided.json"))
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
    if args.two_sided:
        qualification = replay_two_sided(
            args.repo.resolve(), report, limit=args.two_sided_limit,
            test_timeout=args.two_sided_timeout,
        )
        args.two_sided_output.write_text(
            json.dumps(qualification, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"preexisting_regressions": qualification["qualified"],
                          "new_test_expectation_only": qualification["new_test_expectation_only"],
                          "three_way_total": qualification["total"]}, sort_keys=True))
    print(json.dumps({
        "status": report["status"], "candidate_tasks": len(report["tasks"]),
        "source_type": report["source_type"],
    }, sort_keys=True))
    return 0 if report["tasks"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
