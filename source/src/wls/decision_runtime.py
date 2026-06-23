from __future__ import annotations

from typing import Any, cast

from .decision_memory import MemoryAttributionPlanner, ensure_decision_memory_tables
from .v2_runtime import LivingSystemV2


class DecisionAwareRuntime(LivingSystemV2):
    """Runtime extension that records memory-use comparisons."""

    def __init__(self, config) -> None:
        super().__init__(config)
        ensure_decision_memory_tables(self.db)
        self.memory_planner = MemoryAttributionPlanner(
            self.planner,
            self.db,
            self.ledger,
            str(config.provider.get("type", "deterministic")),
        )
        self.planner = cast(Any, self.memory_planner)

    def status(self) -> dict[str, Any]:
        value = super().status()
        value["memory_decision_attribution"] = self.memory_planner.summary(limit=500)
        return value
