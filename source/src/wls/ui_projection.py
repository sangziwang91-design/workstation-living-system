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
        "provider_routes",
        "goals",
        "actions_approval",
        "approval_channels",
        "scheduled_events",
        "external_handoffs",
        "voice_ingress",
        "notification_drafts",
        "screen_snapshots",
        "task_previews",
        "execution_preflight",
        "execution_receipts",
        "result_projections",
        "projection_reviews",
        "memory_world",
        "evolution_lab",
        "skill_candidates",
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
            self._task_previews_panel(status),
            self._execution_preflight_panel(status),
            self._execution_receipts_panel(status),
            self._result_projections_panel(status),
            self._projection_reviews_panel(status),
            self._memory_world_panel(status),
            self._evolution_panel(status),
            self._skill_candidates_panel(status),
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
        return {
            "panel_id": "capability_epoch",
            "title": "Capability Epoch",
            "status": {
                "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
                "items": receipts if isinstance(receipts, list) else [],
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
