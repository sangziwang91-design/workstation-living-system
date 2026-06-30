from __future__ import annotations

from typing import Any


class OwnerConsoleProjection:
    """Read-only rebuildable projection over LivingSystem.status()."""

    ALLOWED_KEYS = {
        "version",
        "home",
        "read_only",
        "paused",
        "killed",
        "cycle_count",
        "event_counts",
        "pending_actions",
        "active_goals",
        "growth_cycles",
        "planner_provider",
        "next_focus",
    }

    def project(self, status: dict[str, Any]) -> dict[str, Any]:
        return {key: status.get(key) for key in sorted(self.ALLOWED_KEYS)}
