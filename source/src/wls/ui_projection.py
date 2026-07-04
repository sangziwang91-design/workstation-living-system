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
