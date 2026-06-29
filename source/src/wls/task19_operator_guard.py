from __future__ import annotations

from types import MethodType
from typing import Any


def register_wls(runtime: Any) -> None:
    """Serialize operator reconciliation and suppress unrelated Action execution."""

    if getattr(runtime, "_task19_operator_guard_installed", False):
        return
    runtime._task19_operator_guard_installed = True
    original = runtime.resolve_unknown_action

    def resolve_unknown_action(
        self: Any,
        action_id: str,
        resolution: str,
        evidence: dict[str, Any],
    ) -> None:
        with self.lease:
            previous_budget = self.config.max_actions_per_cycle
            self.config.max_actions_per_cycle = 0
            try:
                original(action_id, resolution, evidence)
            finally:
                self.config.max_actions_per_cycle = previous_budget

    runtime.resolve_unknown_action = MethodType(resolve_unknown_action, runtime)
    runtime.ledger.append(
        "task19_operator_guard_installed",
        {
            "runtime_lease_required": True,
            "unrelated_action_budget_during_reconciliation": 0,
            "cycle_postprocessing_allowed": True,
        },
    )
