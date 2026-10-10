"""Actual canonical WLS life cycles on an ephemeral GitHub-hosted Linux home.

Each CLI invocation is a new OS process; SQLite state must survive process
restart. No second agent, paid model calls, owner data, or private holdouts.
GitHub artifacts do not make the home durable across scheduled runs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

SCHEMA = "wls.hosted_life_cycle.v1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_CAPTURE = 131072


def safe_cli_env(home: Path) -> dict[str, str]:
    """Do not pass runner GitHub/model tokens to the WLS process."""
    result = {"PATH": os.environ.get("PATH", os.defpath),
              "HOME": str(home), "WLS_HOME": str(home), "PYTHONNOUSERSITE": "1"}
    for name in ("SYSTEMROOT", "TMP", "TEMP", "TMPDIR"):
        if name in os.environ:
            result[name] = os.environ[name]
    return result


def cli(home: Path, *args: str, timeout: float = 60.0) -> dict:
    if not 1 <= timeout <= 60:
        raise ValueError("unsupported life-cycle command timeout")
    argv = ["wls", "--config", str(home / "config.json"), *args]
    if args and args[0] == "init":
        argv = ["wls", "init", "--home", str(home)]
    try:
        completed = subprocess.run(  # nosec B603: canonical local WLS CLI
            argv, check=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=safe_cli_env(home), timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("canonical WLS process could not complete") from exc
    if (completed.returncode != 0
            or len(completed.stdout) > MAX_CAPTURE
            or len(completed.stderr) > MAX_CAPTURE):
        raise ValueError("canonical WLS process failed or exceeded output budget")
    try:
        response = json.loads(completed.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("WLS CLI returned invalid JSON evidence") from exc
    if not isinstance(response, dict):
        raise ValueError("WLS command returned non-object evidence")
    return response


def validate_cycle(cycle: dict, status: dict, previous: int) -> str:
    if cycle.get("status") != "SUCCEEDED":
        raise ValueError("WLS canonical life cycle failed")
    cycle_id = cycle.get("cycle_id")
    if not isinstance(cycle_id, str) or not cycle_id.startswith("cycle_"):
        raise ValueError("missing real cycle ID")
    latest = status.get("latest_cycle")
    if not isinstance(latest, dict) or latest.get("cycle_id") != cycle_id:
        raise ValueError("restart did not recover this cycle")
    if latest.get("status") != "SUCCEEDED":
        raise ValueError("persisted cycle status is not successful")
    if (status.get("cycle_count") != previous + 1
            or status.get("paused") is not False
            or status.get("killed") is not False
            or status.get("read_only") is not True):
        raise ValueError("persisted read-only state did not advance")
    return cycle_id


def execute(home: Path, *, head: str) -> dict:
    if not SHA.fullmatch(head):
        raise ValueError("must provide immutable source commit SHA")
    if home.exists() and any(home.iterdir()):
        raise ValueError("refuse to operate on a pre-existing home")
    home.mkdir(parents=True, exist_ok=True)
    before = cli(home, "init")["status"]
    if before["cycle_count"] != 0 or before["read_only"] is not True:
        raise ValueError("unexpected initial WLS home")
    ids: list[str] = []
    timings: list[float] = []
    for index in range(3):
        start = time.monotonic()
        cycle = cli(home, "once")
        state = cli(home, "status")  # new process and independent DB load
        timings.append(round(time.monotonic() - start, 3))
        cycle_id = validate_cycle(cycle, state, index)
        if cycle_id in ids:
            raise ValueError("duplicate persisted cycle identity")
        ids.append(cycle_id)
    verify = cli(home, "verify")
    if verify.get("ok") is not True:
        raise ValueError("ledger/DB/memory integrity failure")
    life_state = cli(home, "life-state")
    system = life_state.get("system", {})
    if not isinstance(system, dict) or system.get("cycle_count") != 3:
        raise ValueError("life-state disagrees with persisted cycle count")
    if system.get("read_only") is not True:
        raise ValueError("read-only policy drift in life-state")
    cli(home, "sleep")
    after_sleep = cli(home, "status")
    if after_sleep.get("cycle_count") != 3:
        raise ValueError("offline consolidation altered cycle history")
    if cli(home, "verify").get("ok") is not True:
        raise ValueError("offline consolidation damaged evidence")
    return {
        "schema": SCHEMA,
        "status": "HOSTED_EPHEMERAL_LIFE_CYCLES_PASS",
        "head_sha": head,
        "source_type": "github_hosted_real_canonical_wls_cli",
        "distinct_cycle_ids": len(ids),
        "cycle_count": 3,
        "read_only": True,
        "sqlite_reused_across_processes": True,
        "ledger_integrity_verified": True,
        "sleep_consolidation_executed": True,
        "cycle_seconds": timings,
        "home_persisted_across_runs": False,
        "model_calls": 0,
        "autonomous_skill_gain_proven": False,
        "claim_ceiling": "three_real_ephemeral_process_cycles_not_cross_run_persistence_or_RSI",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, default=Path("wls-hosted-life-receipt.json"))
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="wls-hosted-life-") as base:
            report = execute(Path(base) / "home", head=args.head)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        report = {"schema": SCHEMA, "status": "UNMEASURED",
                  "error_type": type(exc).__name__,
                  "claim_ceiling": "no_life_cycle_claim_without_complete_evidence"}
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": report["status"],
                      "cycle_count": report.get("cycle_count", 0)}))
    return 0 if report["status"] == "HOSTED_EPHEMERAL_LIFE_CYCLES_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
