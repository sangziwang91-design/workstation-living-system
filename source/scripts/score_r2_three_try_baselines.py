"""R2 public-development baseline: actual repair attempts with a hard 3-test ceiling.

Input must be a same-hosted-run R1 receipt + independent three-test replay.
The worker never receives an unmutated reference/answer. The *scorer* retains
the original digest ONLY to report exact restoration separately from pytest.
This does not constitute independent holdout, model learning, or RSI.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random
import shutil
import tempfile
from typing import Any

import make_mutation_tasks as engine
import verify_r1_mutation_replay as replay

SCHEMA = "wls.r2.public_three_try_baselines.v1"
MAX_TESTS_PER_CASE = 3
MAX_PUBLIC_SAMPLE = 20
SEED = 20261019


class BaselineBlocked(ValueError):
    """Unauthenticated/moved source, replay, candidate or scorer identity."""


def encoded(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def read_replay(raw: bytes, receipt: dict[str, Any]) -> list[str]:
    if not raw or len(raw) > 2_000_000:
        raise BaselineBlocked("missing/oversized independent replay")
    payload = replay.read_receipt(encoded(receipt))
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise BaselineBlocked("duplicate JSON key")
            out[key] = value
        return out
    result = json.loads(raw, object_pairs_hook=unique)
    if (
        not isinstance(result, dict)
        or result.get("schema") != replay.SCHEMA
        or result.get("status") != "STABLE_REPLAY_PASS"
        or result.get("source_run_id") != payload["runner_provenance"]["github_run_id"]
        or result.get("source_sha") != payload["runner_provenance"]["github_sha"]
        or result.get("observed_task_count") != len(payload["tasks"])
    ):
        raise BaselineBlocked("replay metadata/runner differs from R1 receipt")
    cases = result.get("cases")
    if not isinstance(cases, list) or len(cases) != len(payload["tasks"]):
        raise BaselineBlocked("missing or duplicated replay cases")
    rows = {row["id"]: row for row in payload["tasks"]}
    seen: set[str] = set()
    stable: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            raise BaselineBlocked("malformed replay task")
        name = case.get("id")
        if not isinstance(name, str) or name not in rows or name in seen:
            raise BaselineBlocked("replay case identity changed")
        seen.add(name)
        source = rows[name]
        if (
            case.get("source_path") != source["source_path"]
            or case.get("mutant_sha256") != source["mutant_sha256"]
        ):
            raise BaselineBlocked("replay mutant source changed")
        valid = (case.get("status") == "STABLE"
                 and case.get("second_pass_stable") is True
                 and case.get("second_pass_test_invocations") == 3)
        invalid = (case.get("status") == "FLAKY_OR_INFRA_FAILURE"
                   and case.get("second_pass_stable") is False
                   and type(case.get("second_pass_test_invocations")) is int
                   and 0 <= case["second_pass_test_invocations"] <= 3)
        if not (valid or invalid):
            raise BaselineBlocked("replay verdict or real test count forged")
        if valid:
            stable.append(name)
    if (
        len(stable) != result.get("stable_task_count")
        or len(cases) - len(stable) != result.get("unstable_task_count")
        or result.get("stable_task_ids") != stable
        or not stable
    ):
        raise BaselineBlocked("replay summary contradicts per-case evidence")
    return stable


def candidate_pool(module: str, broken: bytes) -> list[dict[str, Any]]:
    """Build alternatives from BROKEN source only, never read answer for ordering."""
    generated = engine.candidates(module, broken)
    unique: dict[str, dict[str, Any]] = {}
    for item in generated:
        if item["mutant_sha256"] == engine.sha(broken):
            continue
        unique[item["mutant_sha256"]] = item
    return sorted(unique.values(), key=lambda item: (
        item["mutation_line"], item["mutant_sha256"],
    ))


def rank(options: list[dict[str, Any]], strategy: str,
         task_id: str, seed: int = SEED) -> list[dict[str, Any]]:
    if strategy not in {"line_order", "seeded_random"}:
        raise BaselineBlocked("unapproved baseline strategy")
    result = list(options)
    if strategy == "seeded_random":
        salt = int.from_bytes(
            sha256(f"{seed}:{task_id}".encode()).digest()[:8], "big"
        )
        random.Random(salt).shuffle(result)
    return result


def try_repair(
    root: Path, temp: Path, isolated: Path, selected_test: str,
    broken: bytes, expected_digest: str, task_id: str, module: str,
    strategy: str, timeout: int,
) -> dict[str, Any]:
    if not 1 <= timeout <= engine.MAX_PER_TEST_SECONDS:
        raise BaselineBlocked("unbounded test runtime")
    options = rank(candidate_pool(module, broken), strategy, task_id)
    live = isolated / module
    if not live.is_file() or not live.resolve().is_relative_to(isolated.resolve()):
        raise BaselineBlocked("candidate path escaped isolated package")
    trials: list[dict[str, Any]] = []
    winner_digest: str | None = None
    for option in options:
        if len(trials) >= MAX_TESTS_PER_CASE:
            break
        candidate: bytes = option["mutated_content"]
        if engine.sha(candidate) != option["mutant_sha256"]:
            raise BaselineBlocked("candidate digest mismatch")
        try:
            live.write_bytes(candidate)
            code, reason = engine.pytest_once(
                root, temp, isolated.parent, selected_test, timeout,
            )
        finally:
            live.write_bytes(broken)
        trials.append({
            "candidate_digest": option["mutant_sha256"],
            "candidate_line": option["mutation_line"],
            "exit_code": code,
            "status": reason,
        })
        if code == 0 and reason == "PASS":
            winner_digest = option["mutant_sha256"]
            break
    if live.read_bytes() != broken:
        raise BaselineBlocked("candidate mutated persistent isolated state")
    return {
        "strategy": strategy, "candidate_count": len(options),
        "tests_used": len(trials),
        "max_tests": MAX_TESTS_PER_CASE,
        "selected_test_pass": winner_digest is not None,
        "exact_original_restored": winner_digest == expected_digest,
        "trials": trials,
    }


def score(
    root: Path, receipt: dict[str, Any], replay_raw: bytes,
    sample_size: int, timeout: int,
) -> dict[str, Any]:
    if not 1 <= sample_size <= MAX_PUBLIC_SAMPLE:
        raise BaselineBlocked("public baseline sample out of budget")
    stable = read_replay(replay_raw, receipt)
    replay.checked_source(root, receipt)
    by_id = {row["id"]: row for row in receipt["tasks"]}
    # Sampling is independent of per-task repair line number and outcomes.
    selected = sorted(stable, key=lambda v: sha256(
        f"{SEED}:{v}".encode()).hexdigest())[:sample_size]
    if len(selected) < sample_size:
        raise BaselineBlocked("insufficient independently stable task sample")
    if len(selected) != len(set(selected)):
        raise BaselineBlocked("repeated task in scored sample")
    original_dir = root / "source/src/wls"
    cases = []
    with tempfile.TemporaryDirectory(prefix="wls-r2-public-baseline-") as folder:
        temp = Path(folder)
        isolated = temp / "package/wls"
        isolated.parent.mkdir(parents=True)
        shutil.copytree(
            original_dir, isolated, symlinks=False,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        for task_id in selected:
            row = by_id[task_id]
            source_path = row.get("source_path")
            if not isinstance(source_path, str):
                raise BaselineBlocked("task source must be a canonical allowed Python module")
            module = Path(source_path).name
            if (
                source_path != f"source/src/wls/{module}"
                or module not in engine.BASELINE_TESTS
                or row.get("selected_test") != engine.BASELINE_TESTS[module]
            ):
                raise BaselineBlocked("untrusted mutation module or selected pytest path")
            original = (original_dir / module).read_bytes()
            if engine.sha(original) != row["source_sha256"]:
                raise BaselineBlocked("original source changed")
            matching = [
                m for m in engine.candidates(module, original)
                if m["id"] == task_id and m["mutant_sha256"] == row["mutant_sha256"]
            ]
            if len(matching) != 1:
                raise BaselineBlocked("mutant does not match independently observed AST")
            broken: bytes = matching[0]["mutated_content"]
            live = isolated / module
            if live.read_bytes() != original:
                raise BaselineBlocked("previous scored trial polluted shared package")
            live.write_bytes(broken)
            strategies = {}
            try:
                for name in ("line_order", "seeded_random"):
                    strategies[name] = try_repair(
                        root, temp, isolated, row["selected_test"],
                        broken, engine.sha(original), task_id, module,
                        name, timeout,
                    )
            finally:
                live.write_bytes(original)
            cases.append({
                "task_id": task_id, "mutant_sha256": row["mutant_sha256"],
                "strategies": strategies,
                "public_task_not_private_holdout": True,
            })
    results = {}
    for name in ("line_order", "seeded_random"):
        results[name] = {
            "scored_tasks": len(cases),
            "selected_test_passes": sum(
                int(c["strategies"][name]["selected_test_pass"]) for c in cases
            ),
            "exact_restorations": sum(
                int(c["strategies"][name]["exact_original_restored"])
                for c in cases
            ),
            "total_test_invocations": sum(
                c["strategies"][name]["tests_used"] for c in cases
            ),
        }
    return {
        "schema": SCHEMA, "status": "PUBLIC_BASELINE_SCORED",
        "source_sha": receipt["runner_provenance"]["github_sha"],
        "source_run_id": receipt["runner_provenance"]["github_run_id"],
        "observed_task_count": len(receipt["tasks"]),
        "stable_task_count": len(stable),
        "sample_size": len(cases), "fixed_seed": SEED,
        "per_strategy_per_task_max_pytest_calls": MAX_TESTS_PER_CASE,
        "results": results, "cases": cases,
        "model_calls": 0, "learned_worker_calls": 0,
        "model_gain": "UNMEASURED",
        "heldout_gain": "UNMEASURED",
        "qualification": "PUBLIC_DEV_ONLY_NOT_SEALED_NOT_INDEPENDENT_HOLDOUT",
        "claim_ceiling": (
            "actual up-to-three-repair-attempt public baselines only; "
            "golden code exists in public repo; NOT model improvement, NOT RSI"
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--replay", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--sample", type=int, default=8)
    p.add_argument("--timeout", type=int, default=35)
    args = p.parse_args()
    try:
        original = replay.read_receipt(args.receipt.read_bytes())
        result = score(args.repo.resolve(), original, args.replay.read_bytes(),
                       args.sample, args.timeout)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        result = {
            "schema": SCHEMA, "status": "BLOCKED",
            "reason": type(exc).__name__, "model_gain": "UNMEASURED",
        }
    args.output.write_bytes(encoded(result))
    print(json.dumps({
        "status": result["status"], "sample_size": result.get("sample_size", 0),
        "results": result.get("results", {}),
    }, sort_keys=True))
    return 0 if result["status"] == "PUBLIC_BASELINE_SCORED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
