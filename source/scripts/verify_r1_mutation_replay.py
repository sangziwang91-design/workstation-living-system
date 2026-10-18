"""R1 second-pass reproducibility on the SAME Windows Actions checkout.

The input is a public development receipt produced by make_mutation_tasks.py.
Never treat its claims as truth: reconstruct each AST edit from canonical source,
verify source/test identity and JUnit-backed outcomes, then run three NEW pytest
processes in an isolated WLS package. This is NOT R2 repair evaluation or RSI.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - fixed git executable, no shell
import tempfile
from typing import Any

import make_mutation_tasks as engine

SCHEMA = "wls.r1.observed_replay.v1"
MAX_RECEIPT_BYTES = 2_000_000
MAX_REPLAY_TASKS = 500


class EvidenceBlocked(ValueError):
    """Untrusted, moved, or inconsistent source/score identity."""


def digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def read_receipt(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_RECEIPT_BYTES:
        raise EvidenceBlocked("missing or oversized mutation receipt")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for name, val in pairs:
            if name in obj:
                raise EvidenceBlocked("duplicate JSON key")
            obj[name] = val
        return obj

    payload = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(payload, dict) or payload.get("schema") != engine.SCHEMA:
        raise EvidenceBlocked("unknown mutation receipt schema")
    rows = payload.get("tasks")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_REPLAY_TASKS:
        raise EvidenceBlocked("missing or excessive observed tasks")
    if type(payload.get("observed_task_count")) is not int or len(rows) != payload["observed_task_count"]:
        raise EvidenceBlocked("invalid observed task count")
    if digest(json.dumps(rows, sort_keys=True).encode("utf-8")) != payload.get("task_digest"):
        raise EvidenceBlocked("task digest mismatch")
    provenance = payload.get("runner_provenance")
    if not isinstance(provenance, dict):
        raise EvidenceBlocked("missing original runner metadata")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if (
            provenance.get("github_run_id") != os.environ.get("GITHUB_RUN_ID")
            or provenance.get("github_sha") != os.environ.get("GITHUB_SHA")
            or provenance.get("source_scope") == "local_or_unattested_runner"
            or os.environ.get("RUNNER_OS") != "Windows"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        ):
            raise EvidenceBlocked("receipt source is not this same hosted Windows Actions run")
    return payload


def checked_source(root: Path, payload: dict[str, Any]) -> None:
    provenance = payload["runner_provenance"]
    sha = provenance.get("github_sha")
    if sha == "UNVERIFIED":
        # Unit/local traces are never a trusted Actions attestation.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise EvidenceBlocked("untrusted local receipt presented as GitHub evidence")
        return
    if not isinstance(sha, str) or len(sha) != 40:
        raise EvidenceBlocked("invalid checkout provenance")
    try:
        head = subprocess.run(  # nosec B603
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceBlocked("Git source cannot be independently verified") from exc
    if head.returncode or head.stdout.strip() != sha:
        raise EvidenceBlocked("receipt Git commit differs from checked-out task source")


def replay(root: Path, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    if not 1 <= timeout <= engine.MAX_PER_TEST_SECONDS:
        raise EvidenceBlocked("unbounded replay timeout")
    source = root / "source/src/wls"
    if not (source / "__init__.py").is_file():
        raise EvidenceBlocked("canonical WLS checkout missing")
    checked_source(root, payload)
    rows = payload["tasks"]
    ids: set[str] = set()
    verified_rows: list[tuple[dict[str, Any], bytes]] = []
    # Validate ALL evidence before running a single test or modifying any temp file.
    for row in rows:
        if not isinstance(row, dict):
            raise EvidenceBlocked("non-object observed task")
        task = row.get("id")
        rel = row.get("source_path")
        if not isinstance(task, str) or not task or task in ids:
            raise EvidenceBlocked("duplicate or invalid task ID")
        ids.add(task)
        if not isinstance(rel, str) or not rel.startswith("source/src/wls/"):
            raise EvidenceBlocked("out-of-scope source")
        name = rel.removeprefix("source/src/wls/")
        if name not in engine.BASELINE_TESTS or rel != f"source/src/wls/{name}":
            raise EvidenceBlocked("task references an untrusted source module")
        if row.get("selected_test") != engine.BASELINE_TESTS[name]:
            raise EvidenceBlocked("task changed selected tests")
        source_file = source / name
        original = source_file.read_bytes()
        if digest(original) != row.get("source_sha256"):
            raise EvidenceBlocked("task source changed since original observation")
        matches = [
            option for option in engine.candidates(name, original)
            if option["id"] == task
            and option["mutant_sha256"] == row.get("mutant_sha256")
        ]
        if len(matches) != 1:
            raise EvidenceBlocked("mutant cannot be reconstructed from original AST")
        match = matches[0]
        if any(row.get(key) != match[key] for key in (
            "source_path", "mutation_kind", "mutation_line",
            "operator_from", "operator_to",
        )):
            raise EvidenceBlocked("mutation metadata was altered")
        if (
            row.get("three_actual_pytest_failures") is not True
            or type(row.get("test_invocations")) is not int
            or row["test_invocations"] != 3
            or row.get("test_results") != [
                {"exit_code": 1, "status": "PYTEST_ASSERTION_FAILED"}
            ] * 3
        ):
            raise EvidenceBlocked("original triple-failure proof missing or forged")
        verified_rows.append((row, match["mutated_content"]))
    checked = []
    with tempfile.TemporaryDirectory(prefix="r1-trusted-replay-") as folder:
        temp = Path(folder)
        isolated = temp / "package/wls"
        isolated.parent.mkdir(parents=True)
        shutil.copytree(
            source, isolated, symlinks=False,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        baselines: dict[str, tuple[int, str]] = {}
        for row, changed in verified_rows:
            name = row["source_path"].rsplit("/", 1)[-1]
            selected = engine.BASELINE_TESTS[name]
            original = (source / name).read_bytes()
            live = isolated / name
            if name not in baselines:
                baselines[name] = engine.pytest_once(
                    root, temp, isolated.parent, selected, timeout,
                )
            outcomes: list[dict[str, Any]] = []
            if baselines[name] == (0, "PASS"):
                try:
                    live.write_bytes(changed)
                    for _ in range(3):
                        code, reason = engine.pytest_once(
                            root, temp, isolated.parent, selected, timeout,
                        )
                        outcomes.append({"exit_code": code, "status": reason})
                        if code != 1:
                            break
                finally:
                    live.write_bytes(original)
            if live.read_bytes() != original:
                raise EvidenceBlocked("replay failed to restore its isolated source")
            stable = baselines[name] == (0, "PASS") and outcomes == [
                {"exit_code": 1, "status": "PYTEST_ASSERTION_FAILED"}
            ] * 3
            checked.append({
                "id": row["id"],
                "source_path": row["source_path"],
                "mutant_sha256": row["mutant_sha256"],
                "second_pass_test_invocations": len(outcomes),
                "second_pass_stable": stable,
                "status": "STABLE" if stable else "FLAKY_OR_INFRA_FAILURE",
            })
    stable_rows = [row["id"] for row in checked if row["second_pass_stable"]]
    return {
        "schema": SCHEMA,
        "qualification": "SAME_RUN_INDEPENDENT_THREE_TEST_REPLAY",
        "source_run_id": payload["runner_provenance"]["github_run_id"],
        "source_sha": payload["runner_provenance"]["github_sha"],
        "observed_task_count": len(rows),
        "stable_task_count": len(stable_rows),
        "unstable_task_count": len(rows) - len(stable_rows),
        "stable_task_ids": stable_rows,
        "cases": checked,
        "model_calls": 0,
        "repair_success_rate": "UNMEASURED",
        "heldout_gain": "UNMEASURED",
        "claim_ceiling": "same-run repeatability of real pytest-killed mutations, not a sealed corpus or RSI",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--minimum-stable", type=int, default=5)
    p.add_argument("--timeout", type=int, default=35)
    args = p.parse_args()
    try:
        if not 1 <= args.minimum_stable <= MAX_REPLAY_TASKS:
            raise EvidenceBlocked("invalid minimum stable count")
        payload = read_receipt(args.receipt.read_bytes())
        result = replay(args.repo.resolve(), payload, args.timeout)
        result["status"] = (
            "STABLE_REPLAY_PASS"
            if result["stable_task_count"] >= args.minimum_stable
            else "INSUFFICIENT_STABLE_MUTANTS"
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        result = {"schema": SCHEMA, "status": "BLOCKED", "reason": type(exc).__name__,
                  "model_calls": 0, "heldout_gain": "UNMEASURED"}
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "observed_task_count": result.get("observed_task_count", 0),
        "stable_task_count": result.get("stable_task_count", 0),
    }, sort_keys=True))
    return 0 if result["status"] == "STABLE_REPLAY_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
