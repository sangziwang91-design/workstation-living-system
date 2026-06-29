from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .lease import ProcessLease
from .schemas import ActionSpec, GoalStatus, utc_now


ALLOWED_PROVENANCE = (
    "EXECUTED_CURRENT_ACTION",
    "REUSED_PRIOR_RESULT",
    "RECOVERED_DURABLE_ACTION",
    "NO_OBSERVABLE_OUTCOME",
    "LEGACY_UNATTRIBUTED",
)
TERMINAL_RUN_STATUSES = {
    "COMPLETED",
    "STOPPED",
    "CRASHED",
    "INTERRUPTED",
    "RESTART_CHECKPOINT",
}
TERMINAL_GOAL_STATUSES = {
    GoalStatus.COMPLETED,
    GoalStatus.SUCCEEDED,
    GoalStatus.FAILED,
    GoalStatus.CANCELLED,
    GoalStatus.ABANDONED,
    GoalStatus.ARCHIVED,
}


def _backfill_legacy(runtime: Any) -> int:
    with runtime.db.transaction() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS n FROM actions
            WHERE status IN ('SUCCEEDED','FAILED','REJECTED','CANCELLED')
              AND outcome_provenance IS NULL
            """
        ).fetchone()
        count = int(row["n"]) if row else 0
        if count == 0:
            return 0
        evidence_id = runtime.ledger.append(
            "legacy_action_provenance_migrated",
            {
                "count": count,
                "classification": "LEGACY_UNATTRIBUTED",
                "reason": (
                    "terminal actions predate persistent Task19 provenance and "
                    "cannot be upgraded to fresh intervention evidence"
                ),
            },
            connection,
        )
        connection.execute(
            """
            UPDATE actions
            SET outcome_provenance='LEGACY_UNATTRIBUTED',
                source_action_id=NULL,provenance_evidence_id=?
            WHERE status IN ('SUCCEEDED','FAILED','REJECTED','CANCELLED')
              AND outcome_provenance IS NULL
            """,
            (evidence_id,),
        )
        return count


def _counts(runtime: Any) -> dict[str, int]:
    queries = {
        "terminal_missing_provenance": """
            SELECT COUNT(*) AS n FROM actions
            WHERE status IN ('SUCCEEDED','FAILED','REJECTED','CANCELLED')
              AND outcome_provenance IS NULL
        """,
        "reused_without_source": """
            SELECT COUNT(*) AS n FROM actions
            WHERE outcome_provenance='REUSED_PRIOR_RESULT'
              AND source_action_id IS NULL
        """,
        "fresh_with_source": """
            SELECT COUNT(*) AS n FROM actions
            WHERE outcome_provenance IN (
                'EXECUTED_CURRENT_ACTION','RECOVERED_DURABLE_ACTION'
            ) AND source_action_id IS NOT NULL
        """,
        "provenance_without_evidence": """
            SELECT COUNT(*) AS n FROM actions
            WHERE outcome_provenance IS NOT NULL
              AND provenance_evidence_id IS NULL
        """,
        "reuse_source_missing": """
            SELECT COUNT(*) AS n FROM actions reused
            LEFT JOIN actions source
              ON source.action_id=reused.source_action_id
            WHERE reused.outcome_provenance='REUSED_PRIOR_RESULT'
              AND source.action_id IS NULL
        """,
        "invalid_provenance": """
            SELECT COUNT(*) AS n FROM actions
            WHERE outcome_provenance IS NOT NULL
              AND outcome_provenance NOT IN (
                'EXECUTED_CURRENT_ACTION',
                'REUSED_PRIOR_RESULT',
                'RECOVERED_DURABLE_ACTION',
                'NO_OBSERVABLE_OUTCOME',
                'LEGACY_UNATTRIBUTED'
              )
        """,
    }
    result: dict[str, int] = {}
    for name, sql in queries.items():
        row = runtime.db.query_one(sql)
        result[name] = int(row["n"]) if row else 0
    return result


def _run_row(runtime: Any, run_id: str) -> Any:
    row = runtime.db.query_one(
        "SELECT * FROM survival_runs WHERE run_id=?", (run_id,)
    )
    if row is None:
        raise KeyError(run_id)
    return row


def _require_running(runtime: Any, run_id: str) -> None:
    row = _run_row(runtime, run_id)
    if str(row["status"]) != "RUNNING":
        raise RuntimeError(
            f"survival run {run_id} is terminal: {row['status']}"
        )


def register_wls(runtime: Any) -> None:
    """Protect recovery ownership and make operational evidence fail closed."""

    if getattr(runtime, "_task19_action_integrity_installed", False):
        return
    runtime._task19_action_integrity_installed = True
    migrated = _backfill_legacy(runtime)

    original_initialize = runtime._initialize_runtime

    def initialize_with_lease(self: Any) -> None:
        startup_lease = ProcessLease(
            self.config.home_path / "state" / "runtime.lock"
        )
        with startup_lease:
            original_initialize()

    runtime._initialize_runtime = MethodType(initialize_with_lease, runtime)

    original_execute_action = runtime._execute_action

    def execute_action_with_provenance(
        self: Any,
        action: ActionSpec,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        result = original_execute_action(action, approval_id=approval_id)
        provenance = result.get("provenance")
        if provenance in ALLOWED_PROVENANCE:
            return result
        with self.db.transaction() as connection:
            evidence_id = self.ledger.append(
                "action_outcome_missing_provenance",
                {
                    "action_id": action.action_id,
                    "observed_status": result.get("status"),
                    "fallback": "NO_OBSERVABLE_OUTCOME",
                },
                connection,
            )
            connection.execute(
                """
                UPDATE actions
                SET outcome_provenance='NO_OBSERVABLE_OUTCOME',
                    source_action_id=NULL,provenance_evidence_id=?,
                    error=COALESCE(error,?),finished_at=COALESCE(finished_at,?)
                WHERE action_id=?
                """,
                (
                    evidence_id,
                    "execution returned without explicit outcome provenance",
                    utc_now(),
                    action.action_id,
                ),
            )
        result.update(
            {
                "provenance": "NO_OBSERVABLE_OUTCOME",
                "source_action_id": None,
                "provenance_evidence_id": evidence_id,
            }
        )
        return result

    runtime._execute_action = MethodType(execute_action_with_provenance, runtime)

    survival = runtime.survival
    original_preflight = survival.preflight
    original_success = survival.record_success
    original_failure = survival.record_failure
    original_heartbeat = survival.heartbeat
    original_finish = survival.finish_run

    def preflight(self: Any, run_id: str, cycle_index: int) -> dict[str, Any]:
        _require_running(runtime, run_id)
        return original_preflight(run_id, cycle_index)

    def record_success(
        self: Any,
        run_id: str,
        cycle_index: int,
        duration_seconds: float,
        result_status: str,
    ) -> dict[str, Any]:
        _require_running(runtime, run_id)
        return original_success(
            run_id, cycle_index, duration_seconds, result_status
        )

    def record_failure(
        self: Any,
        run_id: str,
        cycle_index: int,
        exc: Exception,
    ) -> dict[str, Any]:
        _require_running(runtime, run_id)
        return original_failure(run_id, cycle_index, exc)

    def heartbeat(
        self: Any,
        run_id: str,
        cycle_index: int,
        status: str,
        duration_seconds: float | None,
    ) -> str | None:
        _require_running(runtime, run_id)
        return original_heartbeat(
            run_id, cycle_index, status, duration_seconds
        )

    def finish_run(
        self: Any,
        run_id: str,
        status: str,
        reason: str,
    ) -> dict[str, Any]:
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"invalid survival terminal status: {status}")
        row = _run_row(runtime, run_id)
        if str(row["status"]) != "RUNNING":
            if row["report_json"]:
                return json.loads(str(row["report_json"]))
            return {
                "run_id": run_id,
                "status": str(row["status"]),
                "termination_reason": row["termination_reason"],
                "completed_cycles": int(row["completed_cycles"]),
                "failed_cycles": int(row["failed_cycles"]),
                "finished_at": row["finished_at"],
                "idempotent": True,
            }
        return original_finish(run_id, status, reason)

    survival.preflight = MethodType(preflight, survival)
    survival.record_success = MethodType(record_success, survival)
    survival.record_failure = MethodType(record_failure, survival)
    survival.heartbeat = MethodType(heartbeat, survival)
    survival.finish_run = MethodType(finish_run, survival)

    reviewer = runtime.goal_runtime.reviewer
    original_review = reviewer.review

    def stable_goal_review(
        self: Any,
        goal_id: str,
        *,
        cycle_id: str | None = None,
        reason: str = "periodic",
        source_ids: list[str] | None = None,
    ) -> dict[str, Any] | None:
        goal = runtime.goals.get(goal_id)
        if goal is not None and goal.status in TERMINAL_GOAL_STATUSES:
            return None
        return original_review(
            goal_id,
            cycle_id=cycle_id,
            reason=reason,
            source_ids=source_ids,
        )

    reviewer.review = MethodType(stable_goal_review, reviewer)

    original_verify = runtime.verify_integrity

    def verify_integrity(self: Any, full: bool = True) -> dict[str, Any]:
        result = original_verify(full=full)
        counts = _counts(self)
        provenance_ok = all(value == 0 for value in counts.values())
        result["action_provenance"] = {
            "ok": provenance_ok,
            "counts": counts,
            "allowed": list(ALLOWED_PROVENANCE),
        }
        result["ok"] = bool(result.get("ok")) and provenance_ok
        if not provenance_ok:
            self.kill(f"action provenance integrity failure: {counts}")
        return result

    runtime.verify_integrity = MethodType(verify_integrity, runtime)
    runtime.ledger.append(
        "task19_action_integrity_installed",
        {
            "legacy_rows_migrated": migrated,
            "allowed_provenance": list(ALLOWED_PROVENANCE),
            "startup_recovery_requires_runtime_lease": True,
            "missing_provenance_fails_closed": True,
            "survival_terminal_state_enforced": True,
            "goal_terminal_state_enforced": True,
        },
    )
