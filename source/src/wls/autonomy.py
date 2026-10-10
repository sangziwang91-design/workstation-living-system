from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json

from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .schemas import Goal, RiskLevel
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


    def _observed_growth_goal(self) -> Goal | None:
        """Select one real failure hypothesis from canonical WLS history.

        A candidate is an investigation request, never permission to modify
        code, execute untrusted proposals, or promote a skill.
        """
        # Rotate a bounded scan through historical candidates. The oldest
        # unexecutable 32 proposals must not permanently starve newer real
        # failures. The cursor is stored in the existing runtime_state table.
        try:
            cursor = max(0, int(self.db.get_runtime("autonomy_growth_cursor", 0)))
        except (ValueError, TypeError):
            cursor = 0
        query = """
            SELECT rowid AS ordinal, candidate_id, candidate_type, source_ids_json
            FROM evolution_candidates
            WHERE candidate_type IN ('failure_repair', 'world_model_revision')
              AND status='PROPOSED'
              AND rowid>?
            ORDER BY rowid ASC LIMIT 32
        """
        candidates = self.db.query_all(query, (cursor,))
        if not candidates and cursor:
            cursor = 0
            candidates = self.db.query_all(query, (cursor,))
        if not candidates:
            return None
        for candidate in candidates:
            candidate_id = str(candidate["candidate_id"])
            revision = str(candidate["candidate_type"]) == "world_model_revision"
            title = (
                f"Inspect prediction errors: {candidate_id}"
                if revision else f"Inspect recurring failures: {candidate_id}"
            )
            # Check every status, including completed/failed goals, so a
            # restart or an unchanged failed hypothesis cannot loop forever.
            if self.db.query_one("SELECT goal_id FROM goals WHERE title=?", (title,)):
                continue
            try:
                source_ids = json.loads(candidate["source_ids_json"])
            except (TypeError, ValueError):
                continue
            if (
                not isinstance(source_ids, list)
                or not (2 if revision else 3) <= len(source_ids) <= 64
                or any(not isinstance(item, str) or not item for item in source_ids)
                or len(set(source_ids)) != len(source_ids)
            ):
                continue
            placeholders = ",".join("?" for _ in source_ids)
            if revision:
                from math import isfinite

                observations = self.db.query_all(
                    f"""SELECT prediction_id,subject,predicate,error_score
                    FROM predictions WHERE prediction_id IN ({placeholders})
                      AND status='REFUTED' AND resolved_at IS NOT NULL
                      AND error_score>=0.5 AND error_score<=1.0""",
                    tuple(source_ids),
                )
                if len(observations) != len(source_ids):
                    continue
                if len({(row["subject"], row["predicate"]) for row in observations}) != 1:
                    continue
                if any(
                    row["error_score"] is None
                    or not isfinite(float(row["error_score"]))
                    for row in observations
                ):
                    continue
            else:
                outcomes = self.db.query_all(
                    f"""SELECT action_id, result_json, tool, risk FROM actions
                    WHERE action_id IN ({placeholders})
                      AND status='FAILED' AND started_at IS NOT NULL
                      AND finished_at IS NOT NULL AND result_json IS NOT NULL""",
                    tuple(source_ids),
                )
                if len(outcomes) != len(source_ids):
                    continue
                # Only completed, independently evaluated fixed-tool failures
                # may become automatic recovery experiments.
                if any(
                    row["risk"] != RiskLevel.READ.value
                    or row["tool"] not in {"noop", "read_file", "list_directory"}
                    for row in outcomes
                ):
                    continue
                try:
                    if any(
                        json.loads(row["result_json"]).get("evaluation", {}).get("accepted")
                        is not False
                        for row in outcomes
                    ):
                        continue
                except (TypeError, ValueError, AttributeError):
                    continue
            self.db.set_runtime("autonomy_growth_cursor", int(candidate["ordinal"]))
            return Goal(
                title=title,
                description=(
                    "Inspect independent refuted predictions, seek counterexamples "
                    "and propose one falsifiable revision without rewriting facts."
                    if revision else
                    "Inspect the actual failed WLS action evidence and formulate "
                    "one bounded, independently testable recovery experiment. "
                    "Do not execute untrusted code, grant permissions, or promote "
                    "a candidate without the existing owner gate."
                ),
                priority=0.7,
                success_criteria=[
                    "one source-linked hypothesis and a bounded read-only investigation",
                    "subsequent skill promotion remains owner-authorized",
                ],
                source="autonomy.inquiry" if revision else "autonomy.learning",
                autonomous=True,
                risk=RiskLevel.READ,
                task_spec={
                    "operation": "INSPECT",
                    "candidate_id": candidate_id,
                    "source_prediction_ids" if revision else "source_action_ids": source_ids,
                    "proposal_only": True,
                },
            )
        self.db.set_runtime("autonomy_growth_cursor", int(candidates[-1]["ordinal"]))
        return None

    def consider(self) -> list[str]:
        autonomous_count = self.goals.autonomous_count()
        if autonomous_count >= self.config.max_autonomous_goals:
            return []
        now = datetime.now(UTC)
        last_considered = self.db.get_runtime("autonomy_last_considered_at", None)
        due = True
        if last_considered:
            try:
                elapsed = (
                    now - datetime.fromisoformat(str(last_considered))
                ).total_seconds()
                due = elapsed >= self.config.autonomy_consider_interval_seconds
            except ValueError:
                due = True
        proposals: list[Goal] = []
        if due:
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
        unknown_snapshot = self.db.query_one(
            "SELECT COUNT(*) AS total, MIN(action_id) AS first_id, "
            "MAX(action_id) AS last_id FROM actions "
            "WHERE status='UNKNOWN_SIDE_EFFECT'"
        )
        if unknown_snapshot and int(unknown_snapshot["total"]) > 0:
            sample_rows = self.db.query_all(
                "SELECT action_id FROM actions WHERE status='UNKNOWN_SIDE_EFFECT' "
                "ORDER BY action_id LIMIT 64"
            )
            source_ids = [str(row["action_id"]) for row in sample_rows]
            total = int(unknown_snapshot["total"])
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "total": total,
                        "first_id": unknown_snapshot["first_id"],
                        "last_id": unknown_snapshot["last_id"],
                        "sample": source_ids,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:16]
            proposals.append(
                Goal(
                    title=f"Preserve action-state truth: {fingerprint}",
                    description="Reconcile actions with unknown side effects without replaying them automatically.",
                    priority=0.9,
                    success_criteria=[
                        "all unknown side effects receive external evidence and human resolution"
                    ],
                    source="autonomy.safety",
                    autonomous=True,
                    risk=RiskLevel.READ,
                    task_spec={
                        "source_action_ids": source_ids,
                        "unknown_action_count": total,
                        "proposal_only": True,
                    },
                )
            )
        growth_goal = self._observed_growth_goal()
        if growth_goal is not None:
            proposals.append(growth_goal)
        created: list[str] = []
        for goal in proposals:
            if self.goals.autonomous_count() >= self.config.max_autonomous_goals:
                break
            if not goal.title.startswith(self.ALLOWED_PREFIXES):
                continue
            if goal.source == "autonomy.safety":
                existing = self.db.query_one(
                    "SELECT goal_id FROM goals WHERE title=?", (goal.title,)
                )
            else:
                existing = self.db.query_one(
                    "SELECT goal_id FROM goals WHERE title=? AND status IN ('ACTIVE','BLOCKED')",
                    (goal.title,),
                )
            if existing:
                continue
            created.append(self.goals.add(goal))
        if created:
            self.ledger.append("autonomous_goals_created", {"goal_ids": created})
        if due or created:
            self.db.set_runtime("autonomy_last_considered_at", now.isoformat())
        return created
