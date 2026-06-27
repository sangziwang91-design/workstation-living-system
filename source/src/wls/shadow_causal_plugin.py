from __future__ import annotations

from typing import Any, Callable

from .shadow_causal import ShadowCausalAnalyzer


def register_wls(runtime: Any) -> None:
    """Opt-in read-only shadow integration through WLS's existing plugin contract.

    Enable with:

    ``"plugin_modules": ["wls.shadow_causal_plugin"]``

    The plugin wraps only the current runtime instance. It does not create another
    runtime, event store, planner, memory store, world model, or action executor.
    """
    analyzer = ShadowCausalAnalyzer(runtime.db, runtime.ledger)
    runtime.shadow_causal = analyzer

    try:
        analyzer.recover_pending()
    except Exception as exc:  # failure isolation: canonical initialization continues
        analyzer.safe_record_error(phase="RECOVER_PENDING", error=exc)

    original_persist: Callable[..., Any] = runtime._persist_plan_and_ack_events
    original_execute: Callable[..., Any] = runtime._execute_plan

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
                    reason=f"canonical plan persistence failed: {type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            raise

    def execute_with_shadow(plan: Any) -> list[dict[str, Any]]:
        outcomes = original_execute(plan)
        try:
            analyzer.resolve(plan_id=str(plan.plan_id), outcomes=outcomes)
        except Exception as exc:
            analyzer.safe_record_error(
                phase="RESOLVE",
                error=exc,
                plan_id=str(plan.plan_id),
            )
        return outcomes

    runtime._persist_plan_and_ack_events = persist_with_shadow
    runtime._execute_plan = execute_with_shadow
