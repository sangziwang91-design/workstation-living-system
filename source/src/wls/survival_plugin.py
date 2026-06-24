from __future__ import annotations

from types import MethodType
from typing import Any, cast
import json
import signal
import time

from .lease import ProcessLease
from .schemas import utc_now
from .survival import SurvivalSupervisor


def register_wls(runtime: Any) -> None:
    """Attach mandatory bounded survival supervision to canonical LivingSystem.

    The canonical runtime class remains ``runtime.py::LivingSystem``. This
    built-in extension uses the repository's existing extension hook only to
    avoid creating a second runtime authority or executor.
    """

    target = cast(Any, runtime)
    if hasattr(target, "survival"):
        return
    target.survival = SurvivalSupervisor(target.db, target.ledger, target.config)

    original_initialize = target._initialize_runtime
    original_status = target.status

    def initialize_with_recovery(self: Any) -> None:
        _recover_interrupted_survival_runs(self)
        self.survival.recover_interrupted_cycles()
        original_initialize()

    def status_with_survival(self: Any) -> dict[str, Any]:
        value = dict(original_status())
        value["survival"] = self.survival.status()
        return value

    target._initialize_runtime = MethodType(initialize_with_recovery, target)
    target.status = MethodType(status_with_survival, target)
    target.run_daemon = MethodType(_run_supervised_daemon, target)


def _recover_interrupted_survival_runs(runtime: Any) -> list[str]:
    rows = runtime.db.query_all(
        "SELECT run_id FROM survival_runs WHERE status='RUNNING' ORDER BY started_at"
    )
    if not rows:
        return []
    run_ids = [str(row["run_id"]) for row in rows]
    now = utc_now()
    with runtime.db.transaction() as connection:
        for run_id in run_ids:
            report = {
                "run_id": run_id,
                "status": "INTERRUPTED",
                "termination_reason": "process ended before supervised shutdown",
                "recovered_at": now,
            }
            connection.execute(
                """
                UPDATE survival_runs
                SET status='INTERRUPTED',finished_at=?,termination_reason=?,report_json=?
                WHERE run_id=? AND status='RUNNING'
                """,
                (
                    now,
                    report["termination_reason"],
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )
        runtime.ledger.append(
            "interrupted_survival_runs_recovered",
            {"run_ids": run_ids, "count": len(run_ids)},
            connection,
        )
    return run_ids


def _run_supervised_daemon(
    self: Any, max_cycles: int | None = None
) -> dict[str, Any]:
    if max_cycles is not None and max_cycles < 0:
        raise ValueError("max_cycles must be non-negative")
    self._stop = False
    previous_handlers: dict[Any, Any] = {}
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[signal_name] = signal.signal(
                signal_name, lambda *_: setattr(self, "_stop", True)
            )
        except (ValueError, OSError):
            pass

    run_id = self.survival.start_run(max_cycles)
    attempted_cycles = 0
    report: dict[str, Any] | None = None
    daemon_lease = ProcessLease(self.config.home_path / "state" / "daemon.lock")
    try:
        with daemon_lease:
            while not self._stop and (
                max_cycles is None or attempted_cycles < max_cycles
            ):
                preflight = self.survival.preflight(run_id, attempted_cycles + 1)
                if not preflight["allowed"]:
                    reason = "daemon resource budget exceeded"
                    self.pause(reason)
                    report = self.survival.finish_run(
                        run_id, "PAUSED_RESOURCE_BUDGET", reason
                    )
                    break

                started = time.monotonic()
                try:
                    result = self.run_cycle()
                    duration = time.monotonic() - started
                    attempted_cycles += 1
                    status = str(result.get("status", "UNKNOWN"))
                    if status == "KILLED":
                        report = self.survival.finish_run(
                            run_id,
                            "KILLED",
                            str(result.get("reason", "runtime kill switch active")),
                        )
                        break
                    if status == "PAUSED":
                        report = self.survival.finish_run(
                            run_id,
                            "PAUSED",
                            str(result.get("reason", "runtime paused")),
                        )
                        break
                    self.survival.record_success(
                        run_id, attempted_cycles, duration, status
                    )
                except Exception as exc:
                    attempted_cycles += 1
                    decision = self.survival.record_failure(
                        run_id, attempted_cycles, exc
                    )
                    if self.db.get_runtime("kill_switch", False):
                        report = self.survival.finish_run(
                            run_id,
                            "KILLED_INTEGRITY",
                            self.db.get_runtime("kill_reason", str(exc)),
                        )
                        raise
                    exhausted = (
                        int(decision["consecutive_failures"])
                        >= self.config.daemon_max_consecutive_failures
                    )
                    if exhausted:
                        reason = (
                            "daemon consecutive failure budget exhausted: "
                            f"{decision['consecutive_failures']}"
                        )
                        self.pause(reason)
                        report = self.survival.finish_run(
                            run_id, "PAUSED_FAILURE_BUDGET", reason
                        )
                        break
                    _interruptible_sleep(
                        self,
                        float(decision["backoff_seconds"]),
                    )
                    continue

                remaining = self.config.cycle_seconds - (
                    time.monotonic() - started
                )
                _interruptible_sleep(self, remaining)

            if report is None:
                if self._stop:
                    report = self.survival.finish_run(
                        run_id, "STOPPED", "termination signal received"
                    )
                else:
                    report = self.survival.finish_run(
                        run_id, "COMPLETED", "maximum cycle count reached"
                    )
    except Exception as exc:
        if report is None:
            report = self.survival.finish_run(
                run_id,
                "FAILED_EXCEPTION",
                f"{type(exc).__name__}: {exc}"[:2000],
            )
        raise
    finally:
        for name, handler in previous_handlers.items():
            try:
                signal.signal(name, handler)
            except (ValueError, OSError):
                pass
    return report


def _interruptible_sleep(runtime: Any, seconds: float) -> None:
    if seconds <= 0:
        return
    end = time.monotonic() + seconds
    while not runtime._stop and time.monotonic() < end:
        time.sleep(min(0.25, max(0.0, end - time.monotonic())))
