"""Bounded GitHub-hosted self-repair of real WLS source, not mock generations.

Uses an existing deterministic repair tool; candidate is independently tested
before a separate credentials-free promotion step can apply the exact patch.
No inference endpoint, privileged runtime state or user's PC is required.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess  # nosec B404 -- fixed trusted command vectors
import sys
from pathlib import Path

# New generations discover the next source-only, safe auto-fix target.
# Candidate models cannot alter tests, workflow security or the promotion gate.
ROOTS = ("source/src/wls", "source/scripts")
MAX_FILES_PER_GENERATION = 2
MAX_PATCH_BYTES_PER_GENERATION = 128_000
BLOCKED_FILES = frozenset({
    "source/scripts/github_rsi_autorepair.py",
    "source/scripts/verify_rsi_evaluator_change_boundary.py",
    "source/scripts/github_rsi_local_model.py",
    "source/scripts/github_rsi_replay_model.py",
    "source/src/wls/evidence.py",
    "source/src/wls/db.py",
    "source/src/wls/rsi_artifact_gate.py",
    "source/src/wls/benchmark.py",
    "source/src/wls/evaluator.py",
    "source/src/wls/experiment_decision.py",
    "source/src/wls/rsi_evolution.py",
    "source/src/wls/schemas.py",
    "source/scripts/generate_ci_feedback.py",
    "source/src/wls/task_admission.py",
    "source/scripts/run_external_risk_holdout.py",
})


def call(*args: str, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 -- trusted static tool argv
        list(args), check=False, capture_output=True, text=True, timeout=timeout
    )


def diagnostics(*files: str) -> list[dict[str, object]]:
    result = call(
        sys.executable, "-m", "ruff", "check", "--output-format", "json",
        *files,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("Ruff could not assess existing source")
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, list):
        raise TypeError("Ruff diagnostics were not a list")
    return parsed


def select_scope() -> tuple[str, ...]:
    """Select the highest safe-fixable *tracked source* faults autonomously."""
    tracked = set(call("git", "ls-files", "--", *ROOTS).stdout.splitlines())
    counts: dict[str, int] = {}
    root = Path.cwd().resolve()
    for item in diagnostics(*ROOTS):
        if not isinstance(item, dict):
            continue
        fix = item.get("fix")
        if not isinstance(fix, dict) or fix.get("applicability") != "safe":
            continue
        filename = item.get("filename")
        if not isinstance(filename, str):
            continue
        try:
            relative = Path(filename).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if (
            relative not in tracked or relative in BLOCKED_FILES
            or not relative.endswith(".py")
            or not any(relative.startswith(prefix + "/") for prefix in ROOTS)
            or Path(relative).is_symlink()
        ):
            continue
        counts[relative] = counts.get(relative, 0) + 1
    ordered = sorted(counts, key=lambda file: (-counts[file], file))
    return tuple(ordered[:MAX_FILES_PER_GENERATION])


def lint_count(*files: str) -> int:
    return len(diagnostics(*files))


def no_gain(base_sha: str, scope: tuple[str, ...], score: int) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "wls.github_rsi_autorepair.v1",
        "base_sha": base_sha,
        "repair_operator": "ruff-fix",
        "status": "NO_GAIN",
        "scope": list(scope),
        "changed_files": [],
        "before_ruff_issues": score,
        "after_ruff_issues": score,
        "gain": 0,
        "eligible": False,
        "claims": "no safe fix available in next generation; code unchanged",
    }
    Path("rsi-repair-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with Path(path).open("a", encoding="utf-8") as output:
            output.write("eligible=false\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


def run() -> dict[str, object]:
    if call("git", "status", "--porcelain").stdout.strip():
        raise RuntimeError("repair requires a clean, checked-out GitHub snapshot")
    original_sha = call("git", "rev-parse", "HEAD")
    if original_sha.returncode:
        raise RuntimeError("not a git checkout")
    source_files = select_scope()
    for name in source_files:
        if not Path(name).is_file() or Path(name).is_symlink():
            raise ValueError("selected repair path missing or unsafe: " + name)
    before = lint_count(*source_files) if source_files else 0
    if not source_files:
        # No safe model-independent improvement is left anywhere in the
        # approved source roots; do not spend tokens or create empty commits.
        return no_gain(original_sha.stdout.strip(), (), before)

    # Repair is limited to independently documented mechanical transformations.
    # "ruff check --fix" may still return 1 because some legacy findings remain.
    fix = call(sys.executable, "-m", "ruff", "check", "--fix", *source_files)
    if fix.returncode not in {0, 1}:
        raise RuntimeError("repair tool failed unexpectedly")
    changed = call("git", "diff", "--name-only").stdout.splitlines()
    if not set(changed).issubset(source_files):
        raise RuntimeError("repair touched files outside frozen scope")
    after = lint_count(*source_files)
    if not changed and after == before:
        return no_gain(original_sha.stdout.strip(), source_files, before)
    if after >= before:
        raise RuntimeError("repair did not strictly lower frozen lint faults")

    diff = call("git", "diff", "--binary", "--", *source_files)
    if diff.returncode or not diff.stdout.strip():
        raise RuntimeError("repair patch missing")
    patch_bytes = diff.stdout.encode("utf-8")
    if len(patch_bytes) > MAX_PATCH_BYTES_PER_GENERATION:
        raise RuntimeError("bounded improvement patch size exceeded")
    Path("rsi-repair-candidate.patch").write_bytes(patch_bytes)

    # Grader is independent of the repair program. Work is rejected if even
    # one existing test fails. This is a live WLS source edit on hosted CI.
    tests = call(
        sys.executable, "-m", "pytest", "-q", "source/tests",
        "--maxfail=1", timeout=1200,
    )
    report: dict[str, object] = {
        "schema": "wls.github_rsi_autorepair.v1",
        "base_sha": original_sha.stdout.strip(),
        "repair_operator": "ruff-fix",
        "scope": list(source_files),
        "changed_files": changed,
        "before_ruff_issues": before,
        "after_ruff_issues": after,
        "gain": before - after,
        "test_exit_code": tests.returncode,
        "test_output_tail": (tests.stdout + tests.stderr)[-2000:],
        "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
        "patch_bytes": len(patch_bytes),
        "eligible": tests.returncode == 0,
        "claims": "bounded source lint improvement; not autonomous model coding",
    }
    Path("rsi-repair-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(f"eligible={'true' if report['eligible'] else 'false'}\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


if __name__ == "__main__":
    try:
        result = run()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        Path("rsi-repair-report.json").write_text(
            json.dumps({"eligible": False, "reason": f"{type(error).__name__}: {error}"}),
            encoding="utf-8",
        )
        print(f"RSI candidate refused: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    raise SystemExit(0 if result["eligible"] or result.get("status") == "NO_GAIN" else 1)
