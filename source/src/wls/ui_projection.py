from __future__ import annotations

from typing import Any

from .schemas import digest_json, utc_now


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
        "goals",
        "actions_approval",
        "approval_channels",
        "scheduled_events",
        "external_handoffs",
        "task_previews",
        "execution_preflight",
        "execution_receipts",
        "result_projections",
        "projection_reviews",
        "memory_world",
        "evolution_lab",
        "organs",
    )

    def project(self, status: dict[str, Any]) -> dict[str, Any]:
        panels = [
            self._life_panel(status),
            self._attention_panel(status),
            self._goals_panel(status),
            self._actions_panel(status),
            self._approval_channels_panel(status),
            self._scheduled_events_panel(status),
            self._external_handoffs_panel(status),
            self._task_previews_panel(status),
            self._execution_preflight_panel(status),
            self._execution_receipts_panel(status),
            self._result_projections_panel(status),
            self._projection_reviews_panel(status),
            self._memory_world_panel(status),
            self._evolution_panel(status),
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
    def _organs_panel(status: dict[str, Any]) -> dict[str, Any]:
        return {
            "panel_id": "organs",
            "title": "Organs",
            "status": {
                "capabilities": status.get("capabilities", {}),
                "authority_model": "many organs, one canonical WLS subject",
            },
        }
