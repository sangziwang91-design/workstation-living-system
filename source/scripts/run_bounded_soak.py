from __future__ import annotations

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import argparse
import json
import statistics
import sys
import time
import tracemalloc

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import Event


RESTART_AFTER = {25, 50, 75}
MEMORY_SAMPLE_EVERY = 10
MEMORY_GROWTH_ALLOWANCE_BYTES = 32 * 1024 * 1024


def _survival_integrity(runtime: LivingSystem) -> dict[str, Any]:
    checks = {
        "orphan_heartbeats": """
            SELECT COUNT(*) AS n FROM survival_heartbeats h
            LEFT JOIN survival_runs r ON r.run_id=h.run_id
            WHERE r.run_id IS NULL
        """,
        "orphan_incidents": """
            SELECT COUNT(*) AS n FROM runtime_incidents i
            LEFT JOIN survival_runs r ON r.run_id=i.run_id
            WHERE i.run_id IS NOT NULL AND r.run_id IS NULL
        """,
        "running_cycles": "SELECT COUNT(*) AS n FROM cycles WHERE status='RUNNING'",
        "running_actions": "SELECT COUNT(*) AS n FROM actions WHERE status='RUNNING'",
        "running_survival_runs": "SELECT COUNT(*) AS n FROM survival_runs WHERE status='RUNNING'",
    }
    counts: dict[str, int] = {}
    for name, sql in checks.items():
        row = runtime.db.query_one(sql)
        counts[name] = int(row["n"] if row else 0)
    return {"ok": all(value == 0 for value in counts.values()), "counts": counts}


def _event(index: int) -> Event:
    return Event(
        event_type="task19_soak",
        source="task19-bounded-soak",
        payload={
            "observation": {
                "source": "task19-bounded-soak",
                "kind": "state",
                "subject": "bounded-soak",
                "predicate": "cycle_requested",
                "value": {"index": index, "action": "record_only"},
                "confidence": 1.0,
            }
        },
        salience_hint=0.7,
        dedupe_key=f"task19-soak-{index}",
    )


def _close_segment(
    runtime: LivingSystem,
    run_id: str,
    *,
    status: str,
    reason: str,
) -> dict[str, Any]:
    report = runtime.survival.finish_run(run_id, status, reason)
    if report["status"] != status:
        raise AssertionError(f"survival segment did not close as {status}")
    return report


