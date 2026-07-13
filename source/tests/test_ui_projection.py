from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from wls.schemas import Goal
from wls.ui_projection import OwnerConsoleProductProjection, UIProjection


class MiniDB:
    def __init__(
        self,
        *,
        legacy_evidence: bool = False,
        archived_goal_column: bool = True,
    ) -> None:
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        evidence_optional = "" if legacy_evidence else ",source_type TEXT,producer TEXT,branch TEXT,commit_sha TEXT"
        goal_archive_columns = (
            ",completed_at TEXT,archived_at TEXT,last_reviewed_at TEXT"
            if archived_goal_column
            else ""
        )
        self.connection.executescript(
            f"""
            CREATE TABLE goals (
                goal_id TEXT PRIMARY KEY,title TEXT,description TEXT,priority REAL,
                success_criteria_json TEXT,source TEXT,autonomous INTEGER,
                parent_goal_id TEXT,deadline TEXT,status TEXT,progress REAL,
                created_at TEXT,updated_at TEXT,rationale TEXT,origin TEXT,
                task_spec_json TEXT,dependencies_json TEXT,progress_evidence_json TEXT,
                remaining_work_json TEXT,risk TEXT,blocked_reason TEXT,
                contradiction_reason TEXT,interruption_count INTEGER,recovery_count INTEGER
                {goal_archive_columns}
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
                created_at TEXT,record_hash TEXT{evidence_optional}
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
            "INSERT INTO goals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                goal.rationale,
                goal.origin,
                json.dumps(goal.task_spec),
                "[]",
                "[]",
                "[]",
                goal.risk.value,
                None,
                None,
                0,
                0,
                None,
                None,
                None,
            ),
        )
        return goal.goal_id


class ApprovalFacade:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, int, str]] = []

    def issue(self, action_id, approved, minutes, reason):
        self.calls.append((action_id, approved, minutes, reason))
        return f"approval-{action_id}-{int(approved)}"


class FakeRuntime:
    def __init__(
        self,
        *,
        legacy_evidence: bool = False,
        archived_goal_column: bool = True,
    ) -> None:
        self.db = MiniDB(
            legacy_evidence=legacy_evidence,
            archived_goal_column=archived_goal_column,
        )
        self.goals = GoalFacade(self.db)
        self.approvals = ApprovalFacade()
        self.config = SimpleNamespace(secret_path="secret.key")
        self.resumed: list[str] = []
        self.resolved: list[tuple[str, str, dict]] = []
        self.cycle_result = {"status": "COMPLETED"}
        self.garbage_receipts: list[dict] = []
        self.garbage_clear_receipts: list[dict] = []

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
            "garbage_audit_receipts": self.garbage_receipts,
            "garbage_quarantine_clear_receipts": self.garbage_clear_receipts,
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
        return {"resolved": True}

    def garbage_audit(
        self,
        *,
        max_candidates=500,
        reason="",
        execute_cleanup=False,
        approval_reference=None,
        roots=None,
    ):
        receipt = {
            "audit_id": f"garbage_audit_{len(self.garbage_receipts) + 1}",
            "status": "CLEANUP_EXECUTED" if execute_cleanup else "OWNER_REVIEW_REQUIRED",
            "candidate_count": 1,
            "candidates": [{"candidate_id": "candidate-1", "kind": "temporary_file"}],
            "cleanup_executed": execute_cleanup,
            "reason": reason,
            "approval_reference": approval_reference,
            "max_candidates": max_candidates,
            "quarantine_root": "/tmp/wls/.wls_quarantine/audit"
            if execute_cleanup
            else None,
        }
        self.garbage_receipts.insert(0, receipt)
        return receipt

    def garbage_audit_receipts(self, limit=100):
        return self.garbage_receipts[:limit]

    def clear_garbage_quarantine(self, *, audit_id, approval_reference, reason):
        receipt = {
            "clear_id": (
                f"garbage_quarantine_clear_{len(self.garbage_clear_receipts) + 1}"
            ),
            "audit_id": audit_id,
            "approval_reference": approval_reference,
            "reason": reason,
            "status": "QUARANTINE_CLEARED",
        }
        self.garbage_clear_receipts.insert(0, receipt)
        return receipt


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
            "rationale": "Owner requested UI hardening",
            "risk": "READ",
        }
    )

    projects = projection.projects()
    tasks = projection.tasks(project_id=project_id)

    assert projects[0]["goal_id"] == project_id
    assert projects[0]["task_count"] == 1
    assert projects[0]["origin"] == "owner"
    assert projects[0]["task_spec"]["kind"] == "project"
    assert tasks[0]["title"] == "Build owner console"
    assert tasks[0]["parent_goal_id"] == project_id
    assert tasks[0]["rationale"] == "Owner requested UI hardening"
    assert tasks[0]["risk"] == "READ"
    assert tasks[0]["task_spec"] == {
        "created_via": "wls-ui",
        "kind": "task",
        "ui_contract_version": 2,
    }


def test_projection_filters_archived_and_explicit_task_rows() -> None:
    runtime = FakeRuntime()
    c = runtime.db.connection
    c.execute(
        "INSERT INTO goals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "archived-project",
            "Archived",
            "",
            0.5,
            "[]",
            "fixture",
            0,
            None,
            None,
            "ACTIVE",
            0.0,
            "now",
            "now",
            None,
            None,
            "{}",
            "[]",
            "[]",
            "[]",
            "READ",
            None,
            None,
            0,
            0,
            None,
            "now",
            None,
        ),
    )
    c.execute(
        "INSERT INTO goals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "orphan-task",
            "Bad historical task",
            "",
            0.5,
            "[]",
            "fixture",
            0,
            None,
            None,
            "ACTIVE",
            0.0,
            "now",
            "now",
            None,
            None,
            '{"kind":"task"}',
            "[]",
            "[]",
            "[]",
            "READ",
            None,
            None,
            0,
            0,
            None,
            None,
            None,
        ),
    )

    assert UIProjection(runtime).projects() == []
    with pytest.raises(ValueError, match="explicitly tagged as task"):
        UIProjection(runtime).project_detail("orphan-task")


def test_owner_console_product_projection_summarizes_delivery_readiness() -> None:
    projection = OwnerConsoleProductProjection()
    empty = projection.project({})

    assert empty["delivery_readiness"]["overall_status"] == "NEEDS_EVIDENCE"
    assert empty["release_handoff"]["overall_status"] == "NEEDS_EVIDENCE"
    assert set(empty["delivery_readiness"]["missing_or_blocked"]) == {
        "operational_preflight",
        "installed_tail_check",
        "packaging_layout",
        "delivery_readiness",
    }
    assert set(empty["release_handoff"]["missing_or_blocked"]) == {
        "delivery_handoff",
        "release_state_audit",
    }

    ready = projection.project(
        {
            "operational_preflight_receipts": [
                {"status": "OPERATIONAL_PREFLIGHT_PASSED"}
            ],
            "installed_tail_check_receipts": [
                {"status": "INSTALLED_TAIL_CHECK_PASSED"}
            ],
            "packaging_layout_receipts": [{"status": "PACKAGING_LAYOUT_PASSED"}],
            "delivery_readiness_receipts": [{"status": "DELIVERY_READY_CANDIDATE"}],
            "delivery_handoff_receipts": [{"status": "DELIVERY_HANDOFF_READY"}],
            "release_state_audit_receipts": [
                {"status": "RELEASE_STATE_CANDIDATE_READY"}
            ],
        }
    )

    assert ready["delivery_readiness"]["overall_status"] == "CANDIDATE_READY"
    assert ready["release_handoff"]["overall_status"] == "CANDIDATE_READY"
    assert ready["delivery_readiness"]["missing_or_blocked"] == []
    assert ready["release_handoff"]["missing_or_blocked"] == []
    assert [item["pass_id"] for item in ready["delivery_readiness"]["items"]] == [
        "P77",
        "P76",
        "P75",
        "P74",
    ]
    assert [item["pass_id"] for item in ready["release_handoff"]["items"]] == [
        "P79",
        "P80",
    ]
    assert ready["delivery_readiness"]["writes_canonical_state"] is False
    assert ready["release_handoff"]["writes_canonical_state"] is False


def test_owner_console_product_projection_includes_garbage_review_panel() -> None:
    runtime = FakeRuntime()
    runtime.garbage_audit(reason="unit test garbage panel")
    projection = OwnerConsoleProductProjection()

    payload = projection.project(runtime.status())
    panels = {panel["panel_id"]: panel for panel in payload["panels"]}

    assert "garbage_review" in payload["panel_ids"]
    panel = panels["garbage_review"]
    assert panel["status"]["latest_status"] == "OWNER_REVIEW_REQUIRED"
    assert panel["status"]["latest_candidate_count"] == 1
    assert panel["status"]["cleanup_mode"] == "quarantine_first"


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


def test_task_parent_cannot_be_explicit_task_row() -> None:
    runtime = FakeRuntime()
    c = runtime.db.connection
    c.execute(
        "INSERT INTO goals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "bad-project",
            "Bad project",
            "",
            0.5,
            "[]",
            "fixture",
            0,
            None,
            None,
            "ACTIVE",
            0.0,
            "now",
            "now",
            None,
            None,
            '{"kind":"task"}',
            "[]",
            "[]",
            "[]",
            "READ",
            None,
            None,
            0,
            0,
            None,
            None,
            None,
        ),
    )

    with pytest.raises(ValueError, match="explicitly tagged as task"):
        UIProjection(runtime).create_goal(
            {"kind": "task", "project_id": "bad-project", "title": "Nested"}
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
        """
        INSERT INTO evidence(
            seq,evidence_id,event_type,payload_json,created_at,record_hash,
            source_type,producer,branch,commit_sha
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "1",
            "ev-1",
            "runtime_initialized",
            '{"ok": true}',
            "now",
            "h",
            "runtime",
            "pytest",
            "branch",
            "commit",
        ),
    )
    c.execute(
        "INSERT INTO skills VALUES (?,?,?,?,?,?,?,?,?)",
        ("sk-1", "read", 1, "PROPOSED", 0.0, 0, "{}", "now", "now"),
    )

    library = UIProjection(runtime).library()

    assert library["evidence"][0]["event_type"] == "runtime_initialized"
    assert library["evidence"][0]["payload"]["ok"] is True
    assert library["skills"][0]["name"] == "read"


def test_library_accepts_legacy_evidence_schema_without_provenance_columns() -> None:
    runtime = FakeRuntime(legacy_evidence=True)
    c = runtime.db.connection
    c.execute(
        "INSERT INTO evidence(seq,evidence_id,event_type,payload_json,created_at,record_hash) VALUES (?,?,?,?,?,?)",
        ("1", "ev-legacy", "runtime_initialized", '{"ok": true}', "now", "h"),
    )

    evidence = UIProjection(runtime).library()["evidence"][0]

    assert evidence["evidence_id"] == "ev-legacy"
    assert evidence["source_type"] is None
    assert evidence["producer"] is None
    assert evidence["branch"] is None
    assert evidence["commit_sha"] is None


def test_bootstrap_accepts_fresh_canonical_goal_schema_without_archive_column() -> None:
    runtime = FakeRuntime(archived_goal_column=False)

    bootstrap = UIProjection(runtime).bootstrap()

    assert bootstrap["projects"] == []
    assert bootstrap["inbox"]["items"] == []
    assert bootstrap["home"]["integrity_hint"]["projection_only"] is True
