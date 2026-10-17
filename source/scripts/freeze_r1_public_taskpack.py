"""R1 public mutation task *identity* preflight; no success scoring authority.

Seals source, tests, task order, candidate order, evaluator files and an
environment signature. A seal does NOT prove mutations are detectable three
times, nor that a model improved. Nuomi's private holdout never enters Git.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess  # nosec B404: local git provenance checks, no shell
import sys
from typing import Any

SCHEMA = "wls.r1.public_taskpack.v1"
CONTRACT = "wls.r1.frozen_contract.v1"
MAX_TASKS = 2_000
MIN_PUBLIC_TASKS = 100
MAX_TEST_INVOCATIONS = 3
SHA = re.compile(r"[0-9a-f]{64}\Z")
HEAD = re.compile(r"[0-9a-f]{40}\Z")
ID = re.compile(r"[A-Za-z0-9_.-]{1,96}\Z")
PINNED_SCORER_PATHS = (
    "source/src/wls/db.py",
    "source/src/wls/evidence.py",
    "source/src/wls/schemas.py",
    "source/src/wls/rsi_artifact_gate.py",
    "source/src/wls/rsi_evolution.py",
    "source/scripts/generate_ci_feedback.py",
    "source/src/wls/task_admission.py",
    "source/src/wls/experiment_decision.py",
    "source/scripts/github_rsi_replay_model.py",
    "source/scripts/run_external_risk_holdout.py",
    "source/src/wls/benchmark.py",
    "source/src/wls/evaluator.py",
    "source/scripts/verify_rsi_evaluator_change_boundary.py",
    "source/scripts/freeze_r1_public_taskpack.py",
    "pyproject.toml",
)


class StandardMoved(ValueError):
    """One of the identity-bearing scorer/task/environment inputs moved."""


def digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def parse_json(raw: bytes) -> Any:
    if not raw or len(raw) > 4_000_000:
        raise ValueError("missing or oversized R1 input")
    return json.loads(
        raw, object_pairs_hook=no_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError("invalid JSON number: " + value)
        ),
    )


def file_bytes(root: Path, name: str, allowed_prefix: str = "") -> bytes:
    if (
        not isinstance(name, str) or "\\" in name
        or name.startswith("/") or not name
    ):
        raise ValueError("unsafe R1 file path")
    p = PurePosixPath(name)
    if (
        any(part in {"..", "."} for part in p.parts)
        or p.as_posix() != name
        or (allowed_prefix and not name.startswith(allowed_prefix))
    ):
        raise ValueError("R1 file path outside approved scope")
    target = root / name
    real_root = root.resolve()
    resolved = target.resolve(strict=True)
    if not resolved.is_relative_to(real_root) or target.is_symlink() or not resolved.is_file():
        raise ValueError("untrusted R1 file path or symlink")
    data = resolved.read_bytes()
    if len(data) > 1_000_000:
        raise ValueError("oversized R1 source/test/scorer file")
    return data


def environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
        "platform": platform.system().lower(),
        "machine": platform.machine().lower(),
        "pytest": version("pytest"),
    }


def checked_source_head(root: Path, expected: str) -> None:
    """Bind task records to a real clean checkout, not a user-typed SHA."""
    if not isinstance(expected, str) or not HEAD.fullmatch(expected):
        raise ValueError("invalid immutable WLS source SHA")
    def git(*args: str) -> str:
        try:
            result = subprocess.run(  # nosec B603: fixed Git executable, no shell
                ["git", "-C", str(root), *args], capture_output=True,
                text=True, encoding="utf-8", check=False, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StandardMoved("Git source provenance not verifiable") from exc
        if result.returncode:
            raise StandardMoved("Git checkout provenance invalid")
        return result.stdout.strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise StandardMoved("source root is not exact Git checkout")
    if git("rev-parse", "HEAD") != expected:
        raise StandardMoved("task source_head differs from Git HEAD")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise StandardMoved("Git tracked checkout changed after source HEAD")


def validate_pack(root: Path, pack: Any, minimum: int = MIN_PUBLIC_TASKS) -> dict[str, Any]:
    if not isinstance(pack, dict) or set(pack) != {
        "schema", "source_head", "max_test_invocations", "baseline_seed", "tasks",
    } or pack["schema"] != SCHEMA:
        raise ValueError("unexpected public R1 schema or keys")
    checked_source_head(root, pack["source_head"])
    if type(pack["max_test_invocations"]) is not int or pack["max_test_invocations"] != 3:
        raise ValueError("R1 must fix exactly three permitted test invocations")
    if type(pack["baseline_seed"]) is not int or not 0 <= pack["baseline_seed"] <= 2**32 - 1:
        raise ValueError("R1 random baseline seed is missing or unsafe")
    cases = pack["tasks"]
    if not isinstance(cases, list) or not minimum <= len(cases) <= MAX_TASKS:
        raise ValueError("R1 public task count below acceptance threshold")
    seen: set[str] = set()
    seen_mutants: set[tuple[str, str]] = set()
    used_tests: set[str] = set()
    source_hashes: dict[str, str] = {}
    for case in cases:
        required = {
            "id", "source_path", "source_sha256", "mutant_sha256",
            "mutation_kind", "mutation_line", "test_paths",
            "candidate_patch_sha256s",
        }
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("public mutation task schema drift")
        task_id = case["id"]
        if not isinstance(task_id, str) or not ID.fullmatch(task_id) or task_id in seen:
            raise ValueError("duplicate/invalid public mutation task")
        seen.add(task_id)
        if case["mutation_kind"] not in {"compare", "boolean", "arithmetic", "literal"}:
            raise ValueError("unsupported mutation operation")
        if type(case["mutation_line"]) is not int or case["mutation_line"] < 1:
            raise ValueError("invalid mutation line")
        source_path = case["source_path"]
        raw_source = file_bytes(root, source_path, "source/src/wls/")
        if not source_path.endswith(".py"):
            raise ValueError("mutation must touch canonical Python source")
        raw_hash = digest(raw_source)
        if raw_hash != case["source_sha256"]:
            raise StandardMoved("mutation source content changed")
        source_hashes[source_path] = raw_hash
        if (
            not isinstance(case["mutant_sha256"], str)
            or not SHA.fullmatch(case["mutant_sha256"])
            or case["mutant_sha256"] == raw_hash
        ):
            raise ValueError("invalid or unmodified mutation digest")
        mutant_key = (source_path, case["mutant_sha256"])
        if mutant_key in seen_mutants:
            raise ValueError("duplicate public mutation content disguised as new task")
        seen_mutants.add(mutant_key)
        if case["mutation_line"] > len(raw_source.splitlines()):
            raise ValueError("mutation line does not exist")
        tests = case["test_paths"]
        if not isinstance(tests, list) or not tests or len(tests) > 12:
            raise ValueError("missing or oversized test selection")
        if not all(isinstance(p, str) for p in tests) or len(set(tests)) != len(tests):
            raise ValueError("duplicated test file")
        for test_path in tests:
            if not test_path.endswith(".py"):
                raise ValueError("only pytest source files are permitted")
            file_bytes(root, test_path, "source/tests/")
            used_tests.add(test_path)
        candidates = case["candidate_patch_sha256s"]
        if (
            not isinstance(candidates, list) or not 1 <= len(candidates) <= 100
            or any(not isinstance(x, str) or not SHA.fullmatch(x) for x in candidates)
            or len(set(candidates)) != len(candidates)
        ):
            raise ValueError("candidate ordering/digests missing or duplicated")
    return {
        "task_count": len(cases),
        "source_digests": source_hashes,
        "test_digests": {path: digest(file_bytes(root, path)) for path in sorted(used_tests)},
    }


def contract_for(root: Path, pack: dict[str, Any], minimum: int = MIN_PUBLIC_TASKS) -> dict[str, Any]:
    observed = validate_pack(root, pack, minimum=minimum)
    public_digest = digest(encode(pack))
    grader_digests = {
        path: digest(file_bytes(root, path)) for path in PINNED_SCORER_PATHS
    }
    output = {
        "schema": CONTRACT,
        "source_head": pack["source_head"],
        "public_tasks_sha256": public_digest,
        "task_count": observed["task_count"],
        "source_digests": observed["source_digests"],
        "test_digests": observed["test_digests"],
        "grader_digests": grader_digests,
        "environment": environment(),
        "model_budget": "NOT_ASSESSED",
        "max_test_invocations": MAX_TEST_INVOCATIONS,
        "random_seed": pack["baseline_seed"],
        "line_order_baseline": "ascending_source_line_then_candidate_order",
        "qualification": "UNMEASURED_three_repeat_detection_not_yet_verified",
        "heldout_gain": "UNMEASURED",
        "model_gain": "UNMEASURED",
    }
    output["contract_sha256"] = digest(encode(output))
    return output


def seal(root: Path, tasks: Path, out: Path) -> dict[str, Any]:
    if out.exists() or out.is_symlink():
        raise ValueError("refuse to rewrite a frozen R1 contract")
    pack = parse_json(tasks.read_bytes())
    manifest = contract_for(root, pack)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(encode(manifest))
    return manifest


def verify(root: Path, tasks: Path, sealed: Path) -> dict[str, Any]:
    pack = parse_json(tasks.read_bytes())
    original = parse_json(sealed.read_bytes())
    regenerated = contract_for(root, pack)
    if original != regenerated:
        raise StandardMoved("R1 task, tests, scorer, source or environment moved")
    return original


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("seal", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (
            seal(args.root, args.tasks, args.manifest)
            if args.operation == "seal"
            else verify(args.root, args.tasks, args.manifest)
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "STANDARD_MOVED" if isinstance(exc, StandardMoved)
                          else "BLOCKED", "error_type": type(exc).__name__},
                         sort_keys=True))
        return 2
    print(json.dumps({
        "status": "CONTRACT_SEALED" if args.operation == "seal" else "CONTRACT_VERIFIED",
        "task_count": result["task_count"],
        "contract_sha256": result["contract_sha256"],
        "learning_gain": "UNMEASURED",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
