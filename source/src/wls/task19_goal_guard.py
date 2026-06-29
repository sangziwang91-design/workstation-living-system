from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .schemas import Goal, GoalStatus, RiskLevel, new_id, utc_now
from .task19_stabilization import _validated_task_spec


TERMINAL = {
    GoalStatus.COMPLETED.value,
    GoalStatus.SUCCEEDED.value,
    GoalStatus.FAILED.value,
    GoalStatus.CANCELLED.value,
    GoalStatus.ABANDONED.value,
    GoalStatus.ARCHIVED.value,
}


def _insert_goal(connection: Any, goal: Goal) -> None:
    connection.execute(
        """
        INSERT INTO goals(
            goal_id,title,description,priority,success_criteria_json,
            source,autonomous,parent_goal_id,deadline,status,progress,
            created_at,updated_at,rationale,origin,task_spec_json,
            dependencies_json,progress_evidence_json,remaining_work_json,
            risk,blocked_reason,contradiction_reason,interruption_count,
            recovery_count,completed_at,archived_at,last_reviewed_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            goal.goal_id,
            goal.title,
            goal.description,
            goal.priority,
            json.dumps(goal.success_criteria, ensure_ascii=False),
            goal.source,
            int(goal.autonomous),
            goal.parent_goal_id,
            goal.deadline,
            goal.status.value,
            goal.progress,
            goal.created_at,
            goal.updated_at,
            goal.rationale,
            goal.origin,
            json.dumps(goal.task_spec, ensure_ascii=False, sort_keys=True),
            json.dumps(goal.dependencies, ensure_ascii=False),
            json.dumps(goal.progress_evidence, ensure_ascii=False),
            json.dumps(goal.remaining_work, ensure_ascii=False),
            goal.risk.value,
            goal.blocked_reason,
            goal.contradiction_reason,
            goal.interruption_count,
            goal.recovery_count,
            goal.completed_at,
            goal.archived_at,
            goal.last_reviewed_at,
        ),
    )


def register_wls(runtime: Any) -> None:
    """Make goal decomposition atomic and dependency integrity exact."""

    if getattr(runtime, "_task19_goal_guard_installed", False):
        return
    runtime._task19_goal_guard_installed = True
    decomposer = runtime.goal_runtime.decomposer

    def decompose(
        self: Any,
        goal_id: str,
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        parent = runtime.goals.get(goal_id)
        if parent is None:
            raise KeyError(f"goal not found: {goal_id}")
        if parent.parent_goal_id is not None:
            raise ValueError("only a parent goal may be decomposed")
        if not tasks:
            raise ValueError("at least one child task is required")
        if len(tasks) > 32:
            raise ValueError("decomposition is bounded to 32 child tasks")

        decomposition_id = new_id("goaldecomp")
        child_ids: list[str] = []
        children: list[Goal] = []
        task_records: list[dict[str, Any]] = []
        for index, raw_item in enumerate(tasks):
            if not isinstance(raw_item, dict):
                raise ValueError(f"task {index} must be an object")
            item = dict(raw_item)
            title = str(item.get("title", "")).strip()
            if not title:
                raise ValueError(f"task {index} requires a title")
            task_spec = _validated_task_spec(item.get("task_spec"))
            dependencies = list(
                dict.fromkeys(
                    str(value).strip()
                    for value in item.get("dependencies", [])
                    if str(value).strip()
                )
            )
            if dependencies == ["previous"]:
                dependencies = child_ids[-1:] if child_ids else []
            unknown = set(dependencies) - set(child_ids)
            if unknown:
                raise ValueError(
                    f"task {index} depends on unknown child goals: {sorted(unknown)}"
                )
            risk = RiskLevel(str(item.get("risk", RiskLevel.READ.value)))
            success_criteria = [
                str(value).strip()
                for value in item.get(
                    "success_criteria",
                    ["governed action succeeds with evidence"],
                )
                if str(value).strip()
            ]
            if not success_criteria:
                raise ValueError(f"task {index} requires success criteria")
            child = Goal(
                title=title,
                description=str(item.get("description", title)),
                priority=max(
                    0.0,
                    min(1.0, float(item.get("priority", parent.priority))),
                ),
                success_criteria=success_criteria,
                source=parent.source,
                autonomous=parent.autonomous,
                parent_goal_id=goal_id,
                deadline=parent.deadline,
                status=GoalStatus.WAITING if dependencies else GoalStatus.ACTIVE,
                rationale=str(
                    item.get(
                        "rationale",
                        "Bounded child task derived from the accepted parent goal.",
                    )
                ),
                origin=f"decomposition:{goal_id}",
                task_spec=task_spec,
                dependencies=dependencies,
                remaining_work=[
                    str(value)
                    for value in item.get("remaining_work", [title])
                ],
                risk=risk,
            )
            children.append(child)
            child_ids.append(child.goal_id)
            task_records.append(
                {
                    "goal_id": child.goal_id,
                    "title": title,
                    "task_spec": task_spec,
                    "dependencies": dependencies,
                    "risk": risk.value,
                }
            )

        now = utc_now()
        with runtime.db.transaction() as connection:
            current_parent = connection.execute(
                "SELECT parent_goal_id,status FROM goals WHERE goal_id=?",
                (goal_id,),
            ).fetchone()
            if current_parent is None:
                raise KeyError(f"goal not found: {goal_id}")
            if current_parent["parent_goal_id"] is not None:
                raise ValueError("only a parent goal may be decomposed")
            if str(current_parent["status"]) in TERMINAL:
                raise ValueError("terminal parent goal cannot be decomposed")
            existing = connection.execute(
                "SELECT COUNT(*) AS n FROM goals WHERE parent_goal_id=?",
                (goal_id,),
            ).fetchone()
            if existing is not None and int(existing["n"]) > 0:
                raise ValueError("goal is already decomposed")
            prior_record = connection.execute(
                "SELECT 1 FROM goal_decompositions WHERE goal_id=? LIMIT 1",
                (goal_id,),
            ).fetchone()
            if prior_record is not None:
                raise ValueError("goal already has a decomposition record")

            for child in children:
                _insert_goal(connection, child)
                runtime.ledger.append(
                    "goal_created",
                    {"goal": child.to_dict()},
                    connection,
                )
            updated = connection.execute(
                """
                UPDATE goals SET status=?,progress=0.0,updated_at=?
                WHERE goal_id=? AND parent_goal_id IS NULL
                  AND status NOT IN (
                    'COMPLETED','SUCCEEDED','FAILED','CANCELLED','ABANDONED','ARCHIVED'
                  )
                """,
                (GoalStatus.DECOMPOSED.value, now, goal_id),
            ).rowcount
            if updated != 1:
                raise ValueError("parent goal changed during decomposition")
            connection.execute(
                """
                INSERT INTO goal_decompositions(
                    decomposition_id,goal_id,specification_json,
                    child_goal_ids_json,created_at
                ) VALUES (?,?,?,?,?)
                """,
                (
                    decomposition_id,
                    goal_id,
                    json.dumps(task_records, ensure_ascii=False, sort_keys=True),
                    json.dumps(child_ids, ensure_ascii=False),
                    now,
                ),
            )
            runtime.ledger.append(
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

    decomposer.decompose = MethodType(decompose, decomposer)

    def integrity(self: Any) -> tuple[bool, dict[str, int]]:
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
                WHERE status IN ('COMPLETED','SUCCEEDED','ARCHIVED')
                  AND progress < 1.0
            """,
            "dependency_self_reference": """
                SELECT COUNT(*) AS n
                FROM goals g, json_each(g.dependencies_json) dependency
                WHERE CAST(dependency.value AS TEXT)=g.goal_id
            """,
            "dependency_missing_goal": """
                SELECT COUNT(*) AS n
                FROM goals g, json_each(g.dependencies_json) dependency
                LEFT JOIN goals required
                  ON required.goal_id=CAST(dependency.value AS TEXT)
                WHERE required.goal_id IS NULL
            """,
        }
        counts: dict[str, int] = {}
        for name, sql in checks.items():
            row = runtime.db.query_one(sql)
            counts[name] = int(row["n"]) if row else 0
        return all(value == 0 for value in counts.values()), counts

    runtime.goal_runtime.integrity = MethodType(
        integrity, runtime.goal_runtime
    )
    runtime.ledger.append(
        "task19_goal_guard_installed",
        {
            "atomic_decomposition": True,
            "exact_dependency_integrity": True,
        },
    )
