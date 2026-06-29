from __future__ import annotations

from typing import Any
import json

from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .goal_debt import GoalDebtLedger
from .goal_review import GoalReviewer
from .schemas import Goal, GoalStatus, RiskLevel, new_id, utc_now
from .stores import GoalStore


class GoalDecomposer:
    """Creates bounded child goals under the existing GoalStore authority."""

    def __init__(self, db: Database, ledger: EvidenceLedger, goals: GoalStore):
        self.db = db
        self.ledger = ledger
        self.goals = goals
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS goal_decompositions (
                decomposition_id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                specification_json TEXT NOT NULL,
                child_goal_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    def decompose(self, goal_id: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        parent = self.goals.get(goal_id)
        if parent is None:
            raise KeyError(f"goal not found: {goal_id}")
        if parent.parent_goal_id is not None:
            raise ValueError("only a parent goal may be decomposed")
        if not tasks:
            raise ValueError("at least one child task is required")
        if len(tasks) > 32:
            raise ValueError("decomposition is bounded to 32 child tasks")
        if self.goals.children(goal_id, include_archived=True):
            raise ValueError("goal is already decomposed")

        decomposition_id = new_id("goaldecomp")
        child_ids: list[str] = []
        task_records: list[dict[str, Any]] = []
        for index, item in enumerate(tasks):
            title = str(item.get("title", "")).strip()
            if not title:
                raise ValueError(f"task {index} requires a title")
            task_spec = dict(item.get("task_spec", {}))
            if not task_spec.get("action"):
                raise ValueError(f"task {index} requires task_spec.action")
            dependencies = [str(value) for value in item.get("dependencies", [])]
            if dependencies == ["previous"]:
                dependencies = child_ids[-1:] if child_ids else []
            unknown = set(dependencies) - set(child_ids)
            if unknown:
                raise ValueError(f"task {index} depends on unknown child goals: {sorted(unknown)}")
            child = Goal(
                title=title,
                description=str(item.get("description", title)),
                priority=max(0.0, min(1.0, float(item.get("priority", parent.priority)))),
                success_criteria=[str(value) for value in item.get("success_criteria", ["governed action succeeds with evidence"])],
                source=parent.source,
                autonomous=parent.autonomous,
                parent_goal_id=goal_id,
                deadline=parent.deadline,
                status=GoalStatus.WAITING if dependencies else GoalStatus.ACTIVE,
                rationale=str(item.get("rationale", "Bounded child task derived from the accepted parent goal.")),
                origin=f"decomposition:{goal_id}",
                task_spec=task_spec,
                dependencies=dependencies,
                remaining_work=[str(value) for value in item.get("remaining_work", [title])],
                risk=RiskLevel(str(item.get("risk", RiskLevel.READ.value))),
            )
            child_id = self.goals.add(child)
            child_ids.append(child_id)
            task_records.append(
                {
                    "goal_id": child_id,
                    "title": title,
                    "task_spec": task_spec,
                    "dependencies": dependencies,
                }
            )
        self.goals.set_state(goal_id, status=GoalStatus.DECOMPOSED, progress=0.0)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO goal_decompositions(
                    decomposition_id,goal_id,specification_json,child_goal_ids_json,created_at
                ) VALUES (?,?,?,?,?)
                """,
                (
                    decomposition_id,
                    goal_id,
                    json.dumps(task_records, ensure_ascii=False, sort_keys=True),
                    json.dumps(child_ids, ensure_ascii=False),
                    utc_now(),
                ),
            )
            self.ledger.append(
                "goal_decomposed",
                {
                    "decomposition_id": decomposition_id,
                    "goal_id": goal_id,
                    "child_goal_ids": child_ids,
                    "tasks": task_records,
                },
                connection,
            )
        return {
            "decomposition_id": decomposition_id,
            "goal_id": goal_id,
            "subgoal_ids": child_ids,
            "task_specs": task_records,
        }


class GoalRuntime:
    """Canonical goal lifecycle, attribution, interruption recovery, and ablation switch."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        goals: GoalStore,
        config: RuntimeConfig,
    ):
        self.db = db
        self.ledger = ledger
        self.goals = goals
        self.config = config
        self.debts = GoalDebtLedger(db, ledger)
        self.reviewer = GoalReviewer(db, ledger, goals, self.debts)
        self.decomposer = GoalDecomposer(db, ledger, goals)
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS goal_attributions (
                goal_trace_id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL UNIQUE,
                decision_trace_id TEXT,
                selected_goal_ids_json TEXT NOT NULL,
                counterfactual_json TEXT NOT NULL,
                selected_key TEXT,
                goal_influenced_decision INTEGER NOT NULL,
                goal_delta REAL NOT NULL,
                causal_reason TEXT NOT NULL,
                outcome_json TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )

    @property
    def enabled(self) -> bool:
        return str(self.config.provider.get("goal_mode", "enabled")).lower() != "disabled"

    @property
    def frozen(self) -> bool:
        return str(self.config.provider.get("goal_mode", "enabled")).lower() == "frozen"

    def prepare_cycle(self, cycle_id: str, *, limit: int = 20) -> dict[str, Any]:
        if not self.enabled:
            return {"mode": "disabled", "goals": [], "selected_goal_ids": [], "resumed_goal_ids": []}
        reviews = self.reviewer.review_all(cycle_id=cycle_id, reason="cycle_prepare")
        actionable = self.goals.actionable(limit=limit)
        resumed = [
            goal.goal_id
            for goal in actionable
            if goal.recovery_count > 0 and goal.interruption_count > 0
        ]
        return {
            "mode": "frozen" if self.frozen else "enabled",
            "goals": actionable,
            "selected_goal_ids": [actionable[0].goal_id] if actionable else [],
            "resumed_goal_ids": resumed,
            "reviews": reviews,
        }

    def record_decision(
        self,
        cycle_id: str,
        *,
        selected_goal_ids: list[str],
        counterfactual: dict[str, Any],
    ) -> dict[str, Any]:
        trace = self.db.query_one(
            "SELECT trace_id,selected_key,selected_hypothesis_id FROM cognitive_traces WHERE cycle_id=?",
            (cycle_id,),
        )
        selected_key = str(trace["selected_key"]) if trace else None
        selected_score = 0.0
        decision_trace_id = str(trace["trace_id"]) if trace else None
        if trace and trace["selected_hypothesis_id"]:
            row = self.db.query_one(
                "SELECT score FROM cognitive_hypotheses WHERE hypothesis_id=?",
                (trace["selected_hypothesis_id"],),
            )
            selected_score = float(row["score"]) if row else 0.0
        counter_key = str(counterfactual.get("key", ""))
        counter_score = float(counterfactual.get("score", 0.0))
        influenced = bool(selected_goal_ids and selected_key != counter_key)
        delta = selected_score - counter_score
        causal_reason = (
            f"selected={selected_key}; goal_free={counter_key}; "
            f"selected_goal_ids={','.join(selected_goal_ids) or 'none'}"
        )
        goal_trace_id = new_id("goaltrace")
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO goal_attributions(
                    goal_trace_id,cycle_id,decision_trace_id,selected_goal_ids_json,
                    counterfactual_json,selected_key,goal_influenced_decision,goal_delta,
                    causal_reason,outcome_json,created_at,resolved_at
                ) VALUES (?,?,?,?,?,?,?,?,?,NULL,?,NULL)
                """,
                (
                    goal_trace_id,
                    cycle_id,
                    decision_trace_id,
                    json.dumps(selected_goal_ids, ensure_ascii=False),
                    json.dumps(counterfactual, ensure_ascii=False, sort_keys=True),
                    selected_key,
                    int(influenced),
                    delta,
                    causal_reason,
                    utc_now(),
                ),
            )
            self.ledger.append(
                "goal_decision_attributed",
                {
                    "goal_trace_id": goal_trace_id,
                    "cycle_id": cycle_id,
                    "decision_trace_id": decision_trace_id,
                    "selected_goal_ids": selected_goal_ids,
                    "counterfactual_without_goal": counterfactual,
                    "selected_key": selected_key,
                    "goal_influenced_decision": influenced,
                    "goal_delta": delta,
                    "causal_reason": causal_reason,
                },
                connection,
            )
        return {
            "goal_trace_id": goal_trace_id,
            "decision_trace_id": decision_trace_id,
            "selected_goal_ids": selected_goal_ids,
            "counterfactual_without_goal": counterfactual,
            "selected_key": selected_key,
            "goal_influenced_decision": influenced,
            "goal_delta": delta,
            "causal_reason": causal_reason,
        }

    def resolve_cycle(
        self,
        cycle_id: str,
        *,
        selected_goal_ids: list[str],
        outcomes: list[dict[str, Any]],
        attribution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"mode": "disabled", "goal_updates": [], "frozen": False}
        if self.frozen:
            updates: list[dict[str, Any]] = []
        else:
            updates = self.reviewer.apply_outcomes(cycle_id, selected_goal_ids, outcomes)
        result = {
            "mode": "frozen" if self.frozen else "enabled",
            "goal_updates": updates,
            "frozen": self.frozen,
            "observed_actions": len(outcomes),
            "successful_actions": sum(bool(item.get("success")) for item in outcomes),
        }
        if attribution:
            with self.db.transaction() as connection:
                connection.execute(
                    "UPDATE goal_attributions SET outcome_json=?,resolved_at=? WHERE goal_trace_id=?",
                    (
                        json.dumps(result, ensure_ascii=False, sort_keys=True),
                        utc_now(),
                        attribution["goal_trace_id"],
                    ),
                )
                self.ledger.append(
                    "goal_attribution_resolved",
                    {"goal_trace_id": attribution["goal_trace_id"], "outcome": result},
                    connection,
                )
        return result

    def review_all(self, *, reason: str = "periodic") -> list[dict[str, Any]]:
        return self.reviewer.review_all(reason=reason)

    def archive_completed(self) -> list[str]:
        return self.goals.archive_completed()

    def summary(self) -> dict[str, Any]:
        counts = {
            str(row["status"]): int(row["n"])
            for row in self.db.query_all(
                "SELECT status,COUNT(*) AS n FROM goals GROUP BY status"
            )
        }
        attribution = self.db.query_one(
            """
            SELECT COUNT(*) AS traces,
                   COALESCE(SUM(goal_influenced_decision),0) AS influenced,
                   COALESCE(AVG(goal_delta),0.0) AS mean_delta
            FROM goal_attributions
            """
        )
        return {
            "mode": "disabled" if not self.enabled else ("frozen" if self.frozen else "enabled"),
            "status_counts": counts,
            "open_debt": len(self.debts.open_all(limit=1000)),
            "attribution_traces": int(attribution["traces"]) if attribution else 0,
            "goal_influenced_decisions": int(attribution["influenced"]) if attribution else 0,
            "mean_goal_delta": float(attribution["mean_delta"]) if attribution else 0.0,
        }

    def integrity(self) -> tuple[bool, dict[str, int]]:
        checks = {
            "orphan_goal_debts": """
                SELECT COUNT(*) AS n FROM goal_debts d
                LEFT JOIN goals g ON g.goal_id=d.goal_id
                WHERE g.goal_id IS NULL
            """,
            "orphan_goal_reviews": """
                SELECT COUNT(*) AS n FROM goal_reviews r
                LEFT JOIN goals g ON g.goal_id=r.goal_id
                WHERE g.goal_id IS NULL
            """,
            "orphan_goal_attributions": """
                SELECT COUNT(*) AS n FROM goal_attributions a
                LEFT JOIN cycles c ON c.cycle_id=a.cycle_id
                WHERE c.cycle_id IS NULL
            """,
            "completed_goal_without_full_progress": """
                SELECT COUNT(*) AS n FROM goals
                WHERE status IN ('COMPLETED','SUCCEEDED','ARCHIVED') AND progress < 1.0
            """,
            "dependency_self_reference": """
                SELECT COUNT(*) AS n FROM goals
                WHERE dependencies_json LIKE '%' || goal_id || '%'
            """,
        }
        counts: dict[str, int] = {}
        for name, sql in checks.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["n"]) if row else 0
        return all(value == 0 for value in counts.values()), counts

    def recent_attributions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM goal_attributions ORDER BY created_at DESC LIMIT ?",
            (max(1, min(200, int(limit))),),
        )
        return [
            {
                "goal_trace_id": row["goal_trace_id"],
                "cycle_id": row["cycle_id"],
                "decision_trace_id": row["decision_trace_id"],
                "selected_goal_ids": json.loads(row["selected_goal_ids_json"]),
                "counterfactual_without_goal": json.loads(row["counterfactual_json"]),
                "selected_key": row["selected_key"],
                "goal_influenced_decision": bool(row["goal_influenced_decision"]),
                "goal_delta": float(row["goal_delta"]),
                "causal_reason": row["causal_reason"],
                "outcome": json.loads(row["outcome_json"]) if row["outcome_json"] else None,
                "created_at": row["created_at"],
                "resolved_at": row["resolved_at"],
            }
            for row in rows
        ]
