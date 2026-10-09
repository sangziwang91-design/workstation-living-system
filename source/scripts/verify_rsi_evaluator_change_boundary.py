"""Fail-closed scorer/code PR separation. Trusted base-branch execution only."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import PurePosixPath
from urllib.request import Request, urlopen

EVALUATORS = frozenset({
    "source/src/wls/task_admission.py",
    "source/src/wls/benchmark.py",
    "source/src/wls/evaluator.py",
    "source/src/wls/experiment_decision.py",
    "source/scripts/github_rsi_replay_model.py",
    "source/scripts/run_external_risk_holdout.py",
})
CODE_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go",
    ".sh", ".ps1", ".yml", ".yaml", ".toml", ".json",
})
MAX_FILES = 3000


def is_other_code(path: str) -> bool:
    # GitHub's file list is repository-global. Limiting classification to
    # source/ or .github/ lets a PR conceal implementation under scripts/,
    # tools/, nested packages or a root-level entrypoint while also editing
    # the evaluator. Fail closed on executable/config suffixes *anywhere*.
    # CURRENT_STATE is an advisory owner-facing status record, not code.
    if path in {"README.md", "CURRENT_STATE.yaml"}:
        return False
    return (
        path == ".github/CODEOWNERS"
        or PurePosixPath(path).suffix.lower() in CODE_SUFFIXES
        or PurePosixPath(path).name in {
            "Dockerfile", "Makefile", "tox.ini", "pytest.ini",
            "uv.lock", "requirements.txt",
        }
    )


def check_files(files: list[str]) -> dict:
    if not files or len(files) > MAX_FILES:
        return {"eligible": False, "status": "UNMEASURED",
                "reason": "missing or truncated changed-file listing"}
    changed = set(files)
    scoring = sorted(changed & EVALUATORS)
    other = sorted(path for path in changed if path not in EVALUATORS
                   and is_other_code(path))
    mixed = bool(scoring and other)
    return {
        "eligible": not mixed,
        "status": "REJECT_MIXED_EVALUATOR_AND_CODE" if mixed else "PASS",
        "evaluator_files": scoring,
        "other_code_files": other,
    }


def fetch_pr_files(repo: str, pr: int, token: str) -> list[str]:
    """No PR checkout, downloaded source, or execution of candidate code."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    if not token or pr < 1:
        raise ValueError("missing token or PR number")
    files: list[str] = []
    changed_names: list[str] = []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "WLS-evaluator-change-guard",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    for page in range(1, 32):
        request = Request(
            f"https://api.github.com/repos/{repo}/pulls/{pr}/files"
            f"?per_page=100&page={page}",
            headers=headers,
        )
        with urlopen(request, timeout=20) as response:  # nosec B310 trusted API
            rows = json.load(response)
        if not isinstance(rows, list):
            raise ValueError("invalid GitHub PR files")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("filename"), str):
                raise ValueError("malformed PR path")
            files.append(row["filename"])
            changed_names.append(row["filename"])
            if row.get("status") == "renamed":
                previous = row.get("previous_filename")
                if not isinstance(previous, str):
                    raise ValueError("rename missing previous filename")
                files.append(previous)
        if len(files) > MAX_FILES:
            raise ValueError("changed-file count exceeds safe bound")
        if len(rows) < 100:
            # GitHub's changed-files endpoint is capped at 3,000. Verify
            # completeness against independent PR metadata, fail closed.
            detail = Request(
                f"https://api.github.com/repos/{repo}/pulls/{pr}",
                headers=headers,
            )
            with urlopen(detail, timeout=20) as response:  # nosec B310 pinned host
                metadata = json.load(response)
            actual = metadata.get("changed_files") if isinstance(metadata, dict) else None
            if type(actual) is not int or actual > MAX_FILES or actual != len(set(changed_names)):
                raise ValueError("PR file list incomplete versus GitHub changed_files count")
            return files
    raise ValueError("GitHub file-list pagination overflow")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-pr", type=int)
    parser.add_argument("--repository")
    parser.add_argument("--fixture-file", help="test fixture only; not CI authority")
    args = parser.parse_args()
    try:
        if args.fixture_file and args.github_pr is None:
            with open(args.fixture_file, encoding="utf-8") as handle:
                files = json.load(handle)
            if not isinstance(files, list) or not all(
                isinstance(x, str) for x in files
            ):
                raise ValueError("fixture not a list of paths")
        elif args.github_pr and not args.fixture_file:
            files = fetch_pr_files(
                args.repository or os.environ.get("GITHUB_REPOSITORY", ""),
                args.github_pr, os.environ.get("GH_TOKEN", ""),
            )
        else:
            raise ValueError("must select exactly one input mode")
        result = check_files(files)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["eligible"] else 2
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"eligible": False, "status": "UNMEASURED",
                          "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
