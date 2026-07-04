from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from wls.schemas import Goal
from wls.ui_projection import UIProjection


class MiniDB:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE goals (
                goal_id TEXT PRIMARY KEY,title TEXT,description TEXT,priority REAL,
                success_criteria_json TEXT,source TEXT,autonomous INTEGER,
                parent_goal_id TEXT,deadline TEXT,status TEXT,progress REAL,
                created_at TEXT,updated_at TEXT
            );
            CREATE TABLE cycles (
                cycle_id TEXT PRIMARY KEY,started_at TEXT,finished_at TEXT,status TEXT,
                workspace_json TEXT,metrics_json TEXT,error TEXT
            );
            CREATE TABLE plans (
                plan_id TEXT PRIMARY KEY,cycle_id TEXT,plan_json TEXT,status TEXT,
                created_at TEXT,completed_at TEXT
            );
            CREATE TABLE actions (
                action_id TEXT PRIMARY KEY,plan_id TEXT,goal_id TEXT,skill_id TEXT,
                tool TEXT,arguments_json TEXT,purpose TEXT,expected_result TEXT,
                risk TEXT,acceptance_json TEXT,idempotency_key TEXT,status TEXT,
                approval_id TEXT,started_at TEXT,finished_at TEXT,result_json TEXT,
                error TEXT,side_effect_class TEXT
            );
            CREATE TABLE events (status TEXT);
            CREATE TABLE evidence (
                seq INTEGER PRIMARY KEY,evidence_id TEXT,event_type TEXT,payload_json TEXT,
                created_at TEXT,previous_hash TEXT,record_hash TEXT,signature TEXT
            );
            CREATE TABLE skills (
                skill_id TEXT,name TEXT,version INTEGER,status TEXT,success_rate REAL,
                use_count INTEGER,definition_json TEXT,created_at TEXT,updated_at TEXT
            );
            """
        )

    def query_all(self, sql, parameters=()):
        return list(self.connection.execute(sql, parameters).fetchall())

    def query_one(self, sql, parameters=()):
        return self.connection.execute(sql, parameters).fetchone()


class GoalFacade:
    def __init__(self, db: MiniDB) -> None:
        self.db = db

    def get(self, goal_id: str):
        return self.db.query_one("SELECT * FROM goals WHERE goal_id=?", (goal_id,))

    def add(self, goal: Goal) -> str:
        self.db.connection.execute(
            "INSERT INTO goals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                goal.goal_id,
                goal.title,
                goal.description,
                goal.priority,
                json.dumps(goal.success_criteria),
                goal.source,
                int(goal.autonomous),
                goal.parent_goal_id,
                goal.deadline,
                goal.status.value,
                goal.progress,
                goal.created_at,
                goal.updated_at,
            ),
        )
        return goal.goal_id


class ApprovalFacade:
    def __init__(self) -> None:
        self.calls = []

    def issue(self, action_id, approved, minutes, reason):
        self.calls.append((action_id, approved, minutes, reason))
        return f"approval-{action_id}-{int(approved)}"


class FakeRuntime:
    def __init__(self) -> None:
        self.db = MiniDB()
        self.goals = GoalFacade(self.db)
        self.approvals = ApprovalFacade()
        self.config = SimpleNamespace(secret_path="secret.key")
        self.resumed = []
        self.resolved = []
        self.cycle_result = {"status": "COMPLETED"}

    def status(self):
        return {
            "version": "test",
            "home": "/tmp/wls",
            "read_only": False,
            "paused": False,
            "killed": False,
            "planner_provider": "deterministic",
            "event_counts": {},
            "latest_cycle": None,
            "pending_actions": [],
            "active_goals": [],
            "next_focus": [],
        }

    def add_goal(self, goal: Goal) -> str:
        return self.goals.add(goal)

    def run_cycle(self):
        return self.cycle_result

    def resume_action(self, action_id: str):
        self.resumed.append(action_id)
        return {"action_id": action_id, "status": "SUCCEEDED"}

    def resolve_unknown_action(self, action_id: str, resolution: str, evidence: dict):
        self.resolved.append((action_id, resolution, evidence))


def add_action(
    runtime: FakeRuntime,
    *,
    action_id: str,
    goal_id: str | None = None,
    status: str = "WAITING_APPROVAL",
    cycle_id: str = "cycle-1",
) -> None:
    c = runtime.db.connection
    c.execute(
        "INSERT OR IGNORE INTO cycles VALUES (?,?,?,?,?,?,?)",
        (cycle_id, "2026-07-04T00:00:00Z", None, "RUNNING", "{}", "{}", None),
    )
    plan_id = f"plan-{cycle_id}"
    c.execute(
        "INSERT OR IGNORE INTO plans VALUES (?,?,?,?,?,?)",
        (plan_id, cycle_id, "{}", "RUNNING", "2026-07-04T00:00:00Z", None),
    )
    c.execute(
        "INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            action_id,
            plan_id,
            goal_id,
            None,
            "read_file",
            "{}",
            "Read state",
            "state loaded",
            "READ",
            "[]",
            f"key-{action_id}",
            status,
            "approval-x" if status == "APPROVED" else None,
            None,
            None,
            None,
            None,
            "none",
        ),
    )


def test_projection_maps_top_level_goals_to_projects() -> None:
    runtime = FakeRuntime()
    projection = UIProjection(runtime)
    project_id = projection.create_goal({"kind": "project", "title": "WLS"})[
        "goal_id"
    ]
    projection.create_goal(
        {
            "kind": "task",
            "project_id": project_id,
            "title": "Build owner console",
            "success_criteria": ["UI starts"],
        }
    )

    projects = projection.projects()
    tasks = projection.tasks(project_id=project_id)

    assert projects[0]["goal_id"] == project_id
    assert projects[0]["task_count"] == 1
    assert tasks[0]["title"] == "Build owner console"
    assert tasks[0]["parent_goal_id"] == project_id


def test_task_requires_project_id() -> None:
    projection = UIProjection(FakeRuntime())
    with pytest.raises(ValueError, match="project_id is required"):
        projection.create_goal({"kind": "task", "title": "Orphan"})


def test_task_parent_must_be_top_level_project() -> None:
    runtime = FakeRuntime()
    projection = UIProjection(runtime)
    project_id = projection.create_goal({"kind": "project", "title": "P"})[
        "goal_id"
    ]
    task_id = projection.create_goal(
        {"kind": "task", "project_id": project_id, "title": "T"}
    )["goal_id"]
    with pytest.raises(ValueError, match="top-level"):
        projection.create_goal(
            {"kind": "task", "project_id": task_id, "title": "Nested via UI"}
        )


def test_project_runs_include_child_task_actions() -> None:
    runtime = FakeRuntime()
    projection = UIProjection(runtime)
    project_id = projection.create_goal({"kind": "project", "title": "WLS"})[
        "goal_id"
    ]
    task_id = projection.create_goal(
        {"kind": "task", "project_id": project_id, "title": "UI"}
    )["goal_id"]
    add_action(runtime, action_id="act-child", goal_id=task_id)

    detail = projection.project_detail(project_id)

    assert detail["recent_runs"][0]["run_id"] == "cycle-1"
    assert detail["recent_runs"][0]["action_count"] == 1


def test_action_operations_are_state_specific() -> None:
    runtime = FakeRuntime()
    add_action(runtime, action_id="wait", status="WAITING_APPROVAL")
    add_action(runtime, action_id="approved", status="APPROVED", cycle_id="cycle-2")
    add_action(
        runtime, action_id="unknown", status="UNKNOWN_SIDE_EFFECT", cycle_id="cycle-3"
    )
    projection = UIProjection(runtime)

    assert projection.decide_action("wait", approved=True)["approved"] is True
    assert projection.resume_action("approved")["status"] == "SUCCEEDED"
    assert (
        projection.resolve_unknown_action(
            "unknown", resolution="FAILED", evidence={"owner_note": "verified"}
        )["resolved"]
        is True
    )


def test_library_projects_evidence_and_skills() -> None:
    runtime = FakeRuntime()
    c = runtime.db.connection
    c.execute(
        "INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)",
        ("1", "ev-1", "runtime_initialized", '{"ok": true}', "now", "p", "h", "s"),
    )
    c.execute(
        "INSERT INTO skills VALUES (?,?,?,?,?,?,?,?,?)",
        ("sk-1", "read", 1, "PROPOSED", 0.0, 0, "{}", "now", "now"),
    )

    library = UIProjection(runtime).library()

    assert library["evidence"][0]["event_type"] == "runtime_initialized"
    assert library["evidence"][0]["payload"]["ok"] is True
    assert library["skills"][0]["name"] == "read"
