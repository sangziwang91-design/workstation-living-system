"""Goal runtime state and lifecycle management.

This module provides goal-aware runtime extensions for persistent
multi-round continuity. It does not introduce a parallel runtime,
but extends the canonical LivingSystem with goal state tracking.
"""

from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import Goal, new_id, utc_now


class GoalRuntime:
    """Goal lifecycle state machine for persistent continuity.
    
    Tracks goal decomposition, debt ledger, interruption recovery,
    and restart continuity within the canonical runtime.
    """

    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger

    def snapshot(self) -> dict[str, Any]:
        """Return goal runtime state snapshot for CURRENT_STATE."""
        total_row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM goals"
        )
        active_row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM goals WHERE status IN ('ACTIVE', 'BLOCKED')"
        )
        return {
            "total_goals": int(total_row["n"]) if total_row else 0,
            "active_goals": int(active_row["n"]) if active_row else 0,
            "debt_ledger_exists": True,
        }

    def initialize_identity(self) -> str:
        """Initialize goal runtime state at startup."""
        evidence_id = self.ledger.append(
            "goal_runtime_initialized",
            {"timestamp": utc_now()},
        )
        return evidence_id

    def integrity_check(self) -> tuple[bool, dict[str, Any]]:
        """Verify goal runtime consistency."""
        total = self.db.query_one("SELECT COUNT(*) AS n FROM goals")
        with_ids = self.db.query_one(
            "SELECT COUNT(*) AS n FROM goals WHERE goal_id IS NOT NULL"
        )
        ok = (
            total is not None
            and with_ids is not None
            and int(total["n"]) == int(with_ids["n"])
        )
        return ok, {
            "total_goals": int(total["n"]) if total else 0,
            "goals_with_ids": int(with_ids["n"]) if with_ids else 0,
        }
