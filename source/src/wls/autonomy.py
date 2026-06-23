from __future__ import annotations


from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .schemas import Goal
from .stores import GoalStore
from .world import WorldModel


class AutonomySystem:
    """Creates only bounded maintenance, inquiry, and learning goals."""

    ALLOWED_PREFIXES = ("Clarify", "Inspect", "Learn", "Recover", "Preserve")

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        goals: GoalStore,
        world: WorldModel,
        config: RuntimeConfig,
    ):
        self.db = db
        self.ledger = ledger
        self.goals = goals
        self.world = world
        self.config = config

    def consider(self) -> list[str]:
        if self.goals.autonomous_count() >= self.config.max_autonomous_goals:
            return []
        proposals: list[Goal] = []
        contradictions = self.world.unresolved_contradictions(limit=3)
        for item in contradictions:
            title = f"Clarify contradiction: {item['subject']} / {item['predicate']}"
            proposals.append(
                Goal(
                    title=title,
                    description="Collect additional direct evidence and avoid choosing a fact by narrative preference.",
                    priority=0.65,
                    success_criteria=[
                        "contradiction has one verified active interpretation or remains explicitly UNKNOWN"
                    ],
                    source="autonomy.reality_coherence",
                    autonomous=True,
                )
            )
        failed_sensor = self.db.query_one(
            "SELECT sensor_name,last_error FROM sensor_state WHERE last_error IS NOT NULL ORDER BY last_polled_at DESC LIMIT 1"
        )
        if failed_sensor:
            proposals.append(
                Goal(
                    title=f"Recover sensor: {failed_sensor['sensor_name']}",
                    description="Diagnose the sensor failure using read-only evidence and request approval before any repair.",
                    priority=0.75,
                    success_criteria=[
                        "sensor records a successful poll or failure is escalated with evidence"
                    ],
                    source="autonomy.continuity",
                    autonomous=True,
                )
            )
        unknown_actions = self.db.query_one(
            "SELECT COUNT(*) AS n FROM actions WHERE status='UNKNOWN_SIDE_EFFECT'"
        )
        if unknown_actions and int(unknown_actions["n"]) > 0:
            proposals.append(
                Goal(
                    title="Preserve action-state truth",
                    description="Reconcile actions with unknown side effects without replaying them automatically.",
                    priority=0.9,
                    success_criteria=[
                        "all unknown side effects receive external evidence and human resolution"
                    ],
                    source="autonomy.safety",
                    autonomous=True,
                )
            )
        created: list[str] = []
        for goal in proposals:
            if self.goals.autonomous_count() >= self.config.max_autonomous_goals:
                break
            if not goal.title.startswith(self.ALLOWED_PREFIXES):
                continue
            existing = self.db.query_one(
                "SELECT goal_id FROM goals WHERE title=? AND status IN ('ACTIVE','BLOCKED')",
                (goal.title,),
            )
            if existing:
                continue
            created.append(self.goals.add(goal))
        if created:
            self.ledger.append("autonomous_goals_created", {"goal_ids": created})
        return created
