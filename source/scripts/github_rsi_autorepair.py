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

SOURCE_FILES = (
    "source/scripts/build_capability_inventory.py",
    "source/scripts/build_delivery_handoff.py",
)


def call(*args: str, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 -- trusted static tool argv
        list(args), check=False, capture_output=True, text=True, timeout=timeout
    )


def lint_count() -> int:
    result = call(
        sys.executable, "-m", "ruff", "check", "--output-format", "json",
        *SOURCE_FILES,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("Ruff could not assess the existing source")
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, list):
        raise TypeError("Ruff diagnostics were not a list")
    return len(parsed)


def run() -> dict[str, object]:
    for name in SOURCE_FILES:
        if not Path(name).is_file() or Path(name).is_symlink():
            raise ValueError("frozen repair path missing or unsafe: " + name)
    if call("git", "status", "--porcelain").stdout.strip():
        raise RuntimeError("repair requires a clean, checked-out GitHub snapshot")
    original_sha = call("git", "rev-parse", "HEAD")
    if original_sha.returncode:
        raise RuntimeError("not a git checkout")
    before = lint_count()

    # Repair is limited to independently documented mechanical transformations.
    # "ruff check --fix" may still return 1 because some legacy findings remain.
    fix = call(sys.executable, "-m", "ruff", "check", "--fix", *SOURCE_FILES)
    if fix.returncode not in {0, 1}:
        raise RuntimeError("repair tool failed unexpectedly")
    changed = call("git", "diff", "--name-only").stdout.splitlines()
    if not changed or not set(changed).issubset(SOURCE_FILES):
        raise RuntimeError("repair did not produce a bounded source-only candidate")
    after = lint_count()
    if after >= before:
        raise RuntimeError("repair did not strictly lower frozen lint faults")

    diff = call("git", "diff", "--binary", "--", *SOURCE_FILES)
    if diff.returncode or not diff.stdout.strip():
        raise RuntimeError("repair patch missing")
    patch_bytes = diff.stdout.encode("utf-8")
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
        "scope": list(SOURCE_FILES),
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
    raise SystemExit(0 if result["eligible"] else 1)
