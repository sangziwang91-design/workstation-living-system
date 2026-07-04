from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
import json

from .schemas import Goal, RiskLevel, digest_json, utc_now


UNKNOWN_ACTION_RESOLUTIONS = {
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "RETRY_SAFE",
}


def _decode_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    try:
        return record[key]
    except (KeyError, TypeError, IndexError):
        return getattr(record, key, default)


@dataclass(slots=True)
class UIProjection:
    """Loopback UI adapter over canonical WLS state.

    This class owns no durable state. Reads come from canonical tables and writes
    call existing LivingSystem authorities.
    """

    runtime: Any

    @property
    def db(self) -> Any:
        return self.runtime.db

    def bootstrap(self) -> dict[str, Any]:
        return {
            "generated_at": utc_now(),
            "home": self.home(),
            "projects": self.projects(),
            "inbox": self.inbox(),
            "runs": self.runs(limit=30),
        }

    def home(self) -> dict[str, Any]:
        status = self.runtime.status()
        return {
            "generated_at": utc_now(),
            "system": {
                "version": status.get("version"),
                "home": status.get("home"),
                "read_only": bool(status.get("read_only", False)),
                "paused": bool(status.get("paused", False)),
                "killed": bool(status.get("killed", False)),
                "planner_provider": status.get("planner_provider"),
            },
            "counts": {
                "goals": self._status_counts("goals"),
                "cycles": self._status_counts("cycles"),
                "actions": self._status_counts("actions"),
                "events": status.get("event_counts", {}),
            },
            "focus": status.get("next_focus", []),
            "latest_cycle": status.get("latest_cycle"),
            "pending_action_count": len(status.get("pending_actions", [])),
            "active_goal_count": len(status.get("active_goals", [])),
            "integrity_hint": {
                "authority": "canonical-runtime",
                "projection_only": True,
                "state_owner": "LivingSystem",
            },
        }

    def projects(self) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT g.*,
                   (SELECT COUNT(*) FROM goals child
                    WHERE child.parent_goal_id=g.goal_id) AS task_count,
                   (SELECT COUNT(*) FROM goals child
                    WHERE child.parent_goal_id=g.goal_id
                      AND child.status IN ('ACTIVE','BLOCKED')
                   ) AS active_task_count
            FROM goals g
            WHERE g.parent_goal_id IS NULL
            ORDER BY g.priority DESC, g.updated_at DESC
            """
        )
        return [self._goal_view(row, kind="project") for row in rows]

    def tasks(
        self,
        *,
        project_id: str | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        if project_id:
            where = "parent_goal_id=?"
            parameters.append(project_id)
        else:
            where = "parent_goal_id IS NOT NULL"
        parameters.append(max(1, min(1000, int(limit))))
        rows = self.db.query_all(
            f"""
            SELECT * FROM goals
            WHERE {where}
            ORDER BY priority DESC, updated_at DESC
            LIMIT ?
            """,  # nosec B608 - where is selected from fixed internal strings
            tuple(parameters),
        )
        return [self._goal_view(row, kind="task") for row in rows]

    def project_detail(self, project_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM goals WHERE goal_id=?", (project_id,))
        if row is None:
            raise KeyError(f"project not found: {project_id}")
        if _value(row, "parent_goal_id") is not None:
            raise ValueError(f"goal is not a top-level project: {project_id}")
        project = self._goal_view(row, kind="project")
        project["tasks"] = self.tasks(project_id=project_id)
        project["recent_runs"] = self.runs(
            goal_ids=self._goal_tree_ids(project_id),
            limit=20,
        )
        return project

    def runs(
        self,
        *,
        goal_ids: Iterable[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_goal_ids = list(dict.fromkeys(str(value) for value in goal_ids or []))
        parameters: list[Any] = []
        goal_filter = ""
        if normalized_goal_ids:
            placeholders = ",".join("?" for _ in normalized_goal_ids)
            goal_filter = f"""
                WHERE EXISTS (
                    SELECT 1
                    FROM plans p2
                    JOIN actions a2 ON a2.plan_id=p2.plan_id
                    WHERE p2.cycle_id=c.cycle_id
                      AND a2.goal_id IN ({placeholders})
                )
            """  # nosec B608 - placeholders are generated
            parameters.extend(normalized_goal_ids)
        parameters.append(max(1, min(500, int(limit))))
        rows = self.db.query_all(
            f"""
            SELECT c.cycle_id,c.started_at,c.finished_at,c.status,c.error,
                   COUNT(DISTINCT p.plan_id) AS plan_count,
                   COUNT(a.action_id) AS action_count,
                   SUM(CASE WHEN a.status='SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded_actions,
                   SUM(CASE WHEN a.status='FAILED' THEN 1 ELSE 0 END) AS failed_actions,
                   SUM(CASE WHEN a.status='WAITING_APPROVAL' THEN 1 ELSE 0 END) AS waiting_actions,
                   SUM(CASE WHEN a.status='APPROVED' THEN 1 ELSE 0 END) AS approved_actions,
                   SUM(CASE WHEN a.status='UNKNOWN_SIDE_EFFECT' THEN 1 ELSE 0 END) AS unknown_actions
            FROM cycles c
            LEFT JOIN plans p ON p.cycle_id=c.cycle_id
            LEFT JOIN actions a ON a.plan_id=p.plan_id
            {goal_filter}
            GROUP BY c.cycle_id
            ORDER BY c.started_at DESC
            LIMIT ?
            """,  # nosec B608 - goal_filter contains generated placeholders only
            tuple(parameters),
        )
        return [
            {
                "run_id": row["cycle_id"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "status": row["status"],
                "error": row["error"],
                "plan_count": int(row["plan_count"] or 0),
                "action_count": int(row["action_count"] or 0),
                "succeeded_actions": int(row["succeeded_actions"] or 0),
                "failed_actions": int(row["failed_actions"] or 0),
                "waiting_actions": int(row["waiting_actions"] or 0),
                "approved_actions": int(row["approved_actions"] or 0),
                "unknown_actions": int(row["unknown_actions"] or 0),
            }
            for row in rows
        ]

    def run_detail(self, run_id: str) -> dict[str, Any]:
        cycle = self.db.query_one("SELECT * FROM cycles WHERE cycle_id=?", (run_id,))
        if cycle is None:
            raise KeyError(f"run not found: {run_id}")
        plans = self.db.query_all(
            "SELECT * FROM plans WHERE cycle_id=? ORDER BY created_at ASC",
            (run_id,),
        )
        actions = self.db.query_all(
            """
            SELECT a.*,p.cycle_id
            FROM actions a
            JOIN plans p ON p.plan_id=a.plan_id
            WHERE p.cycle_id=?
            ORDER BY COALESCE(a.started_at,''),a.rowid
            """,
            (run_id,),
        )
        return {
            "run_id": run_id,
            "cycle": self._cycle_view(cycle),
            "plans": [self._plan_view(row) for row in plans],
            "actions": [self._action_view(row) for row in actions],
        }

    def inbox(self) -> dict[str, Any]:
        action_rows = self.db.query_all(
            """
            SELECT action_id,plan_id,goal_id,tool,purpose,expected_result,risk,
                   status,approval_id,started_at,finished_at,error
            FROM actions
            WHERE status IN ('WAITING_APPROVAL','APPROVED','UNKNOWN_SIDE_EFFECT')
            ORDER BY rowid DESC
            """
        )
        goal_rows = self.db.query_all(
            """
            SELECT * FROM goals
            WHERE status='BLOCKED'
            ORDER BY priority DESC,updated_at ASC
            """
        )
        items: list[dict[str, Any]] = []
        for row in action_rows:
            item = _row_dict(row)
            status = str(item["status"])
            item.update(
                {
                    "item_id": item["action_id"],
                    "item_type": "action",
                    "severity": "critical"
                    if status == "UNKNOWN_SIDE_EFFECT"
                    else "attention",
                    "allowed_operations": self._action_operations(status),
                }
            )
            items.append(item)
        for row in goal_rows:
            view = self._goal_view(row, kind="task")
            view.update(
                {
                    "item_id": view["goal_id"],
                    "item_type": "blocked_goal",
                    "severity": "attention",
                    "allowed_operations": [],
                }
            )
            items.append(view)
        return {"generated_at": utc_now(), "count": len(items), "items": items}

    def library(self, limit: int = 100) -> dict[str, Any]:
        safe_limit = max(1, min(500, int(limit)))
        evidence_rows = self.db.query_all(
            """
            SELECT seq,evidence_id,event_type,payload_json,created_at,record_hash
            FROM evidence
            ORDER BY seq DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        skill_rows = self.db.query_all(
            """
            SELECT skill_id,name,version,status,success_rate,use_count,definition_json,
                   created_at,updated_at
            FROM skills
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        return {
            "generated_at": utc_now(),
            "evidence": [
                {
                    **{
                        key: value
                        for key, value in _row_dict(row).items()
                        if key != "payload_json"
                    },
                    "payload": _decode_json(_value(row, "payload_json"), {}),
                }
                for row in evidence_rows
            ],
            "skills": [
                {
                    **{
                        key: value
                        for key, value in _row_dict(row).items()
                        if key != "definition_json"
                    },
                    "definition": _decode_json(_value(row, "definition_json"), {}),
                }
                for row in skill_rows
            ],
        }

    def create_goal(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("title is required")
        kind = str(payload.get("kind", "task")).strip().lower()
        if kind not in {"project", "task"}:
            raise ValueError("kind must be project or task")
        parent_goal_id = payload.get("project_id")
        if kind == "project":
            parent_goal_id = None
        else:
            if not parent_goal_id:
                raise ValueError("project_id is required for a task")
            parent = self.runtime.goals.get(str(parent_goal_id))
            if parent is None:
                raise ValueError("project_id does not identify an existing goal")
            if _value(parent, "parent_goal_id") is not None:
                raise ValueError("project_id must identify a top-level project")
        criteria = payload.get("success_criteria", [])
        if not isinstance(criteria, list):
            raise ValueError("success_criteria must be a list")
        priority = float(payload.get("priority", 0.5))
        if not 0.0 <= priority <= 1.0:
            raise ValueError("priority must be within [0, 1]")
        RiskLevel(str(payload.get("risk", RiskLevel.READ.value)))
        goal = Goal(
            title=title,
            description=str(payload.get("description", "")),
            priority=priority,
            success_criteria=[str(item) for item in criteria if str(item).strip()],
            source="wls-ui",
            autonomous=False,
            parent_goal_id=str(parent_goal_id) if parent_goal_id else None,
        )
        goal_id = self.runtime.add_goal(goal)
        return {"goal_id": goal_id, "kind": kind, "created_at": goal.created_at}

    def run_cycle(self) -> dict[str, Any]:
        return self.runtime.run_cycle()

    def decide_action(
        self,
        action_id: str,
        *,
        approved: bool,
        reason: str = "",
        minutes: int = 30,
    ) -> dict[str, Any]:
        self._require_action_status(action_id, {"WAITING_APPROVAL"})
        approval_id = self.runtime.approvals.issue(
            action_id,
            approved,
            max(1, min(1440, int(minutes))),
            reason or "owner decision from loopback WLS UI",
        )
        return {"action_id": action_id, "approval_id": approval_id, "approved": approved}

    def resume_action(self, action_id: str) -> dict[str, Any]:
        self._require_action_status(action_id, {"APPROVED"})
        return self.runtime.resume_action(action_id)

    def resolve_unknown_action(
        self,
        action_id: str,
        *,
        resolution: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = str(resolution).upper()
        if normalized not in UNKNOWN_ACTION_RESOLUTIONS:
            raise ValueError("invalid unknown-side-effect resolution")
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("non-empty evidence object is required")
        self._require_action_status(action_id, {"UNKNOWN_SIDE_EFFECT"})
        self.runtime.resolve_unknown_action(action_id, normalized, evidence)
        return {"action_id": action_id, "resolution": normalized, "resolved": True}

    def _require_action_status(self, action_id: str, allowed: set[str]) -> Any:
        row = self.db.query_one(
            "SELECT action_id,status FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(f"action not found: {action_id}")
        status = str(row["status"])
        if status not in allowed:
            expected = ", ".join(sorted(allowed))
            raise ValueError(f"action status is {status}; expected one of: {expected}")
        return row

    def _goal_tree_ids(self, root_goal_id: str) -> list[str]:
        rows = self.db.query_all(
            """
            WITH RECURSIVE goal_tree(goal_id) AS (
                SELECT goal_id FROM goals WHERE goal_id=?
                UNION
                SELECT child.goal_id
                FROM goals child
                JOIN goal_tree parent ON child.parent_goal_id=parent.goal_id
            )
            SELECT goal_id FROM goal_tree
            """,
            (root_goal_id,),
        )
        return [str(row["goal_id"]) for row in rows]

    def _status_counts(self, table: str) -> dict[str, int]:
        if table not in {"goals", "cycles", "actions"}:
            raise ValueError("unsupported status table")
        rows = self.db.query_all(
            f"SELECT status,COUNT(*) AS n FROM {table} GROUP BY status"  # nosec B608
        )
        return {str(row["status"]): int(row["n"]) for row in rows}

    @staticmethod
    def _action_operations(status: str) -> list[str]:
        if status == "WAITING_APPROVAL":
            return ["approve", "reject"]
        if status == "APPROVED":
            return ["resume"]
        if status == "UNKNOWN_SIDE_EFFECT":
            return ["resolve"]
        return []

    @staticmethod
    def _goal_view(row: Any, *, kind: str) -> dict[str, Any]:
        data = _row_dict(row)
        data["success_criteria"] = _decode_json(
            data.pop("success_criteria_json", "[]"), []
        )
        data["kind"] = kind
        if "task_count" in data:
            data["task_count"] = int(data["task_count"] or 0)
        if "active_task_count" in data:
            data["active_task_count"] = int(data["active_task_count"] or 0)
        return data

    @staticmethod
    def _cycle_view(row: Any) -> dict[str, Any]:
        data = _row_dict(row)
        data["workspace"] = _decode_json(data.pop("workspace_json", None), None)
        data["metrics"] = _decode_json(data.pop("metrics_json", None), None)
        return data

    @staticmethod
    def _plan_view(row: Any) -> dict[str, Any]:
        data = _row_dict(row)
        data["plan"] = _decode_json(data.pop("plan_json", "{}"), {})
        return data

    @staticmethod
    def _action_view(row: Any) -> dict[str, Any]:
        data = _row_dict(row)
        data["arguments"] = _decode_json(data.pop("arguments_json", "{}"), {})
        data["acceptance"] = _decode_json(data.pop("acceptance_json", "[]"), [])
        data["result"] = _decode_json(data.pop("result_json", None), None)
        data["allowed_operations"] = UIProjection._action_operations(
            str(data["status"])
        )
        return data


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


class OwnerConsoleProductProjection:
    """Productized read-only console panels over the canonical runtime status."""

    PANEL_IDS = (
        "life",
        "attention",
        "provider_routes",
        "goals",
        "actions_approval",
        "approval_channels",
        "scheduled_events",
        "external_handoffs",
        "voice_ingress",
        "notification_drafts",
        "screen_snapshots",
        "browser_form_drafts",
        "download_quarantine",
        "document_ingress",
        "task_previews",
        "execution_preflight",
        "execution_receipts",
        "result_projections",
        "projection_reviews",
        "memory_world",
        "evolution_lab",
        "skill_candidates",
        "skill_sandbox",
        "sandbox_adapters",
        "offspring",
        "agentic_tasks",
        "learning_epoch",
        "capability_epoch",
        "organs",
    )

    def project(self, status: dict[str, Any]) -> dict[str, Any]:
        panels = [
            self._life_panel(status),
            self._attention_panel(status),
            self._provider_routes_panel(status),
            self._goals_panel(status),
            self._actions_panel(status),
            self._approval_channels_panel(status),
            self._scheduled_events_panel(status),
            self._external_handoffs_panel(status),
            self._voice_ingress_panel(status),
            self._notification_drafts_panel(status),
            self._screen_snapshots_panel(status),
            self._browser_form_drafts_panel(status),
            self._download_quarantine_panel(status),
            self._document_ingress_panel(status),
            self._task_previews_panel(status),
            self._execution_preflight_panel(status),
            self._execution_receipts_panel(status),
            self._result_projections_panel(status),
            self._projection_reviews_panel(status),
            self._memory_world_panel(status),
            self._evolution_panel(status),
            self._skill_candidates_panel(status),
            self._skill_sandbox_panel(status),
            self._sandbox_adapters_panel(status),
            self._offspring_panel(status),
            self._agentic_tasks_panel(status),
            self._learning_epoch_panel(status),
            self._capability_epoch_panel(status),
            self._organs_panel(status),
        ]
        payload: dict[str, Any] = {
            "surface": "owner_console",
            "projection_version": "0.2.0",
            "generated_at": utc_now(),
            "mode": "READ_ONLY_PROJECTION",
            "canonical_owner": "evidence",
            "writes_canonical_state": False,
            "direct_tool_execution": False,
            "panels": panels,
            "panel_ids": [panel["panel_id"] for panel in panels],
            "source_keys": sorted(status.keys()),
            "claim_ceiling": "read-only product projection over LivingSystem.status",
        }
        payload["projection_digest"] = digest_json(
            {
                "mode": payload["mode"],
                "panel_ids": payload["panel_ids"],
                "panels": panels,
            }
        )
        return payload

    @staticmethod
    def _life_panel(status: dict[str, Any]) -> dict[str, Any]:
        return {
            "panel_id": "life",
            "title": "Life",
            "status": {
                "version": status.get("version"),
                "home": status.get("home"),
                "read_only": status.get("read_only"),
                "paused": status.get("paused"),
                "killed": status.get("killed"),
                "cycle_count": status.get("cycle_count"),
                "event_counts": status.get("event_counts", {}),
            },
        }

    @staticmethod
    def _attention_panel(status: dict[str, Any]) -> dict[str, Any]:
        planner_route = status.get("planner_route", {})
        return {
            "panel_id": "attention",
            "title": "Attention",
            "status": {
                "next_focus": status.get("next_focus", []),
                "planner_provider": status.get("planner_provider"),
                "planner_route": {
                    "provider_id": planner_route.get("provider_id")
                    if isinstance(planner_route, dict)
                    else None,
                    "claim_ceiling": "route summary only",
                },
            },
        }

    @staticmethod
    def _provider_routes_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("provider_route_receipts", [])
        return {
            "panel_id": "provider_routes",
            "title": "Provider Routes",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "planner_authority": "Planner",
                "direct_model_call": False,
                "direct_tool_execution": False,
            },
        }

    @staticmethod
    def _goals_panel(status: dict[str, Any]) -> dict[str, Any]:
        goals = status.get("active_goals", [])
        return {
            "panel_id": "goals",
            "title": "Goals",
            "status": {
                "active_count": len(goals) if isinstance(goals, list) else 0,
                "items": goals if isinstance(goals, list) else [],
            },
        }

    @staticmethod
    def _actions_panel(status: dict[str, Any]) -> dict[str, Any]:
        actions = status.get("pending_actions", [])
        return {
            "panel_id": "actions_approval",
            "title": "Actions And Approval",
            "status": {
                "pending_count": len(actions) if isinstance(actions, list) else 0,
                "items": actions if isinstance(actions, list) else [],
                "approval_required_for_execution": True,
            },
        }

    @staticmethod
    def _approval_channels_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("approval_channel_receipts", [])
        return {
            "panel_id": "approval_channels",
            "title": "Approval Channels",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "approval_authority": "ApprovalManager",
                "channel_executes_actions": False,
                "channel_issues_approvals": False,
            },
        }

    @staticmethod
    def _scheduled_events_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("scheduled_event_receipts", [])
        return {
            "panel_id": "scheduled_events",
            "title": "Scheduled Events",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "creates_goal": False,
                "creates_action": False,
                "standing_goal_authority": "Owner and GoalStore only",
            },
        }

    @staticmethod
    def _external_handoffs_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("external_handoff_receipts", [])
        return {
            "panel_id": "external_handoffs",
            "title": "External Handoffs",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "candidate_only": True,
                "authority_transfer_allowed": False,
                "creates_action": False,
                "creates_goal": False,
            },
        }

    @staticmethod
    def _voice_ingress_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("voice_transcript_receipts", [])
        return {
            "panel_id": "voice_ingress",
            "title": "Voice Ingress",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "creates_goal": False,
                "creates_action": False,
                "audio_captured": False,
                "stt_executed": False,
                "allowed_next_authority": "EventStore",
            },
        }

    @staticmethod
    def _notification_drafts_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("notification_draft_receipts", [])
        return {
            "panel_id": "notification_drafts",
            "title": "Notification Drafts",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "delivery_executed": False,
                "tts_executed": False,
                "audio_played": False,
                "external_send_executed": False,
            },
        }

    @staticmethod
    def _screen_snapshots_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("screen_snapshot_receipts", [])
        return {
            "panel_id": "screen_snapshots",
            "title": "Screen Snapshots",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "ocr_executed": False,
                "ui_control_executed": False,
                "external_upload_executed": False,
                "allowed_next_authority": "EventStore",
            },
        }

    @staticmethod
    def _browser_form_drafts_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("browser_form_draft_receipts", [])
        return {
            "panel_id": "browser_form_drafts",
            "title": "Browser Form Drafts",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "browser_opened": False,
                "form_submitted": False,
                "network_post_executed": False,
                "approval_required_for_submission": True,
            },
        }

    @staticmethod
    def _download_quarantine_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("download_quarantine_receipts", [])
        return {
            "panel_id": "download_quarantine",
            "title": "Download Quarantine",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "network_fetch_executed": False,
                "file_materialized": False,
                "external_write_executed": False,
                "approval_required_for_fetch": True,
            },
        }

    @staticmethod
    def _document_ingress_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("document_ingress_receipts", [])
        return {
            "panel_id": "document_ingress",
            "title": "Document Ingress",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "text_extracted": False,
                "ocr_executed": False,
                "vector_indexed": False,
                "external_upload_executed": False,
            },
        }

    @staticmethod
    def _task_previews_panel(status: dict[str, Any]) -> dict[str, Any]:
        previews = status.get("read_only_plan_previews", [])
        return {
            "panel_id": "task_previews",
            "title": "Task Previews",
            "status": {
                "preview_count": len(previews) if isinstance(previews, list) else 0,
                "items": previews if isinstance(previews, list) else [],
                "planner_admission_required": True,
                "direct_execution_allowed": False,
            },
        }

    @staticmethod
    def _execution_preflight_panel(status: dict[str, Any]) -> dict[str, Any]:
        preflights = status.get("read_only_execution_preflights", [])
        return {
            "panel_id": "execution_preflight",
            "title": "Execution Preflight",
            "status": {
                "preflight_count": len(preflights)
                if isinstance(preflights, list)
                else 0,
                "items": preflights if isinstance(preflights, list) else [],
                "direct_execution_allowed": False,
                "approval_authority": "PolicyEngine and ApprovalManager",
            },
        }

    @staticmethod
    def _execution_receipts_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("read_only_execution_receipts", [])
        return {
            "panel_id": "execution_receipts",
            "title": "Execution Receipts",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "writes_canonical_state": False,
                "receipt_source": "EvidenceLedger and action results",
            },
        }

    @staticmethod
    def _result_projections_panel(status: dict[str, Any]) -> dict[str, Any]:
        projections = status.get("read_only_result_projections", [])
        return {
            "panel_id": "result_projections",
            "title": "Result Projections",
            "status": {
                "projection_count": len(projections)
                if isinstance(projections, list)
                else 0,
                "items": projections if isinstance(projections, list) else [],
                "candidate_only": True,
                "promotion_authority": "MemoryStore and WorldModel with evidence",
            },
        }

    @staticmethod
    def _projection_reviews_panel(status: dict[str, Any]) -> dict[str, Any]:
        reviews = status.get("read_only_projection_reviews", [])
        return {
            "panel_id": "projection_reviews",
            "title": "Projection Reviews",
            "status": {
                "review_count": len(reviews) if isinstance(reviews, list) else 0,
                "items": reviews if isinstance(reviews, list) else [],
                "candidate_only": True,
                "rollback_supported": True,
            },
        }

    @staticmethod
    def _memory_world_panel(status: dict[str, Any]) -> dict[str, Any]:
        return {
            "panel_id": "memory_world",
            "title": "Memory And World",
            "status": {
                "causal_memory": status.get("causal_memory", {}),
                "temporal_world": status.get("temporal_world", {}),
                "cognition": status.get("cognition", {}),
                "claim_ceiling": "summary projection only",
            },
        }

    @staticmethod
    def _evolution_panel(status: dict[str, Any]) -> dict[str, Any]:
        return {
            "panel_id": "evolution_lab",
            "title": "Evolution Lab",
            "status": {
                "growth_cycles": status.get("growth_cycles", []),
                "active_skills": status.get("active_skills", []),
                "skill_promotion_executed": False,
            },
        }

    @staticmethod
    def _skill_candidates_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("skill_candidate_receipts", [])
        return {
            "panel_id": "skill_candidates",
            "title": "Skill Candidates",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "candidate_only": True,
                "promotion_executed": False,
                "promotion_authority": "SkillLibrary with explicit approval",
            },
        }

    @staticmethod
    def _skill_sandbox_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("skill_sandbox_receipts", [])
        return {
            "panel_id": "skill_sandbox",
            "title": "Skill Sandbox",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "sandbox_started": bool(receipts) if isinstance(receipts, list) else False,
                "validation_passed": False,
                "approval_executed": False,
                "promotion_executed": False,
                "deployment_executed": False,
            },
        }

    @staticmethod
    def _sandbox_adapters_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("sandbox_adapter_receipts", [])
        return {
            "panel_id": "sandbox_adapters",
            "title": "Sandbox Adapters",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "local_fixture_only": True,
                "remote_execution": False,
                "secret_access": False,
                "network_access": False,
                "canonical_authority": "LivingSystem",
            },
        }

    @staticmethod
    def _offspring_panel(status: dict[str, Any]) -> dict[str, Any]:
        birth_receipts = status.get("offspring_birth_receipts", [])
        state_receipts = status.get("offspring_state_receipts", [])
        retirement_receipts = status.get("offspring_retirement_receipts", [])
        retirement_cleanup_receipts = status.get(
            "offspring_retirement_cleanup_receipts", []
        )
        budget_receipts = status.get("offspring_budget_receipts", [])
        checkpoint_receipts = status.get("offspring_checkpoint_receipts", [])
        mailbox_receipts = status.get("offspring_mailbox_receipts", [])
        return {
            "panel_id": "offspring",
            "title": "Offspring",
            "status": {
                "receipt_count": len(birth_receipts)
                if isinstance(birth_receipts, list)
                else 0,
                "items": birth_receipts if isinstance(birth_receipts, list) else [],
                "isolated_state": {
                    "receipt_count": len(state_receipts)
                    if isinstance(state_receipts, list)
                    else 0,
                    "items": state_receipts
                    if isinstance(state_receipts, list)
                    else [],
                },
                "retirement": {
                    "receipt_count": len(retirement_receipts)
                    if isinstance(retirement_receipts, list)
                    else 0,
                    "items": retirement_receipts
                    if isinstance(retirement_receipts, list)
                    else [],
                },
                "retirement_cleanup": {
                    "receipt_count": len(retirement_cleanup_receipts)
                    if isinstance(retirement_cleanup_receipts, list)
                    else 0,
                    "items": retirement_cleanup_receipts
                    if isinstance(retirement_cleanup_receipts, list)
                    else [],
                    "evidence_retained": True,
                    "task_assignment_allowed": False,
                    "resource_cleanup_verified": True,
                },
                "budget": {
                    "receipt_count": len(budget_receipts)
                    if isinstance(budget_receipts, list)
                    else 0,
                    "items": budget_receipts if isinstance(budget_receipts, list) else [],
                    "provider_call_executed": False,
                    "tool_call_executed": False,
                    "aggregate_account": True,
                },
                "checkpoint": {
                    "receipt_count": len(checkpoint_receipts)
                    if isinstance(checkpoint_receipts, list)
                    else 0,
                    "items": checkpoint_receipts
                    if isinstance(checkpoint_receipts, list)
                    else [],
                    "resume_allowed": False,
                    "lease_replay_allowed": False,
                },
                "mailbox": {
                    "receipt_count": len(mailbox_receipts)
                    if isinstance(mailbox_receipts, list)
                    else 0,
                    "items": mailbox_receipts
                    if isinstance(mailbox_receipts, list)
                    else [],
                    "schema_version": "offspring-mailbox-v1",
                    "unknown_schema_quarantine": True,
                    "candidate_only": True,
                    "completion_authority_transferred": False,
                },
                "birth_contract_only": not bool(state_receipts)
                if isinstance(state_receipts, list)
                else True,
                "candidate_only": True,
                "child_runtime_started": False,
                "parent_write_allowed": False,
                "second_authority_created": False,
                "absorption_allowed": False,
                "canonical_authority": "LivingSystem",
                "child_authority": "candidate_only",
                "merge_allowed": False,
                "deployment_allowed": False,
                "skill_promotion_allowed": False,
            },
        }

    @staticmethod
    def _agentic_tasks_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("agentic_task_receipts", [])
        manifests = status.get("agentic_context_manifest_receipts", [])
        workers = status.get("agentic_worker_profile_receipts", [])
        worker_lifecycle = status.get("agentic_worker_lifecycle_receipts", [])
        bindings = status.get("agentic_node_action_receipts", [])
        failures = status.get("agentic_failure_attribution_receipts", [])
        acceptance_traces = status.get("agentic_acceptance_trace_receipts", [])
        mailbox = status.get("agentic_mailbox_receipts", [])
        repair_candidates = status.get("agentic_repair_candidate_receipts", [])
        budgets = status.get("agentic_budget_receipts", [])
        checkpoint_resume = status.get("agentic_checkpoint_resume_receipts", [])
        worker_lease_recovery = status.get(
            "agentic_worker_lease_recovery_receipts", []
        )
        worker_arbitration = status.get("agentic_worker_arbitration_receipts", [])
        worker_trust = status.get("agentic_worker_trust_receipts", [])
        retry_gates = status.get("agentic_retry_gate_receipts", [])
        replan_candidates = status.get("agentic_replan_candidate_receipts", [])
        scorecards = status.get("agentic_benchmark_scorecard_receipts", [])
        audits = status.get("agentic_harness_epoch_audit_receipts", [])
        return {
            "panel_id": "agentic_tasks",
            "title": "Agentic Tasks",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "canonical_runtime": "LivingSystem",
                "second_authority_created": False,
                "direct_worker_execution": False,
                "context_manifest_v1": {
                    "receipt_count": len(manifests) if isinstance(manifests, list) else 0,
                    "items": manifests if isinstance(manifests, list) else [],
                },
                "worker_registry_v1": {
                    "receipt_count": len(workers) if isinstance(workers, list) else 0,
                    "items": workers if isinstance(workers, list) else [],
                },
                "worker_lifecycle_v1": {
                    "receipt_count": len(worker_lifecycle)
                    if isinstance(worker_lifecycle, list)
                    else 0,
                    "items": worker_lifecycle
                    if isinstance(worker_lifecycle, list)
                    else [],
                    "external_authority": False,
                    "worker_execution": False,
                },
                "policy_bound_actions": {
                    "receipt_count": len(bindings) if isinstance(bindings, list) else 0,
                    "items": bindings if isinstance(bindings, list) else [],
                },
                "failure_attribution_v1": {
                    "receipt_count": len(failures) if isinstance(failures, list) else 0,
                    "items": failures if isinstance(failures, list) else [],
                },
                "acceptance_trace_v1": {
                    "receipt_count": len(acceptance_traces)
                    if isinstance(acceptance_traces, list)
                    else 0,
                    "items": acceptance_traces
                    if isinstance(acceptance_traces, list)
                    else [],
                },
                "file_mailbox_v1": {
                    "receipt_count": len(mailbox) if isinstance(mailbox, list) else 0,
                    "items": mailbox if isinstance(mailbox, list) else [],
                },
                "repair_candidates_v1": {
                    "receipt_count": len(repair_candidates)
                    if isinstance(repair_candidates, list)
                    else 0,
                    "items": repair_candidates
                    if isinstance(repair_candidates, list)
                    else [],
                    "candidate_only": True,
                    "direct_execution": False,
                },
                "budget_gate_v1": {
                    "receipt_count": len(budgets) if isinstance(budgets, list) else 0,
                    "items": budgets if isinstance(budgets, list) else [],
                    "provider_calls": False,
                    "tool_execution": False,
                },
                "checkpoint_resume_v1": {
                    "receipt_count": len(checkpoint_resume)
                    if isinstance(checkpoint_resume, list)
                    else 0,
                    "items": checkpoint_resume
                    if isinstance(checkpoint_resume, list)
                    else [],
                    "retry_execution": False,
                    "worker_result_inferred": False,
                },
                "worker_lease_recovery_v1": {
                    "receipt_count": len(worker_lease_recovery)
                    if isinstance(worker_lease_recovery, list)
                    else 0,
                    "items": worker_lease_recovery
                    if isinstance(worker_lease_recovery, list)
                    else [],
                    "retry_execution": False,
                    "worker_execution": False,
                },
                "worker_arbitration_v1": {
                    "receipt_count": len(worker_arbitration)
                    if isinstance(worker_arbitration, list)
                    else 0,
                    "items": worker_arbitration
                    if isinstance(worker_arbitration, list)
                    else [],
                    "lease_created": False,
                    "worker_execution": False,
                    "second_authority_created": False,
                },
                "worker_trust_v1": {
                    "receipt_count": len(worker_trust)
                    if isinstance(worker_trust, list)
                    else 0,
                    "items": worker_trust if isinstance(worker_trust, list) else [],
                    "self_report_used": False,
                    "worker_execution": False,
                    "promotion_allowed": False,
                },
                "retry_gate_v1": {
                    "receipt_count": len(retry_gates)
                    if isinstance(retry_gates, list)
                    else 0,
                    "items": retry_gates if isinstance(retry_gates, list) else [],
                    "retry_execution": False,
                    "repair_success_inferred": False,
                },
                "replan_candidates_v1": {
                    "receipt_count": len(replan_candidates)
                    if isinstance(replan_candidates, list)
                    else 0,
                    "items": replan_candidates
                    if isinstance(replan_candidates, list)
                    else [],
                    "graph_mutation": False,
                    "task_completion_inferred": False,
                },
                "benchmark_scorecard_v1": {
                    "receipt_count": len(scorecards)
                    if isinstance(scorecards, list)
                    else 0,
                    "items": scorecards if isinstance(scorecards, list) else [],
                    "receipt_only": True,
                    "external_benchmark_executed": False,
                },
                "harness_epoch_audit": {
                    "receipt_count": len(audits) if isinstance(audits, list) else 0,
                    "items": audits if isinstance(audits, list) else [],
                },
            },
        }

    @staticmethod
    def _learning_epoch_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("learning_epoch_receipts", [])
        return {
            "panel_id": "learning_epoch",
            "title": "Learning Epoch",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "default_mode": "learning_frozen",
                "allowed_open_mode": "candidate_only",
                "promotion_executed": False,
            },
        }

    @staticmethod
    def _capability_epoch_panel(status: dict[str, Any]) -> dict[str, Any]:
        receipts = status.get("capability_epoch_audit_receipts", [])
        convergence = status.get("single_software_convergence_receipts", [])
        return {
            "panel_id": "capability_epoch",
            "title": "Capability Epoch",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
                "single_software_convergence": {
                    "receipt_count": len(convergence)
                    if isinstance(convergence, list)
                    else 0,
                    "items": convergence if isinstance(convergence, list) else [],
                },
                "allowed_conclusion": "FUNCTIONAL_RUNTIME_ONLY",
                "live_deployment_executed": False,
            },
        }

    @staticmethod
    def _organs_panel(status: dict[str, Any]) -> dict[str, Any]:
        return {
            "panel_id": "organs",
            "title": "Organs",
            "status": {
                "capabilities": status.get("capabilities", {}),
                "authority_model": "many organs, one canonical WLS subject",
            },
        }
