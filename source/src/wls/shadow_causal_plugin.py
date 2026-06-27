from __future__ import annotations

from typing import Any, Callable
import json

from .shadow_causal import ShadowCausalAnalyzer


def register_wls(runtime: Any) -> None:
    """Attach an opt-in read-only causal shadow to one canonical WLS runtime."""
    analyzer = ShadowCausalAnalyzer(runtime.db, runtime.ledger)
    runtime.shadow_causal = analyzer

    try:
        _recover_evidence_bound_pending(analyzer)
    except Exception as exc:  # canonical initialization must continue
        analyzer.safe_record_error(phase="RECOVER_PENDING", error=exc)

    original_persist: Callable[..., Any] = runtime._persist_plan_and_ack_events
    original_execute: Callable[..., Any] = runtime._execute_plan
    original_resume = getattr(runtime, "_resume_durable_actions", None)

    def persist_with_shadow(
        cycle_id: str,
        plan: Any,
        event_ids: list[str],
    ) -> Any:
        plan_id = str(plan.plan_id)
        try:
            contradictions = runtime.world.unresolved_contradictions(limit=10)
            analyzer.freeze(
                cycle_id=cycle_id,
                plan=plan,
                event_ids=list(event_ids),
                contradictions=contradictions,
            )
        except Exception as exc:
            analyzer.safe_record_error(
                phase="FREEZE",
                error=exc,
                plan_id=plan_id,
            )
        try:
            return original_persist(cycle_id, plan, event_ids)
        except Exception as exc:
            try:
                analyzer.mark_aborted(
                    plan_id=plan_id,
                    reason=(
                        "canonical plan persistence failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
            except Exception:
                pass
            raise

    def execute_with_shadow(plan: Any) -> list[dict[str, Any]]:
        outcomes = original_execute(plan)
        _resolve_without_affecting_canonical(
            analyzer,
            plan_id=str(plan.plan_id),
            outcomes=outcomes,
            phase="RESOLVE",
        )
        return outcomes

    def resume_with_shadow() -> list[dict[str, Any]]:
        outcomes = original_resume()
        _resolve_recovered_outcomes(analyzer, outcomes)
        return outcomes

    runtime._persist_plan_and_ack_events = persist_with_shadow
    runtime._execute_plan = execute_with_shadow
    if callable(original_resume):
        runtime._resume_durable_actions = resume_with_shadow


def _recover_evidence_bound_pending(analyzer: ShadowCausalAnalyzer) -> None:
    rows = analyzer.db.query_all(
        "SELECT plan_id FROM shadow_causal_runs WHERE status='FROZEN' ORDER BY frozen_at"
    )
    terminal = {
        "SUCCEEDED",
        "FAILED",
        "REJECTED",
        "CANCELLED",
        "UNKNOWN_SIDE_EFFECT",
    }
    for row in rows:
        actions = analyzer.db.query_all(
            "SELECT action_id,status FROM actions WHERE plan_id=?",
            (row["plan_id"],),
        )
        if not actions or any(str(item["status"]) not in terminal for item in actions):
            continue
        outcomes = [
            {
                "action_id": str(item["action_id"]),
                "success": str(item["status"]) == "SUCCEEDED",
                "status": str(item["status"]),
                "recovered_from_db": True,
            }
            for item in actions
        ]
        _resolve_without_affecting_canonical(
            analyzer,
            plan_id=str(row["plan_id"]),
            outcomes=outcomes,
            phase="RESOLVE_RESTART",
        )


def _resolve_recovered_outcomes(
    analyzer: ShadowCausalAnalyzer,
    outcomes: list[dict[str, Any]],
) -> None:
    action_ids = [
        str(item.get("action_id", ""))
        for item in outcomes
        if str(item.get("action_id", ""))
    ]
    if not action_ids:
        return
    placeholders = ",".join("?" for _ in action_ids)
    rows = analyzer.db.query_all(
        f"SELECT action_id,plan_id FROM actions WHERE action_id IN ({placeholders})",
        tuple(action_ids),
    )
    plan_by_action = {
        str(row["action_id"]): str(row["plan_id"])
        for row in rows
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in outcomes:
        plan_id = plan_by_action.get(str(item.get("action_id", "")))
        if plan_id:
            grouped.setdefault(plan_id, []).append(item)
    for plan_id, plan_outcomes in grouped.items():
        _resolve_without_affecting_canonical(
            analyzer,
            plan_id=plan_id,
            outcomes=plan_outcomes,
            phase="RESOLVE_RECOVERED",
        )


def _resolve_without_affecting_canonical(
    analyzer: ShadowCausalAnalyzer,
    *,
    plan_id: str,
    outcomes: list[dict[str, Any]],
    phase: str,
) -> None:
    try:
        run = analyzer.db.query_one(
            "SELECT proposal_json,frozen_evidence_seq,status FROM shadow_causal_runs WHERE plan_id=?",
            (plan_id,),
        )
        if run is None or str(run["status"]) != "FROZEN":
            return
        proposal = json.loads(run["proposal_json"])
        action_ids = {
            str(item.get("action_id", ""))
            for item in proposal.get("predictions", [])
            if str(item.get("action_id", ""))
        }
        if action_ids and analyzer._first_action_outcome_seq(
            action_ids, int(run["frozen_evidence_seq"])
        ) is None:
            return
        analyzer.resolve(plan_id=plan_id, outcomes=outcomes)
    except Exception as exc:
        analyzer.safe_record_error(
            phase=phase,
            error=exc,
            plan_id=plan_id,
        )
