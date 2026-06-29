from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .schemas import ActionStatus, utc_now


REQUIRED_EVIDENCE = ("actor", "authorization_reference", "finding")
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def _evidence(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("reconciliation evidence must be an object")
    missing = [
        key for key in REQUIRED_EVIDENCE if not str(value.get(key, "")).strip()
    ]
    if missing:
        raise ValueError(f"reconciliation evidence missing: {missing}")
    normalized = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
    if len(json.dumps(normalized, ensure_ascii=False).encode("utf-8")) > 65536:
        raise ValueError("reconciliation evidence exceeds 64 KiB")
    return normalized


def register_wls(runtime: Any) -> None:
    """Require owner evidence before resolving ambiguous side effects."""

    if getattr(runtime, "_task19_reconciliation_installed", False):
        return
    runtime._task19_reconciliation_installed = True

    def resolve_unknown_action(
        self: Any,
        action_id: str,
        resolution: str,
        evidence: dict[str, Any],
    ) -> None:
        if resolution not in {*TERMINAL, "RETRY_SAFE"}:
            raise ValueError("invalid resolution")
        owner_evidence = _evidence(evidence)
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT plan_id,status FROM actions WHERE action_id=?",
                (action_id,),
            ).fetchone()
            if row is None or str(row["status"]) != ActionStatus.UNKNOWN_SIDE_EFFECT.value:
                raise ValueError(
                    "action is not awaiting unknown-side-effect resolution"
                )
            plan_id = str(row["plan_id"])
            evidence_id = self.ledger.append(
                "unknown_action_owner_resolution",
                {
                    "action_id": action_id,
                    "plan_id": plan_id,
                    "resolution": resolution,
                    "evidence": owner_evidence,
                    "fresh_execution": False,
                },
                connection,
            )
            encoded = json.dumps(
                owner_evidence, ensure_ascii=False, sort_keys=True
            )
            if resolution == "RETRY_SAFE":
                connection.execute(
                    """
                    UPDATE actions
                    SET status='WAITING_APPROVAL',approval_id=NULL,
                        started_at=NULL,finished_at=NULL,result_json=NULL,error=?,
                        outcome_provenance=NULL,source_action_id=NULL,
                        provenance_evidence_id=NULL
                    WHERE action_id=?
                    """,
                    (encoded, action_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE actions
                    SET status=?,finished_at=?,error=?,
                        outcome_provenance='NO_OBSERVABLE_OUTCOME',
                        source_action_id=NULL,provenance_evidence_id=?
                    WHERE action_id=?
                    """,
                    (resolution, utc_now(), encoded, evidence_id, action_id),
                )
        self._refresh_plan_status(plan_id)
        if resolution in TERMINAL:
            self._resume_durable_actions()

    runtime.resolve_unknown_action = MethodType(resolve_unknown_action, runtime)
    runtime.ledger.append(
        "task19_reconciliation_installed",
        {
            "owner_evidence_required": list(REQUIRED_EVIDENCE),
            "retry_requires_new_approval": True,
            "automatic_unknown_replay": False,
        },
    )
