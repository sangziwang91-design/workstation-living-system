from __future__ import annotations

from types import MethodType
from typing import Any

from .lease import ProcessLease


ALLOWED_PROVENANCE = (
    "EXECUTED_CURRENT_ACTION",
    "REUSED_PRIOR_RESULT",
    "RECOVERED_DURABLE_ACTION",
    "NO_OBSERVABLE_OUTCOME",
    "LEGACY_UNATTRIBUTED",
)


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


def register_wls(runtime: Any) -> None:
    """Own legacy migration, startup recovery lease, and provenance audit only."""

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
            "cross_authority_monkeypatches_removed": [
                "execution",
                "survival",
                "goal_review",
            ],
        },
    )
