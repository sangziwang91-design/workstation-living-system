from __future__ import annotations

from typing import Any, Callable

from .shadow_causal import ShadowCausalAnalyzer


def register_wls(runtime: Any) -> None:
    """Attach an opt-in read-only causal shadow to one canonical WLS runtime.

    Enable only through the existing plugin contract:

    ``"plugin_modules": ["wls.shadow_causal_plugin"]``

    No runtime, event store, planner, memory, skill, world, goal, or action
    authority is created or replaced.
    """
    analyzer = ShadowCausalAnalyzer(runtime.db, runtime.ledger)
    runtime.shadow_causal = analyzer

    try:
        analyzer.recover_pending()
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
        analyzer.resolve(plan_id=plan_id, outcomes=outcomes)
    except Exception as exc:
        analyzer.safe_record_error(
            phase=phase,
            error=exc,
            plan_id=plan_id,
        )