def run_soak(cycles: int = 100, home_path: Path | None = None) -> dict[str, Any]:
    if cycles != 100:
        raise ValueError("the authoritative Task19 bounded soak requires exactly 100 cycles")
    temporary = TemporaryDirectory(prefix="wls-task19-soak-") if home_path is None else None
    if temporary is not None:
        home = Path(temporary.name).resolve()
    else:
        if home_path is None:
            raise ValueError("home_path is required when no temporary directory is used")
        home = Path(home_path).resolve()
    home.mkdir(parents=True, exist_ok=True)
    config = default_config(home)
    config.sensors = []
    config.read_only = True
    config.max_actions_per_cycle = 1
    config.sleep_after_idle_cycles = cycles + 10
    config.full_integrity_check_every = 10
    config.cycle_seconds = 0.01
    config.daemon_heartbeat_every_cycles = 1
    config.daemon_heartbeat_retention = 1000

    result: dict[str, Any] = {
        "requested_cycles": cycles,
        "completed_cycles": 0,
        "failed_cycles": 0,
        "exceptions": [],
        "restart_after": sorted(RESTART_AFTER),
        "restart_count": 0,
        "run_segments": [],
        "integrity_checkpoints": [],
        "memory_samples": [],
        "action_count": 0,
        "provenance_counts": {},
        "home_is_temporary": temporary is not None,
    }
    provenance: Counter[str] = Counter()
    tracemalloc.start()
    started = time.monotonic()
    runtime = LivingSystem(config)
    run_id = runtime.survival.start_run(25)
    segment_start = 1
    try:
        for index in range(1, cycles + 1):
            preflight = runtime.survival.preflight(run_id, index - segment_start)
            if not preflight["allowed"]:
                raise AssertionError(f"survival preflight blocked cycle {index}: {preflight}")
            runtime.ingest_event(_event(index))
            cycle_started = time.monotonic()
            try:
                cycle = runtime.run_cycle()
                duration = time.monotonic() - cycle_started
                if cycle.get("status") != "SUCCEEDED":
                    raise AssertionError(f"unexpected cycle status: {cycle.get('status')}")
                runtime.survival.record_success(
                    run_id,
                    index - segment_start,
                    duration,
                    str(cycle["status"]),
                )
                result["completed_cycles"] += 1
                result["action_count"] += int(cycle.get("actions", 0))
                for outcome in cycle.get("outcomes", []):
                    provenance[str(outcome.get("provenance", "MISSING"))] += 1
            except Exception as exc:
                result["failed_cycles"] += 1
                result["exceptions"].append(
                    {
                        "cycle": index,
                        "type": type(exc).__name__,
                        "error": str(exc)[:1000],
                    }
                )
                runtime.survival.record_failure(run_id, index - segment_start, exc)
                raise

            if index % MEMORY_SAMPLE_EVERY == 0:
                current, peak = tracemalloc.get_traced_memory()
                result["memory_samples"].append(
                    {"cycle": index, "current_bytes": current, "peak_bytes": peak}
                )

            if index % 10 == 0:
                canonical = runtime.verify_integrity(full=True)
                cycle_count = runtime.db.query_one(
                    "SELECT COUNT(*) AS n FROM cycles WHERE status='RUNNING'"
                )
                action_count = runtime.db.query_one(
                    "SELECT COUNT(*) AS n FROM actions WHERE status='RUNNING'"
                )
                if cycle_count is None or action_count is None:
                    raise AssertionError("running-row count query returned no row")
                checkpoint: dict[str, Any] = {
                    "cycle": index,
                    "canonical": canonical,
                    "open_running_rows": {
                        "cycles": int(cycle_count["n"]),
                        "actions": int(action_count["n"]),
                    },
                }
                result["integrity_checkpoints"].append(checkpoint)
                if not canonical.get("ok") or any(
                    checkpoint["open_running_rows"].values()
                ):
                    raise AssertionError(f"integrity failed at cycle {index}")

            if index in RESTART_AFTER:
                result["run_segments"].append(
                    _close_segment(
                        runtime,
                        run_id,
                        status="RESTART_CHECKPOINT",
                        reason=f"controlled runtime re-instantiation after cycle {index}",
                    )
                )
                runtime.close()
                runtime = LivingSystem(config)
                result["restart_count"] += 1
                post_restart = runtime.verify_integrity(full=True)
                post_survival = _survival_integrity(runtime)
                if not post_restart.get("ok") or not post_survival.get("ok"):
                    raise AssertionError(f"restart integrity failed after cycle {index}")
                segment_start = index + 1
                run_id = runtime.survival.start_run(25)

        result["run_segments"].append(
            _close_segment(
                runtime,
                run_id,
                status="COMPLETED",
                reason="100-cycle bounded soak completed",
            )
        )
        final_integrity = runtime.verify_integrity(full=True)
        final_survival = _survival_integrity(runtime)
        result["final_integrity"] = final_integrity
        result["final_survival_integrity"] = final_survival
        result["database_size_bytes"] = config.db_path.stat().st_size
        ledger_ok, ledger_details = runtime.ledger.verify()
        result["evidence_chain"] = {"ok": ledger_ok, **ledger_details}
        result["provenance_counts"] = dict(sorted(provenance.items()))
        first = result["memory_samples"][0]["current_bytes"]
        last = result["memory_samples"][-1]["current_bytes"]
        growth = max(0, last - first)
        first_half = [item["current_bytes"] for item in result["memory_samples"][:5]]
        second_half = [item["current_bytes"] for item in result["memory_samples"][5:]]
        result["memory_growth_assessment"] = {
            "method": "tracemalloc current bytes sampled every 10 cycles",
            "first_current_bytes": first,
            "last_current_bytes": last,
            "absolute_growth_bytes": growth,
            "first_half_median": statistics.median(first_half),
            "second_half_median": statistics.median(second_half),
            "allowance_bytes": MEMORY_GROWTH_ALLOWANCE_BYTES,
            "within_bounded_window": growth <= MEMORY_GROWTH_ALLOWANCE_BYTES,
            "claim_ceiling": (
                "bounded observation only; not leak-proof or longitudinal stability proof"
            ),
        }
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
        current, peak = tracemalloc.get_traced_memory()
        result["final_tracemalloc_current_bytes"] = current
        result["tracemalloc_peak_bytes"] = peak
        tracemalloc.stop()
        runtime.close()
        if temporary is not None:
            temporary.cleanup()

    segment_cycles = sum(int(item["completed_cycles"]) for item in result["run_segments"])
    accepted_provenance = {
        "EXECUTED_CURRENT_ACTION",
        "REUSED_PRIOR_RESULT",
        "RECOVERED_DURABLE_ACTION",
    }
    accepted_provenance_count = sum(
        int(result["provenance_counts"].get(name, 0)) for name in accepted_provenance
    )
    result["passed"] = bool(
        result["completed_cycles"] == cycles
        and segment_cycles == cycles
        and result["failed_cycles"] == 0
        and result["restart_count"] == len(RESTART_AFTER)
        and len(result["run_segments"]) == 4
        and result["action_count"] == cycles
        and accepted_provenance_count == cycles
        and result["provenance_counts"].get("EXECUTED_CURRENT_ACTION", 0) >= 1
        and result["final_integrity"].get("ok")
        and result["final_survival_integrity"].get("ok")
        and result["evidence_chain"].get("ok")
        and result["memory_growth_assessment"]["within_bounded_window"]
    )
    result["claim_ceiling"] = (
        "100-cycle bounded local workload, four explicit SurvivalSupervisor segments, "
        "and three controlled runtime re-instantiations"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cycles", type=int, nargs="?", default=100)
    parser.add_argument("--home", type=Path)
    args = parser.parse_args()
    try:
        result = run_soak(args.cycles, args.home)
    except Exception as exc:
        print(
            json.dumps(
                {"passed": False, "error_type": type(exc).__name__, "error": str(exc)},
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
