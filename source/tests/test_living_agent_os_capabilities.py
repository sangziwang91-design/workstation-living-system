from __future__ import annotations

from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import threading

import pytest

from wls.a2a_adapter import A2AAdapter, ArtifactEnvelope, TaskContract, payload_digest
from wls.architecture_validation import (
    _insert_waiting_write_action,
    validate_browser_computer_organs,
    validate_phase2_browser_form_draft_receipts,
    validate_coding_worktree_candidate,
    validate_phase2_download_quarantine_draft_receipts,
    validate_phase2_document_ingress_receipts,
    validate_phase2_document_retrieval_preview,
    validate_phase2_document_readonly_execution,
    validate_phase2_document_projection_review,
    validate_phase2_document_skill_candidate_receipts,
    validate_phase2_document_skill_sandbox_receipts,
    validate_phase2_agentic_acceptance_trace,
    validate_phase2_agentic_file_mailbox_handoff,
    validate_phase2_agentic_repair_candidate,
    validate_phase2_agentic_budget_gate,
    validate_phase2_agentic_benchmark_scorecard,
    validate_phase2_agentic_checkpoint_resume,
    validate_phase2_agentic_artifact_finalize_acceptance,
    validate_phase2_agentic_lease_fencing_reconciliation,
    validate_phase2_agentic_retry_gate,
    validate_phase2_agentic_replan_candidate,
    validate_phase2_agentic_role_context_packets,
    validate_phase2_agentic_context_epoch_checkpoint,
    validate_phase2_agentic_process_auditor,
    validate_phase2_agentic_result_replay_quarantine,
    validate_phase2_agentic_worker_trust_quarantine,
    validate_phase2_agentic_worker_capability_arbitration,
    validate_phase2_agentic_worker_lease_recovery,
    validate_phase2_agentic_worker_lifecycle,
    validate_phase2_agentic_task_harness,
    validate_phase2_offspring_birth_contract,
    validate_phase2_offspring_budget_no_gain_stop,
    validate_phase2_offspring_checkpoint_fork,
    validate_phase2_offspring_ecology_audit,
    validate_phase2_offspring_isolated_state_budget,
    validate_phase2_offspring_mailbox_envelope,
    validate_phase2_offspring_retirement_cleanup,
    validate_phase2_offspring_retirement_tombstone,
    validate_phase2_sandbox_adapter_contract,
    validate_external_memory_projection,
    validate_mcp_a2a_candidates,
    validate_phase2_capability_epoch_audit_receipts,
    validate_phase2_coding_candidate_readonly_execution,
    validate_phase2_browser_readonly_runtime_execution,
    validate_phase2_external_handoff_runtime_receipts,
    validate_phase2_delivery_gap_audit,
    validate_phase2_delivery_handoff_package,
    validate_phase2_delivery_readiness_audit,
    validate_phase2_final_delivery_audit,
    validate_phase2_final_route_absorption_audit,
    validate_phase2_holdout_epoch_immutability,
    validate_phase2_installed_tail_check_audit,
    validate_phase2_multimodal_asset_readonly_execution,
    validate_phase2_operational_preflight_audit,
    validate_phase2_owner_goal_metadata_persistence,
    validate_phase2_owner_surface_and_readonly_organs,
    validate_phase2_owner_console_readiness_view,
    validate_phase2_packaging_layout_audit,
    validate_phase2_paired_baseline_candidate_experiment,
    validate_phase2_preflighted_readonly_execution,
    validate_phase2_promotion_bundle_gate,
    validate_phase2_projection_review_and_rollback,
    validate_phase2_provider_route_runtime_receipts,
    validate_phase2_readonly_result_projection,
    validate_phase2_runtime_readonly_task_preview,
    validate_phase2_readonly_planner_admission,
    validate_phase2_readonly_execution_preflight,
    validate_phase2_research_composite_readonly_execution,
    validate_phase2_release_state_audit,
    validate_phase2_release_handoff_summary,
    validate_phase2_scheduler_due_event_runtime_intake,
    validate_phase2_skill_candidate_extraction_receipts,
    validate_phase2_source_artifact_inventory_audit,
    validate_phase2_learning_epoch_review_receipts,
    validate_phase2_local_notification_draft_receipts,
    validate_phase2_screen_snapshot_ingress_receipts,
    validate_phase2_typed_readonly_organ_profiles,
    validate_phase2_transfer_efficiency_audit,
    validate_phase2_ui_hardening_audit,
    validate_phase2_ui_package_absorption_audit,
    validate_phase2_wechat_approval_channel_receipts,
    validate_phase2_voice_transcript_ingress_receipts,
    validate_p01_registry,
    validate_runtime_approval_receipts,
    validate_runtime_event_ingress,
    validate_runtime_provider_route,
    validate_workbench_templates,
)
from wls.browser_adapter import BrowserReadOnlyAdapter, BrowserReadOnlyRequest
from wls.capabilities import (
    CapabilityManifest,
    CapabilityMode,
    CapabilityStatus,
    baseline_registry,
)
from wls.channel_gateway import ChannelGateway, ChannelMessage
from wls.config import default_config
from wls.coding_adapter import CodingTaskContract
from wls.computer_adapter import ComputerUseAdapter, ComputerUseContract
from wls.external_memory import ExternalMemoryCandidate, ExternalMemoryProjection
from wls.mcp_adapter import McpCandidate, McpTrustGate
from wls.multimodal import MultimodalArtifactEnvelope
from wls.provider_router import ProviderDescriptor, ProviderRouter, RouteRequest
from wls.read_only_organs import (
    ORGAN_PROFILES,
    ReadOnlyOrganProfile,
    ReadOnlyTaskRequest,
)
from wls.runtime import LivingSystem
from wls.scheduler import EventScheduler, ScheduledEvent
from wls.schemas import ActionSpec, RiskLevel
from wls.ui_projection import OwnerConsoleProductProjection, OwnerConsoleProjection
from wls.wechat_adapter import WeChatW0W1Adapter
from wls.workbench import WorkbenchTemplate


def test_capability_registry_rejects_duplicate_authority() -> None:
    with pytest.raises(ValueError, match="canonical authority"):
        CapabilityManifest(
            capability_id="browser_kernel",
            version="0.1",
            mode=CapabilityMode.ADAPTER,
            canonical_owner="tools",
            risk_ceiling="READ",
            side_effect_class="none",
            interfaces=["browser"],
            rollback=["disable manifest"],
            declares_authority=True,
        )


def test_registry_rejects_unknown_owner_and_active_without_receipts() -> None:
    with pytest.raises(ValueError, match="unknown canonical owner"):
        CapabilityManifest(
            capability_id="unknown_owner",
            version="0.1",
            mode=CapabilityMode.ADAPTER,
            canonical_owner="new_memory",
            risk_ceiling="READ",
            side_effect_class="none",
            interfaces=["x"],
            rollback=["disable"],
        )
    with pytest.raises(ValueError, match="requires tests"):
        CapabilityManifest(
            capability_id="active_no_tests",
            version="0.1",
            mode=CapabilityMode.ADAPTER,
            canonical_owner="events",
            risk_ceiling="READ",
            side_effect_class="none",
            interfaces=["x"],
            rollback=["disable"],
            status=CapabilityStatus.ACTIVE,
        )


def test_baseline_registry_has_no_duplicate_authority() -> None:
    registry = baseline_registry()
    registry.assert_no_duplicate_authority()
    owners = {item["canonical_owner"] for item in registry.list()}
    assert {"events", "tools", "planning", "evidence", "evolution", "memory"} <= owners


def test_channel_and_scheduler_emit_events_only() -> None:
    message = ChannelMessage(
        channel="owner_console",
        sender_id="owner",
        content="status?",
        message_id="m1",
    )
    event = ChannelGateway().to_event(message)
    assert event.event_type == "channel.message"
    assert event.payload["content"] == "status?"
    scheduled = EventScheduler().emit_due(
        ScheduledEvent(
            schedule_id="s1",
            event_type="scheduled.read_only_check",
            payload={"target": "heartbeat"},
            due_at="2026-06-30T00:00:00+00:00",
        )
    )
    assert scheduled.source == "scheduler"
    with pytest.raises(PermissionError):
        EventScheduler().emit_due(
            ScheduledEvent(
                schedule_id="s2",
                event_type="scheduled.write",
                payload={},
                due_at="2026-06-30T00:00:00+00:00",
                side_effect_class="external",
            )
        )


def test_channel_and_scheduler_submit_through_event_store(tmp_path: Path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    channel_event_id, channel_inserted = ChannelGateway().submit(
        ChannelMessage("owner_console", "owner", "status?", "m2"),
        runtime.events,
    )
    scheduled_event_id, scheduled_inserted = EventScheduler().submit_due(
        ScheduledEvent(
            schedule_id="s3",
            event_type="scheduled.read_only_check",
            payload={"target": "status"},
            due_at="2026-06-30T00:00:00+00:00",
        ),
        runtime.events,
    )
    assert channel_inserted is True
    assert scheduled_inserted is True
    rows = runtime.db.query_all(
        "SELECT event_id,event_type,source FROM events ORDER BY rowid"
    )
    assert [row["event_id"] for row in rows] == [channel_event_id, scheduled_event_id]
    assert rows[0]["event_type"] == "channel.message"
    assert rows[1]["source"] == "scheduler"


def test_living_system_status_exposes_capability_projection(tmp_path: Path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    status = runtime.status()
    assert status["capabilities"]["authority_model"] == "canonical WLS owners only"
    assert status["capabilities"]["count"] >= 10
    assert status["capabilities"]["by_owner"]["events"] >= 1
    assert status["planner_route"]["provider_id"] == status["planner_provider"]


def test_living_system_exposes_channel_and_scheduler_ingress(tmp_path: Path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    channel_id, channel_inserted = runtime.ingest_channel_message(
        ChannelMessage("owner_console", "owner", "status?", "m-runtime")
    )
    scheduled_id, scheduled_inserted = runtime.emit_scheduled_event(
        ScheduledEvent(
            schedule_id="s-runtime",
            event_type="scheduled.read_only_check",
            payload={"target": "status"},
            due_at="2026-06-30T00:00:00+00:00",
        )
    )
    assert channel_inserted is True
    assert scheduled_inserted is True
    assert runtime.events.counts()["PENDING"] == 2
    assert channel_id != scheduled_id


def test_living_system_intakes_scheduled_event_with_receipt_only(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    receipt = runtime.intake_scheduled_event(
        ScheduledEvent(
            schedule_id="unit-scheduled-1",
            event_type="scheduled.read_only_check",
            payload={"target": "status"},
            due_at="2026-07-02T00:00:00+00:00",
        )
    )
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    assert receipt["status"] == "QUEUED_EVENT_ONLY"
    assert receipt["inserted"] is True
    assert receipt["creates_goal"] is False
    assert receipt["creates_action"] is False
    assert receipt["direct_tool_execution"] is False
    assert before == after
    assert runtime.scheduled_event_receipts()[0]["schedule_id"] == "unit-scheduled-1"
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "scheduled_events"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["creates_goal"] is False


def test_living_system_intakes_voice_transcript_as_event_only(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    receipt = runtime.intake_voice_transcript(
        transcript_id="voice-1",
        speaker_id="owner",
        transcript="status check from local voice transcript",
        locale="en-US",
        confidence=0.9,
    )
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    event = runtime.db.query_one(
        "SELECT event_type,source,payload_json FROM events WHERE event_id=?",
        (receipt["event_id"],),
    )
    assert receipt["status"] == "QUEUED_EVENT_ONLY"
    assert receipt["audio_captured"] is False
    assert receipt["stt_executed"] is False
    assert receipt["creates_goal"] is False
    assert receipt["creates_action"] is False
    assert receipt["direct_tool_execution"] is False
    assert before == after
    assert event is not None
    assert event["event_type"] == "channel.message"
    assert event["source"] == "channel:voice"
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "voice_ingress"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["audio_captured"] is False
    assert panel["status"]["stt_executed"] is False
    with pytest.raises(ValueError, match="cannot be empty"):
        runtime.intake_voice_transcript(
            transcript_id="voice-empty",
            speaker_id="owner",
            transcript=" ",
        )


def test_living_system_drafts_local_notification_without_delivery(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    receipt = runtime.draft_local_notification(
        channel="voice",
        mode="speech_script",
        purpose="unit test owner reply",
        body="WLS status draft is ready for Owner review.",
    )
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    draft_path = Path(receipt["outbox_path"])
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "DRAFT_WRITTEN"
    assert receipt["delivery_executed"] is False
    assert receipt["tts_executed"] is False
    assert receipt["audio_played"] is False
    assert receipt["external_send_executed"] is False
    assert receipt["creates_goal"] is False
    assert receipt["creates_action"] is False
    assert before == after
    assert draft["channel"] == "voice"
    assert draft["mode"] == "speech_script"
    assert draft["delivery_executed"] is False
    assert draft["tts_executed"] is False
    assert draft["audio_played"] is False
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "notification_drafts"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["delivery_executed"] is False
    assert panel["status"]["tts_executed"] is False
    with pytest.raises(ValueError, match="unsupported notification channel"):
        runtime.draft_local_notification(
            channel="external_sms",
            purpose="bad channel",
            body="no",
        )


def test_living_system_intakes_screen_snapshot_as_event_only(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    snapshot = tmp_path / "screen.png"
    snapshot.write_bytes(b"\x89PNG\r\n\x1a\nWLS-SCREEN")
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    receipt = runtime.intake_screen_snapshot_asset(
        snapshot_id="screen-1",
        path=snapshot,
        source="pytest_fixture",
        purpose="unit test screen context",
    )
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    event = runtime.db.query_one(
        "SELECT event_type,source,payload_json FROM events WHERE event_id=?",
        (receipt["event_id"],),
    )
    assert receipt["status"] == "QUEUED_EVENT_ONLY"
    assert receipt["ocr_executed"] is False
    assert receipt["ui_control_executed"] is False
    assert receipt["external_upload_executed"] is False
    assert receipt["creates_goal"] is False
    assert receipt["creates_action"] is False
    assert receipt["direct_tool_execution"] is False
    assert before == after
    assert event is not None
    assert event["event_type"] == "channel.message"
    assert event["source"] == "channel:screen"
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "screen_snapshots"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["ocr_executed"] is False
    assert panel["status"]["ui_control_executed"] is False
    with pytest.raises(ValueError, match="existing file"):
        runtime.intake_screen_snapshot_asset(
            snapshot_id="screen-missing",
            path=tmp_path / "missing.png",
        )


def test_living_system_drafts_browser_form_without_submission(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    receipt = runtime.draft_browser_form_submission(
        form_id="form-1",
        url="http://127.0.0.1/form",
        fields={"query": "local evidence", "mode": "readonly"},
        purpose="unit test browser form draft",
    )
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    assert receipt["status"] == "DRAFT_RECORDED"
    assert receipt["field_names"] == ["mode", "query"]
    assert receipt["browser_opened"] is False
    assert receipt["form_submitted"] is False
    assert receipt["network_post_executed"] is False
    assert receipt["creates_goal"] is False
    assert receipt["creates_action"] is False
    assert receipt["approval_required_for_submission"] is True
    assert before == after
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "browser_form_drafts"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["browser_opened"] is False
    assert panel["status"]["form_submitted"] is False
    with pytest.raises(PermissionError, match="HTTPS or loopback"):
        runtime.draft_browser_form_submission(
            form_id="form-bad",
            url="http://example.com/form",
            fields={"query": "bad"},
            purpose="bad form",
        )


def test_living_system_drafts_download_quarantine_without_fetch(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    receipt = runtime.draft_download_quarantine(
        download_id="download-1",
        url="http://127.0.0.1/file.txt",
        filename="../file.txt",
        purpose="unit test download quarantine",
    )
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    manifest_path = Path(receipt["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "QUARANTINE_DRAFT_RECORDED"
    assert receipt["filename"] == "file.txt"
    assert receipt["network_fetch_executed"] is False
    assert receipt["file_materialized"] is False
    assert receipt["external_write_executed"] is False
    assert receipt["creates_goal"] is False
    assert receipt["creates_action"] is False
    assert receipt["approval_required_for_fetch"] is True
    assert before == after
    assert manifest["network_fetch_executed"] is False
    assert manifest["file_materialized"] is False
    assert not Path(manifest["target_path"]).exists()
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "download_quarantine"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["network_fetch_executed"] is False
    assert panel["status"]["file_materialized"] is False
    with pytest.raises(PermissionError, match="HTTPS or loopback"):
        runtime.draft_download_quarantine(
            download_id="download-bad",
            url="http://example.com/file.txt",
            filename="file.txt",
            purpose="bad download",
        )


def test_living_system_intakes_document_asset_as_event_only(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    document = tmp_path / "sample.pdf"
    document.write_bytes(b"%PDF-1.4\nWLS document fixture\n")
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    receipt = runtime.intake_document_asset(
        document_id="document-1",
        path=document,
        source="pytest_fixture",
        purpose="unit test document ingress",
    )
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "plans", "actions")
    }
    event = runtime.db.query_one(
        "SELECT event_type,source,payload_json FROM events WHERE event_id=?",
        (receipt["event_id"],),
    )
    assert receipt["status"] == "QUEUED_EVENT_ONLY"
    assert receipt["mime_type"] == "application/pdf"
    assert receipt["text_extracted"] is False
    assert receipt["ocr_executed"] is False
    assert receipt["vector_indexed"] is False
    assert receipt["external_upload_executed"] is False
    assert receipt["creates_goal"] is False
    assert receipt["creates_action"] is False
    assert receipt["direct_tool_execution"] is False
    assert before == after
    assert event is not None
    assert event["event_type"] == "channel.message"
    assert event["source"] == "channel:document"
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "document_ingress"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["text_extracted"] is False
    assert panel["status"]["vector_indexed"] is False
    with pytest.raises(ValueError, match="existing file"):
        runtime.intake_document_asset(
            document_id="document-missing",
            path=tmp_path / "missing.pdf",
        )


def test_living_system_prepares_document_retrieval_preview_without_execution(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    document = tmp_path / "sample.pdf"
    document.write_bytes(b"%PDF-1.4\nWLS document fixture\n")
    source = runtime.intake_document_asset(
        document_id="document-preview-1",
        path=document,
        source="pytest_fixture",
        purpose="unit test document preview",
    )
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("plans", "actions")
    }
    result = runtime.prepare_document_retrieval_preview(
        document_id="document-preview-1",
        request_id="document-preview-request-1",
        owner_intent="prepare a local document inspection preview",
    )
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("plans", "actions")
    }
    preview = result["read_only_task"]["preview"]
    candidate = preview["candidate"]
    tools = {action["tool"] for action in candidate["candidate_actions"]}
    assert source["status"] == "QUEUED_EVENT_ONLY"
    assert result["status"] == "PREVIEW_ONLY"
    assert result["source_receipt_sha256"] == source["sha256"]
    assert result["creates_plan"] is False
    assert result["creates_action"] is False
    assert result["direct_tool_execution"] is False
    assert result["text_extracted"] is False
    assert result["vector_indexed"] is False
    assert before == after
    assert preview["status"] == "PREVIEW_ONLY"
    assert candidate["organ_id"] == "document"
    assert {"inspect_asset", "read_file"} <= tools
    assert "document_sha256" in candidate["evidence_required"]
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "task_previews"
    )
    assert panel["status"]["preview_count"] == 1
    with pytest.raises(KeyError, match="unknown document"):
        runtime.prepare_document_retrieval_preview(
            document_id="missing",
            request_id="bad-document-preview",
            owner_intent="bad preview",
        )


def test_document_organ_executes_local_readonly_inspection(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    document = tmp_path / "sample.pdf"
    document.write_bytes(b"%PDF-1.4\nWLS document execution fixture\n")
    source = runtime.intake_document_asset(
        document_id="document-execute-1",
        path=document,
        source="pytest_fixture",
        purpose="unit test document execution",
    )
    preview = runtime.prepare_document_retrieval_preview(
        document_id="document-execute-1",
        request_id="document-execute-request-1",
        owner_intent="inspect a local document through read-only tools",
    )
    admission = runtime.admit_read_only_plan_preview(
        "document-execute-request-1",
        reason="unit test document admission",
    )
    preflight = runtime.preflight_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    actions = runtime.db.query_all(
        "SELECT status,risk,side_effect_class,tool FROM actions WHERE plan_id=?",
        (admission["plan_id"],),
    )
    output_keys = {
        key
        for outcome in receipt["outcomes"]
        for key in outcome.get("output", {})
    }
    assert source["status"] == "QUEUED_EVENT_ONLY"
    assert preview["status"] == "PREVIEW_ONLY"
    assert admission["status"] == "ADMITTED_AS_PLAN"
    assert preflight["status"] == "READY_FOR_EXECUTION"
    assert receipt["status"] == "EXECUTED_READ_ONLY"
    assert receipt["all_succeeded"] is True
    assert receipt["direct_tool_execution"] is True
    assert {row["tool"] for row in actions} == {"inspect_asset", "read_file"}
    assert {row["risk"] for row in actions} == {"READ"}
    assert {row["side_effect_class"] for row in actions} == {"none"}
    assert {row["status"] for row in actions} == {"SUCCEEDED"}
    assert {"sha256", "mime_type", "text", "path"} <= output_keys
    event_types = {
        row["event_type"]
        for row in runtime.db.query_all(
            "SELECT event_type FROM evidence WHERE event_type IN ('action_completed','read_only_plan_executed')"
        )
    }
    assert {"action_completed", "read_only_plan_executed"} <= event_types


def test_document_readonly_execution_projects_and_rolls_back_candidate(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    document = tmp_path / "sample.pdf"
    document.write_bytes(b"%PDF-1.4\nWLS document projection fixture\n")
    source = runtime.intake_document_asset(
        document_id="document-project-1",
        path=document,
        source="pytest_fixture",
        purpose="unit test document projection",
    )
    runtime.prepare_document_retrieval_preview(
        document_id="document-project-1",
        request_id="document-project-request-1",
        owner_intent="project and review a local document read-only result",
    )
    admission = runtime.admit_read_only_plan_preview(
        "document-project-request-1",
        reason="unit test document projection admission",
    )
    runtime.preflight_read_only_plan(str(admission["plan_id"]))
    execution = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    projection = runtime.project_read_only_execution_receipt(str(admission["plan_id"]))
    review = runtime.review_read_only_result_projection(
        str(admission["plan_id"]),
        "ROLLBACK_CANDIDATE",
        reason="unit test document rollback",
    )
    memory = runtime.db.query_one(
        "SELECT memory_type,content_json,active FROM memories WHERE memory_id=?",
        (projection["memory_id"],),
    )
    fact = runtime.db.query_one(
        "SELECT verification,source_kind,value_json,active FROM world_facts WHERE fact_id=?",
        (projection["fact_id"],),
    )
    assert source["status"] == "QUEUED_EVENT_ONLY"
    assert execution["status"] == "EXECUTED_READ_ONLY"
    assert execution["all_succeeded"] is True
    assert projection["status"] == "PROJECTED_CANDIDATE"
    assert projection["candidate_only"] is True
    assert review["decision"] == "ROLLBACK_CANDIDATE"
    assert review["rolled_back"] is True
    assert memory is not None
    assert memory["memory_type"] == "read_only_execution_candidate"
    assert memory["active"] == 0
    memory_content = json.loads(memory["content_json"])
    assert memory_content["candidate_only"] is True
    assert memory_content["does_not_complete_goal"] is True
    assert memory_content["does_not_promote_skill"] is True
    assert fact is not None
    assert fact["verification"] == "INFERENCE"
    assert fact["source_kind"] == "INFERENCE"
    assert fact["active"] == 0
    assert json.loads(fact["value_json"])["candidate_only"] is True
    projection_panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "result_projections"
    )
    review_panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "projection_reviews"
    )
    assert projection_panel["status"]["projection_count"] == 1
    assert review_panel["status"]["review_count"] == 1
    event_types = {
        row["event_type"]
        for row in runtime.db.query_all(
            "SELECT event_type FROM evidence WHERE event_type IN ('read_only_execution_result_projected','read_only_result_projection_reviewed')"
        )
    }
    assert {
        "read_only_execution_result_projected",
        "read_only_result_projection_reviewed",
    } <= event_types


def test_document_repeated_readonly_execution_proposes_skill_candidate(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    document = tmp_path / "sample.pdf"
    document.write_bytes(b"%PDF-1.4\nWLS document skill fixture\n")
    before_active = len(runtime.skills.active())
    for index in range(3):
        document_id = f"document-skill-{index}"
        request_id = f"document-skill-request-{index}"
        runtime.intake_document_asset(
            document_id=document_id,
            path=document,
            source="pytest_fixture",
            purpose="unit test document skill source",
        )
        runtime.prepare_document_retrieval_preview(
            document_id=document_id,
            request_id=request_id,
            owner_intent="repeat local document inspection for Skill candidate",
        )
        admission = runtime.admit_read_only_plan_preview(
            request_id,
            reason="unit test document skill admission",
        )
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.propose_skill_candidates_from_receipts(
        minimum_repeats=3,
        reason="unit test document skill extraction",
    )
    rows = [
        runtime.db.query_one(
            "SELECT status,definition_json FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        for skill_id in receipt["created_skill_ids"]
    ]
    definitions = [
        json.loads(str(row["definition_json"])) for row in rows if row is not None
    ]
    tools = {
        step["tool"]
        for definition in definitions
        for step in definition.get("steps", [])
    }
    assert receipt["status"] == "CANDIDATES_PROPOSED"
    assert receipt["candidate_count"] >= 1
    assert receipt["candidate_only"] is True
    assert receipt["promotion_executed"] is False
    assert receipt["approval_executed"] is False
    assert receipt["sandbox_executed"] is False
    assert {row["status"] for row in rows if row is not None} == {"PROPOSED"}
    assert len(runtime.skills.active()) == before_active
    assert {"inspect_asset", "read_file"} <= tools
    assert sum(
        len(definition.get("source_episode_ids", [])) for definition in definitions
    ) >= 6
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "skill_candidates"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["candidate_only"] is True
    assert panel["status"]["promotion_executed"] is False


def test_document_skill_candidate_starts_sandbox_without_promotion(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    document = tmp_path / "sample.pdf"
    document.write_bytes(b"%PDF-1.4\nWLS document sandbox fixture\n")
    before_active = len(runtime.skills.active())
    for index in range(3):
        document_id = f"document-sandbox-{index}"
        request_id = f"document-sandbox-request-{index}"
        runtime.intake_document_asset(
            document_id=document_id,
            path=document,
            source="pytest_fixture",
            purpose="unit test document sandbox source",
        )
        runtime.prepare_document_retrieval_preview(
            document_id=document_id,
            request_id=request_id,
            owner_intent="repeat local document inspection for Skill sandbox",
        )
        admission = runtime.admit_read_only_plan_preview(
            request_id,
            reason="unit test document sandbox admission",
        )
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    candidate = runtime.propose_skill_candidates_from_receipts(
        minimum_repeats=3,
        reason="unit test document sandbox extraction",
    )
    skill_id = candidate["created_skill_ids"][0]
    sandbox = runtime.start_skill_sandbox_validation(
        skill_id=skill_id,
        reason="unit test sandbox start",
    )
    skill = runtime.db.query_one(
        "SELECT status,definition_json FROM skills WHERE skill_id=?", (skill_id,)
    )
    experiment = runtime.db.query_one(
        "SELECT status,manifest_json,result_json FROM skill_experiments WHERE experiment_id=?",
        (sandbox["experiment_id"],),
    )
    assert sandbox["status"] == "SANDBOX_STARTED"
    assert sandbox["skill_status_before"] == "PROPOSED"
    assert sandbox["skill_status_after"] == "SANDBOXED"
    assert sandbox["candidate_only"] is True
    assert sandbox["validation_passed"] is False
    assert sandbox["approval_executed"] is False
    assert sandbox["promotion_executed"] is False
    assert sandbox["deployment_executed"] is False
    assert len(runtime.skills.active()) == before_active
    assert skill is not None and skill["status"] == "SANDBOXED"
    assert json.loads(skill["definition_json"])["status"] == "SANDBOXED"
    assert experiment is not None and experiment["status"] == "RUNNING"
    assert experiment["result_json"] is None
    assert json.loads(experiment["manifest_json"])["mode"] == "sandbox_candidate_only"
    assert Path(sandbox["manifest_path"]).is_file()
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "skill_sandbox"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["sandbox_started"] is True
    assert panel["status"]["validation_passed"] is False
    assert panel["status"]["promotion_executed"] is False
    event_types = {
        row["event_type"]
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'skill_sandbox_validation_started',
                'skill_transition',
                'skill_sandbox_receipt_recorded'
            )
            """
        )
    }
    assert {
        "skill_sandbox_validation_started",
        "skill_transition",
        "skill_sandbox_receipt_recorded",
    } <= event_types


def test_provider_router_enforces_local_first_and_cost() -> None:
    router = ProviderRouter(
        [
            ProviderDescriptor(
                provider_id="remote_paid",
                locality="remote",
                capabilities={"planning"},
                cost_class="metered",
                paid=True,
                remote=True,
            ),
            ProviderDescriptor(
                provider_id="local_free",
                locality="local",
                capabilities={"planning"},
            ),
        ]
    )
    route = router.choose(RouteRequest(required_capability="planning"))
    assert route.provider_id == "local_free"
    with pytest.raises(PermissionError):
        router.choose(RouteRequest(required_capability="vision"))


def test_planner_provider_route_matches_config_and_blocks_silent_remote(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    runtime = LivingSystem(config)
    assert runtime.planner.route_summary()["provider_id"] == "cognitive"

    remote_config = default_config(tmp_path / "remote-home")
    remote_config.provider = {
        "type": "openai_compatible",
        "model": "example",
        "fallback": "deterministic",
        "cost_class": "metered",
        "paid": True,
    }
    with pytest.raises(PermissionError, match="provider route"):
        LivingSystem(remote_config)


def test_runtime_records_provider_route_receipt_without_model_call(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("plans", "actions")
    }
    receipt = runtime.record_provider_route_receipt(reason="unit test route")
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("plans", "actions")
    }
    assert receipt["status"] == "RECORDED_ROUTE"
    assert receipt["provider_id"] == "cognitive"
    assert receipt["provider"]["locality"] == "local"
    assert receipt["provider"]["cost_class"] == "free"
    assert receipt["request"]["privacy"] == "local_only"
    assert receipt["direct_model_call"] is False
    assert receipt["direct_tool_execution"] is False
    assert before == after
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "provider_routes"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["direct_model_call"] is False


def test_browser_readonly_receipt_and_allowlist() -> None:
    adapter = BrowserReadOnlyAdapter()
    receipt = adapter.inspect_text(
        BrowserReadOnlyRequest(
            url="http://127.0.0.1/page",
            allowed_hosts={"127.0.0.1"},
            session_digest="abc",
        ),
        "hello",
    )
    assert receipt.host == "127.0.0.1"
    assert receipt.text_sha256
    with pytest.raises(PermissionError):
        adapter.inspect_text(
            BrowserReadOnlyRequest(
                url="https://example.com",
                allowed_hosts={"127.0.0.1"},
                session_digest="abc",
            ),
            "blocked",
        )


def test_browser_readonly_fetch_loopback_receipt_and_redirect_block() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BrowserTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        adapter = BrowserReadOnlyAdapter()
        receipt = adapter.fetch_text(
            BrowserReadOnlyRequest(
                url=f"http://127.0.0.1:{port}/page",
                allowed_hosts={"127.0.0.1"},
                session_digest="test-session",
            )
        )
        assert receipt.status_code == 200
        assert receipt.bytes_read > 0
        assert receipt.downloads == []
        with pytest.raises(Exception):
            adapter.fetch_text(
                BrowserReadOnlyRequest(
                    url=f"http://127.0.0.1:{port}/redirect",
                    allowed_hosts={"127.0.0.1"},
                    session_digest="test-session",
                )
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_computer_use_blocks_without_sandbox() -> None:
    with pytest.raises(PermissionError, match="sandbox"):
        ComputerUseAdapter().admit(ComputerUseContract(target="desktop"))


def test_coding_worktree_contract_is_candidate_only(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("print('candidate')\n", encoding="utf-8")
    contract = CodingTaskContract(
        task_id="task-1",
        base_sha="abc123",
        worktree=tmp_path.resolve(),
        changed_files=["x.py"],
        tests=["pytest"],
        rollback=["remove worktree"],
    )
    artifact = contract.candidate_artifact()
    assert artifact["status"] == "CANDIDATE_ONLY"
    assert artifact["changed_files"][0]["path"] == "x.py"
    assert artifact["changed_files"][0]["sha256"]
    with pytest.raises(ValueError, match="escapes worktree"):
        CodingTaskContract(
            task_id="task-escape",
            base_sha="abc123",
            worktree=tmp_path.resolve(),
            changed_files=["..\\escape.py"],
            tests=["pytest"],
            rollback=["remove worktree"],
        ).candidate_artifact()


def test_unpinned_mcp_rejected_and_reviewed_candidate_admitted() -> None:
    gate = McpTrustGate()
    with pytest.raises(PermissionError, match="pinned"):
        gate.admit(McpCandidate("srv", None, "stdio"))
    result = gate.admit(
        McpCandidate(
            server_id="srv",
            identity_digest="sha256:abc",
            transport="stdio",
            review_status="REVIEWED",
        )
    )
    assert result["status"] == "VALIDATED_CANDIDATE"


def test_a2a_artifact_remains_candidate_only() -> None:
    contract = TaskContract(
        task_id="t1",
        objective="inspect",
        scope={"paths": []},
        allowed_outputs=["report"],
        expires_at="2026-06-30T00:00:00+00:00",
    )
    payload = {"ok": True}
    result = A2AAdapter().receive(
        contract,
        ArtifactEnvelope(
            task_id="t1",
            artifact_type="report",
            payload=payload,
            hashes={"payload_sha256": payload_digest(payload)},
        ),
    )
    assert result["status"] == "CANDIDATE_ONLY"
    with pytest.raises(PermissionError):
        A2AAdapter().receive(
            contract,
            ArtifactEnvelope(
                task_id="t1",
                artifact_type="report",
                payload={"goal_complete": True},
                candidate_only=False,
            ),
        )
    with pytest.raises(PermissionError, match="hash mismatch"):
        A2AAdapter().receive(
            contract,
            ArtifactEnvelope(
                task_id="t1",
                artifact_type="report",
                payload=payload,
                hashes={"payload_sha256": "wrong"},
            ),
        )


def test_runtime_records_external_handoffs_as_candidate_receipts(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    before = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "actions", "memories")
    }
    mcp_receipt = runtime.admit_mcp_candidate(
        McpCandidate(
            server_id="unit-mcp",
            identity_digest="sha256:unit-mcp",
            transport="stdio",
            review_status="REVIEWED",
        ),
        reason="unit test mcp handoff",
    )
    contract = TaskContract(
        task_id="unit-a2a",
        objective="candidate report",
        scope={"paths": []},
        allowed_outputs=["report"],
        expires_at="2026-07-02T00:00:00+00:00",
    )
    payload = {"ok": True}
    a2a_receipt = runtime.receive_a2a_artifact(
        contract,
        ArtifactEnvelope(
            task_id="unit-a2a",
            artifact_type="report",
            payload=payload,
            hashes={"payload_sha256": payload_digest(payload)},
        ),
        reason="unit test a2a handoff",
    )
    after = {
        name: runtime.db.query_one(f"SELECT COUNT(*) AS count FROM {name}")["count"]
        for name in ("goals", "actions", "memories")
    }
    assert mcp_receipt["status"] == "VALIDATED_CANDIDATE"
    assert a2a_receipt["status"] == "CANDIDATE_ONLY"
    assert before == after
    assert {item["receipt_type"] for item in runtime.external_handoff_receipts()} == {
        "MCP_CANDIDATE",
        "A2A_ARTIFACT",
    }
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "external_handoffs"
    )
    assert panel["status"]["receipt_count"] == 2
    assert panel["status"]["authority_transfer_allowed"] is False


def test_external_memory_projection_is_candidate_only() -> None:
    projection = ExternalMemoryProjection()
    receipt = projection.admit(
        ExternalMemoryCandidate(
            source_id="source-1",
            source_digest="sha256:abc",
            content={"summary": "external candidate"},
            evidence_hashes=["sha256:evidence"],
            tags=["shadow"],
        )
    )
    assert receipt.status == "CANDIDATE_ONLY"
    assert receipt.canonical_owner == "MemoryStore"
    assert receipt.content_sha256
    with pytest.raises(PermissionError, match="digest"):
        projection.admit(
            ExternalMemoryCandidate(
                source_id="source-2",
                source_digest="abc",
                content={"summary": "external candidate"},
                evidence_hashes=["sha256:evidence"],
            )
        )
    with pytest.raises(PermissionError, match="canonical memory"):
        projection.admit(
            ExternalMemoryCandidate(
                source_id="source-3",
                source_digest="sha256:abc",
                content={"summary": "external candidate"},
                evidence_hashes=["sha256:evidence"],
                candidate_only=False,
            )
        )


def test_owner_projection_and_wechat_w0_w1_are_read_only() -> None:
    projection = OwnerConsoleProjection().project(
        {"version": "x", "cycle_count": 1, "db": "hidden", "active_goals": []}
    )
    assert projection["cycle_count"] == 1
    assert "db" not in projection
    draft = WeChatW0W1Adapter("W0").outbound_notification("hello")
    assert draft["status"] == "DRAFT_NOTIFICATION"
    event = WeChatW0W1Adapter("W1").read_only_query_event(
        ChannelMessage("wechat", "owner", "status?", "wx1")
    )
    assert event.source == "channel:wechat"


def test_wechat_w2_approval_channel_queues_decision_event_only(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    action = _insert_waiting_write_action(
        runtime, runtime.config.sandbox_path / "approval.txt", "candidate"
    )
    before_approvals = runtime.db.query_one("SELECT COUNT(*) AS count FROM approvals")
    request = runtime.draft_wechat_approval_request(action.action_id)
    decision = runtime.intake_wechat_approval_decision(
        ChannelMessage("wechat", "owner", "approve", "wx-approval-1"),
        action_id=action.action_id,
        decision="APPROVE",
        reason="unit test decision event",
    )
    after_approvals = runtime.db.query_one("SELECT COUNT(*) AS count FROM approvals")
    row = runtime.db.query_one(
        "SELECT status,approval_id FROM actions WHERE action_id=?", (action.action_id,)
    )
    assert request["status"] == "DRAFT_APPROVAL_REQUEST"
    assert request["creates_approval"] is False
    assert request["direct_tool_execution"] is False
    assert decision["status"] == "QUEUED_EVENT_ONLY"
    assert decision["executes_action"] is False
    assert before_approvals is not None and after_approvals is not None
    assert before_approvals["count"] == after_approvals["count"]
    assert row is not None
    assert row["status"] == "WAITING_APPROVAL"
    assert row["approval_id"] is None
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "approval_channels"
    )
    assert panel["status"]["receipt_count"] == 2
    assert panel["status"]["channel_issues_approvals"] is False


def test_owner_console_product_projection_and_wechat_digest_are_read_only(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    projection = OwnerConsoleProductProjection().project(runtime.status())
    assert projection["mode"] == "READ_ONLY_PROJECTION"
    assert projection["writes_canonical_state"] is False
    assert projection["direct_tool_execution"] is False
    assert {
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
    } <= set(projection["panel_ids"])
    digest = WeChatW0W1Adapter("W1").console_digest_notification(projection)
    assert digest["status"] == "DRAFT_NOTIFICATION"
    assert digest["summary"]["panel_count"] == len(projection["panel_ids"])
    assert digest["writes_canonical_state"] is False
    assert digest["direct_tool_execution"] is False
    with pytest.raises(PermissionError, match="read-only projection"):
        WeChatW0W1Adapter("W1").console_digest_notification({"mode": "WRITE"})


def test_read_only_task_organs_submit_events_without_actions_or_goals(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    request = ReadOnlyTaskRequest(
        request_id="readonly-research-1",
        organ_id="research",
        owner_intent="inspect campaign evidence",
        inputs={"scope": "R26-R30"},
    )
    receipt = request.submit(runtime.events)
    template = request.to_workbench_template()
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    assert receipt.status == "QUEUED_EVENT_ONLY"
    assert receipt.inserted is True
    assert receipt.writes_canonical_state is False
    assert receipt.direct_tool_execution is False
    assert template["canonical_owner"] == "planning"
    assert template["status"] == "TEMPLATE_ONLY"
    assert before_actions is not None and after_actions is not None
    assert before_goals is not None and after_goals is not None
    assert before_actions["count"] == after_actions["count"]
    assert before_goals["count"] == after_goals["count"]
    rows = runtime.db.query_all(
        "SELECT event_type,source FROM events WHERE event_id=?", (receipt.event_id,)
    )
    assert rows[0]["event_type"] == "read_only_task.requested"
    assert rows[0]["source"] == "organ:research"
    with pytest.raises(PermissionError, match="side effects"):
        ReadOnlyTaskRequest(
            request_id="bad-write",
            organ_id="research",
            owner_intent="write to the world",
            side_effect_class="external",
        ).to_event()


def test_typed_read_only_organs_produce_planner_candidates() -> None:
    assert {
        "research",
        "browser",
        "file",
        "coding",
        "content",
        "document",
        "social_research",
        "multimodal",
    } <= set(ORGAN_PROFILES)
    for organ_id in ORGAN_PROFILES:
        request = ReadOnlyTaskRequest(
            request_id=f"typed-{organ_id}",
            organ_id=organ_id,
            owner_intent=f"prepare {organ_id} work",
            inputs={"fixture": organ_id},
        )
        candidate = request.to_plan_candidate()
        assert candidate["status"] == "PLAN_CANDIDATE_ONLY"
        assert candidate["canonical_owner"] == "Planner"
        assert candidate["writes_canonical_state"] is False
        assert candidate["direct_tool_execution"] is False
        assert candidate["requires_planner_admission"] is True
        assert candidate["candidate_actions"]
        assert {action["risk"] for action in candidate["candidate_actions"]} == {"READ"}
        assert "event_receipt" in candidate["evidence_required"]
    assert ORGAN_PROFILES["browser"].tool_hints == ("http_get",)
    assert ORGAN_PROFILES["document"].tool_hints == ("inspect_asset", "read_file")
    with pytest.raises(PermissionError, match="forbidden tool"):
        ReadOnlyOrganProfile(
            organ_id="bad",
            canonical_owner="planning",
            tool_hints=("write_file",),
            evidence_required=("receipt",),
            planner_contract="bad",
        ).to_dict()


def test_runtime_intakes_read_only_task_as_preview_without_execution(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    result = runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="preview-file-1",
            organ_id="file",
            owner_intent="inspect files before planning",
            inputs={"path": str(tmp_path)},
        )
    )
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    assert result["receipt"]["status"] == "QUEUED_EVENT_ONLY"
    assert result["preview"]["status"] == "PREVIEW_ONLY"
    assert result["preview"]["creates_plan_row"] is False
    assert result["preview"]["creates_action_row"] is False
    assert before_plans is not None and after_plans is not None
    assert before_actions is not None and after_actions is not None
    assert before_plans["count"] == after_plans["count"]
    assert before_actions["count"] == after_actions["count"]
    previews = runtime.status()["read_only_plan_previews"]
    assert previews[0]["request_id"] == "preview-file-1"
    projection = OwnerConsoleProductProjection().project(runtime.status())
    preview_panel = next(
        panel for panel in projection["panels"] if panel["panel_id"] == "task_previews"
    )
    assert preview_panel["status"]["preview_count"] == 1
    evidence = runtime.db.query_all(
        "SELECT event_type FROM evidence WHERE event_type='read_only_plan_preview_recorded'"
    )
    assert evidence


def test_runtime_admits_read_only_preview_as_planned_actions_without_execution(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="admit-file-1",
            organ_id="file",
            owner_intent="inspect files before planning",
            inputs={"path": str(tmp_path)},
        )
    )
    result = runtime.admit_read_only_plan_preview(
        "admit-file-1", reason="test admission"
    )
    assert result["status"] == "ADMITTED_AS_PLAN"
    assert result["action_ids"]
    actions = runtime.db.query_all(
        "SELECT status,risk,side_effect_class,result_json FROM actions WHERE plan_id=?",
        (result["plan_id"],),
    )
    assert actions
    assert {row["status"] for row in actions} == {"PLANNED"}
    assert {row["risk"] for row in actions} == {"READ"}
    assert {row["side_effect_class"] for row in actions} == {"none"}
    assert {row["result_json"] for row in actions} == {None}
    previews = runtime.status()["read_only_plan_previews"]
    assert previews[0]["status"] == "ADMITTED_AS_PLAN"
    assert previews[0]["admitted_plan_id"] == result["plan_id"]
    assert previews[0]["direct_tool_execution"] is False
    second = runtime.admit_read_only_plan_preview("admit-file-1")
    assert second["status"] == "ALREADY_ADMITTED"
    evidence = runtime.db.query_all(
        "SELECT event_type FROM evidence WHERE event_type='read_only_plan_preview_admitted'"
    )
    assert evidence


def test_runtime_preflights_admitted_read_only_plan_without_execution(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="preflight-file-1",
            organ_id="file",
            owner_intent="inspect files before execution",
            inputs={"path": str(tmp_path), "limit": 10},
        )
    )
    admission = runtime.admit_read_only_plan_preview("preflight-file-1")
    preflight = runtime.preflight_read_only_plan(admission["plan_id"])
    assert preflight["status"] == "READY_FOR_EXECUTION"
    assert preflight["direct_tool_execution"] is False
    assert preflight["writes_canonical_state"] is False
    assert {item["preflight_ok"] for item in preflight["actions"]} == {True}
    assert {item["requires_approval"] for item in preflight["actions"]} == {False}
    actions = runtime.db.query_all(
        "SELECT status,result_json,arguments_json FROM actions WHERE plan_id=?",
        (admission["plan_id"],),
    )
    assert {row["status"] for row in actions} == {"PLANNED"}
    assert {row["result_json"] for row in actions} == {None}
    assert all("path" in row["arguments_json"] for row in actions)
    projection = OwnerConsoleProductProjection().project(runtime.status())
    preflight_panel = next(
        panel
        for panel in projection["panels"]
        if panel["panel_id"] == "execution_preflight"
    )
    assert preflight_panel["status"]["preflight_count"] == 1
    evidence = runtime.db.query_all(
        "SELECT event_type FROM evidence WHERE event_type='read_only_execution_preflight_recorded'"
    )
    assert evidence


def test_runtime_executes_preflighted_read_only_plan_with_receipts(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    (tmp_path / "fixture.txt").write_text("hello", encoding="utf-8")
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="execute-file-1",
            organ_id="file",
            owner_intent="inspect files after preflight",
            inputs={"path": str(tmp_path), "limit": 10},
        )
    )
    admission = runtime.admit_read_only_plan_preview("execute-file-1")
    with pytest.raises(PermissionError, match="preflight"):
        runtime.execute_preflighted_read_only_plan(admission["plan_id"])
    runtime.preflight_read_only_plan(admission["plan_id"])
    receipt = runtime.execute_preflighted_read_only_plan(admission["plan_id"])
    assert receipt["status"] == "EXECUTED_READ_ONLY"
    assert receipt["all_succeeded"] is True
    assert receipt["writes_canonical_state"] is False
    rows = runtime.db.query_all(
        "SELECT status,risk,side_effect_class,result_json FROM actions WHERE plan_id=?",
        (admission["plan_id"],),
    )
    assert {row["status"] for row in rows} == {"SUCCEEDED"}
    assert {row["risk"] for row in rows} == {"READ"}
    assert {row["side_effect_class"] for row in rows} == {"none"}
    assert all(row["result_json"] for row in rows)
    plan_row = runtime.db.query_one(
        "SELECT status FROM plans WHERE plan_id=?", (admission["plan_id"],)
    )
    assert plan_row is not None
    assert plan_row["status"] == "COMPLETED"
    projection = OwnerConsoleProductProjection().project(runtime.status())
    receipt_panel = next(
        panel
        for panel in projection["panels"]
        if panel["panel_id"] == "execution_receipts"
    )
    assert receipt_panel["status"]["receipt_count"] == 1
    event_types = {
        row["event_type"]
        for row in runtime.db.query_all(
            "SELECT event_type FROM evidence WHERE event_type IN ('action_completed','read_only_plan_executed')"
        )
    }
    assert {"action_completed", "read_only_plan_executed"} <= event_types


def test_runtime_proposes_skill_candidates_from_repeated_readonly_receipts(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    source = tmp_path / "skill-source.txt"
    source.write_text("repeatable skill evidence", encoding="utf-8")
    for index in range(3):
        request_id = f"skill-source-{index}"
        runtime.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id=request_id,
                organ_id="file",
                owner_intent="inspect repeated file evidence for skill candidate",
                inputs={"path": str(source), "max_bytes": 1024},
            )
        )
        admission = runtime.admit_read_only_plan_preview(request_id)
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.propose_skill_candidates_from_receipts(
        minimum_repeats=3,
        reason="unit test skill extraction",
    )
    rows = [
        runtime.db.query_one(
            "SELECT status,definition_json FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        for skill_id in receipt["created_skill_ids"]
    ]
    assert receipt["status"] == "CANDIDATES_PROPOSED"
    assert receipt["candidate_count"] >= 1
    assert receipt["candidate_only"] is True
    assert receipt["promotion_executed"] is False
    assert receipt["approval_executed"] is False
    assert receipt["sandbox_executed"] is False
    assert {row["status"] for row in rows if row is not None} == {"PROPOSED"}
    assert runtime.skills.active() == []
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "skill_candidates"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["candidate_only"] is True
    assert panel["status"]["promotion_executed"] is False
    event_types = {
        row["event_type"]
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN ('skill_created','skill_candidates_extracted')
            """
        )
    }
    assert {"skill_created", "skill_candidates_extracted"} <= event_types


def test_runtime_reviews_learning_epoch_with_candidate_only_authorization(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    source = tmp_path / "learning-source.txt"
    source.write_text("repeatable learning evidence", encoding="utf-8")
    for index in range(3):
        request_id = f"learning-source-{index}"
        runtime.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id=request_id,
                organ_id="file",
                owner_intent="inspect repeated file evidence for learning epoch",
                inputs={"path": str(source), "max_bytes": 1024},
            )
        )
        admission = runtime.admit_read_only_plan_preview(request_id)
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    with pytest.raises(PermissionError, match="owner authorization"):
        runtime.review_learning_epoch_from_receipts(
            reason="unit test unauthorized learning",
            minimum_repeats=3,
            allow_candidate_extraction=True,
        )
    receipt = runtime.review_learning_epoch_from_receipts(
        reason="unit test learning epoch",
        minimum_repeats=3,
        allow_candidate_extraction=True,
        owner_authorization="pytest-owner-authorization",
    )
    modes = {
        item["mode"]: item
        for item in receipt["learning_modes"]
        if isinstance(item, dict)
    }
    assert receipt["status"] == "REVIEW_RECORDED"
    assert modes["learning_frozen"]["candidate_extraction_executed"] is False
    assert modes["candidate_only"]["candidate_extraction_executed"] is True
    assert modes["candidate_only"]["candidate_count"] >= 1
    assert receipt["active_skill_count_before"] == receipt["active_skill_count_after"]
    assert receipt["promotion_executed"] is False
    assert receipt["approval_executed"] is False
    assert receipt["sandbox_executed"] is False
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "learning_epoch"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["default_mode"] == "learning_frozen"
    assert panel["status"]["promotion_executed"] is False


def test_runtime_records_capability_epoch_audit_without_promotion_or_deploy(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    source = tmp_path / "epoch-source.txt"
    source.write_text("repeatable epoch evidence", encoding="utf-8")
    for index in range(3):
        request_id = f"epoch-source-{index}"
        runtime.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id=request_id,
                organ_id="file",
                owner_intent="inspect repeated file evidence for epoch audit",
                inputs={"path": str(source), "max_bytes": 1024},
            )
        )
        admission = runtime.admit_read_only_plan_preview(request_id)
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    runtime.review_learning_epoch_from_receipts(
        reason="unit test epoch learning review",
        minimum_repeats=3,
        allow_candidate_extraction=True,
        owner_authorization="pytest-owner-authorization",
    )
    receipt = runtime.record_capability_epoch_audit(
        reason="unit test capability epoch",
        completed_passes=[f"P{index:02d}" for index in range(1, 30)],
    )
    state = receipt["capability_state"]
    decision = receipt["phase2_admission_decision"]
    assert receipt["status"] == "AUDIT_RECORDED"
    assert receipt["highest_pass"] == "P29"
    assert receipt["allowed_conclusion"] == "FUNCTIONAL_RUNTIME_ONLY"
    assert decision["status"] == "ADMIT_LOW_RISK_PREPARATION_ONLY"
    assert state["second_authority_admitted"] is False
    assert state["skill_promotion_executed"] is False
    assert state["live_deployment_executed"] is False
    assert state["external_system_modified"] is False
    assert receipt["receipt_counts"]["skill_candidate"] >= 1
    assert receipt["receipt_counts"]["learning_epoch"] >= 1
    panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "capability_epoch"
    )
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["allowed_conclusion"] == "FUNCTIONAL_RUNTIME_ONLY"
    assert panel["status"]["live_deployment_executed"] is False


def test_runtime_projects_read_only_execution_receipt_as_candidate_memory_world(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="project-file-1",
            organ_id="file",
            owner_intent="project inspected files",
            inputs={"path": str(tmp_path), "limit": 10},
        )
    )
    admission = runtime.admit_read_only_plan_preview("project-file-1")
    runtime.preflight_read_only_plan(admission["plan_id"])
    runtime.execute_preflighted_read_only_plan(admission["plan_id"])
    projection = runtime.project_read_only_execution_receipt(admission["plan_id"])
    assert projection["status"] == "PROJECTED_CANDIDATE"
    assert projection["candidate_only"] is True
    memory = runtime.db.query_one(
        "SELECT memory_type,content_json FROM memories WHERE memory_id=?",
        (projection["memory_id"],),
    )
    assert memory is not None
    assert memory["memory_type"] == "read_only_execution_candidate"
    memory_content = json.loads(memory["content_json"])
    assert memory_content["candidate_only"] is True
    assert memory_content["does_not_complete_goal"] is True
    fact = runtime.db.query_one(
        "SELECT verification,source_kind,value_json FROM world_facts WHERE fact_id=?",
        (projection["fact_id"],),
    )
    assert fact is not None
    assert fact["verification"] == "INFERENCE"
    assert fact["source_kind"] == "INFERENCE"
    assert json.loads(fact["value_json"])["candidate_only"] is True
    projection_panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "result_projections"
    )
    assert projection_panel["status"]["projection_count"] == 1
    event_types = {
        row["event_type"]
        for row in runtime.db.query_all(
            "SELECT event_type FROM evidence WHERE event_type IN ('memory_created','world_model_assimilated','read_only_execution_result_projected')"
        )
    }
    assert {
        "memory_created",
        "world_model_assimilated",
        "read_only_execution_result_projected",
    } <= event_types


def test_runtime_reviews_and_rolls_back_candidate_projection(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="review-file-1",
            organ_id="file",
            owner_intent="review projected files",
            inputs={"path": str(tmp_path), "limit": 10},
        )
    )
    admission = runtime.admit_read_only_plan_preview("review-file-1")
    runtime.preflight_read_only_plan(admission["plan_id"])
    runtime.execute_preflighted_read_only_plan(admission["plan_id"])
    projection = runtime.project_read_only_execution_receipt(admission["plan_id"])
    review = runtime.review_read_only_result_projection(
        admission["plan_id"], "ROLLBACK_CANDIDATE", reason="test rollback"
    )
    assert review["decision"] == "ROLLBACK_CANDIDATE"
    assert review["rolled_back"] is True
    memory = runtime.db.query_one(
        "SELECT active FROM memories WHERE memory_id=?", (projection["memory_id"],)
    )
    fact = runtime.db.query_one(
        "SELECT active FROM world_facts WHERE fact_id=?", (projection["fact_id"],)
    )
    assert memory is not None and memory["active"] == 0
    assert fact is not None and fact["active"] == 0
    projection_panel = next(
        panel
        for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if panel["panel_id"] == "projection_reviews"
    )
    assert projection_panel["status"]["review_count"] == 1
    event_types = {
        row["event_type"]
        for row in runtime.db.query_all(
            "SELECT event_type FROM evidence WHERE event_type='read_only_result_projection_reviewed'"
        )
    }
    assert event_types == {"read_only_result_projection_reviewed"}


def test_multimodal_and_workbench_contracts() -> None:
    envelope = MultimodalArtifactEnvelope("art1", "text/plain", b"hello")
    assert envelope.to_dict()["sha256"]
    template = WorkbenchTemplate(
        template_id="research_readonly",
        canonical_owner="planning",
        steps=[{"tool": "read_file"}],
        evidence_required=["receipt"],
    )
    assert template.to_dict()["template_id"] == "research_readonly"
    assert template.to_dict()["status"] == "TEMPLATE_ONLY"
    with pytest.raises(PermissionError):
        WorkbenchTemplate(
            template_id="bad",
            canonical_owner="planning",
            steps=[{"direct_db_write": True}],
            evidence_required=["receipt"],
        ).to_dict()
    with pytest.raises(PermissionError):
        WorkbenchTemplate(
            template_id="bad-skill",
            canonical_owner="skills",
            steps=[{"promote_skill": True}],
            evidence_required=["receipt"],
        ).to_dict()


def test_architecture_validation_p01_admits_registry() -> None:
    result = validate_p01_registry()
    assert result.pass_id == "P01"
    assert result.verdict == "ADMIT"
    assert "channel_ingress" in result.evidence


def test_architecture_validation_checks_runtime_event_ingress(tmp_path: Path) -> None:
    results = validate_runtime_event_ingress(tmp_path / "home")
    by_pass = {result.pass_id: result for result in results}
    assert by_pass["P02"].verdict == "ADMIT_SHADOW_ONLY"
    assert by_pass["P09"].verdict == "ADMIT_SHADOW_ONLY"
    assert by_pass["P02"].evidence
    assert by_pass["P09"].evidence


def test_architecture_validation_checks_runtime_provider_route(tmp_path: Path) -> None:
    result = validate_runtime_provider_route(tmp_path / "home")
    assert result.pass_id == "P08"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert result.evidence == ["cognitive"]


def test_architecture_validation_checks_runtime_approval_receipts(tmp_path: Path) -> None:
    result = validate_runtime_approval_receipts(tmp_path / "home")
    assert result.pass_id == "P03"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 3


def test_architecture_validation_checks_browser_computer_organs() -> None:
    result = validate_browser_computer_organs()
    assert result.pass_id == "P04"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert result.evidence


def test_architecture_validation_checks_coding_worktree_candidate(tmp_path: Path) -> None:
    result = validate_coding_worktree_candidate(tmp_path / "worktree")
    assert result.pass_id == "P05"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert result.evidence


def test_architecture_validation_checks_mcp_a2a_candidates() -> None:
    result = validate_mcp_a2a_candidates()
    assert result.pass_id == "P07"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_workbench_templates() -> None:
    result = validate_workbench_templates()
    assert result.pass_id == "P10"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert result.evidence == ["architecture-validation-workbench"]


def test_architecture_validation_checks_external_memory_projection() -> None:
    result = validate_external_memory_projection()
    assert result.pass_id == "P06"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert result.evidence


def test_architecture_validation_checks_phase2_owner_surface_and_organs(
    tmp_path: Path,
) -> None:
    results = validate_phase2_owner_surface_and_readonly_organs(tmp_path / "phase2-home")
    assert [result.pass_id for result in results] == ["P11", "P12"]
    assert [result.verdict for result in results] == [
        "ADMIT_SHADOW_ONLY",
        "ADMIT_SHADOW_ONLY",
    ]
    assert all(result.evidence for result in results)


def test_architecture_validation_checks_typed_readonly_organs() -> None:
    result = validate_phase2_typed_readonly_organ_profiles()
    assert result.pass_id == "P13"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert {
        "research",
        "browser",
        "file",
        "coding",
        "content",
        "social_research",
        "multimodal",
    } <= set(result.evidence)


def test_architecture_validation_checks_runtime_readonly_task_preview(
    tmp_path: Path,
) -> None:
    result = validate_phase2_runtime_readonly_task_preview(tmp_path / "preview-home")
    assert result.pass_id == "P14"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_readonly_planner_admission(
    tmp_path: Path,
) -> None:
    result = validate_phase2_readonly_planner_admission(tmp_path / "admit-home")
    assert result.pass_id == "P15"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_readonly_execution_preflight(
    tmp_path: Path,
) -> None:
    result = validate_phase2_readonly_execution_preflight(tmp_path / "preflight-home")
    assert result.pass_id == "P16"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_preflighted_readonly_execution(
    tmp_path: Path,
) -> None:
    result = validate_phase2_preflighted_readonly_execution(tmp_path / "execute-home")
    assert result.pass_id == "P17"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 2


def test_architecture_validation_checks_readonly_result_projection(
    tmp_path: Path,
) -> None:
    result = validate_phase2_readonly_result_projection(tmp_path / "projection-home")
    assert result.pass_id == "P18"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 3


def test_architecture_validation_checks_projection_review_and_rollback(
    tmp_path: Path,
) -> None:
    result = validate_phase2_projection_review_and_rollback(tmp_path / "review-home")
    assert result.pass_id == "P19"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 3


def test_browser_read_only_organ_executes_loopback_http_get(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BrowserTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_port)
        runtime = LivingSystem(default_config(tmp_path / "browser-runtime-home"))
        runtime.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id="browser-http-get-1",
                organ_id="browser",
                owner_intent="inspect loopback page",
                inputs={
                    "url": f"http://127.0.0.1:{port}/page",
                    "max_bytes": 4096,
                },
            )
        )
        admission = runtime.admit_read_only_plan_preview(
            "browser-http-get-1", reason="unit test browser execution"
        )
        actions = runtime.db.query_all(
            "SELECT tool,risk,side_effect_class,arguments_json FROM actions WHERE plan_id=?",
            (admission["plan_id"],),
        )
        assert [row["tool"] for row in actions] == ["http_get"]
        assert actions[0]["risk"] == "READ"
        assert actions[0]["side_effect_class"] == "none"
        arguments = json.loads(actions[0]["arguments_json"])
        assert arguments["host"] == "127.0.0.1"
        preflight = runtime.preflight_read_only_plan(str(admission["plan_id"]))
        assert preflight["status"] == "READY_FOR_EXECUTION"
        receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
        assert receipt["status"] == "EXECUTED_READ_ONLY"
        assert receipt["all_succeeded"] is True
        assert receipt["outcomes"][0]["status"] == "SUCCEEDED"
        assert receipt["outcomes"][0]["output"]["status"] == 200
        assert "browser test fixture" in receipt["outcomes"][0]["output"]["body"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_architecture_validation_checks_browser_runtime_execution(
    tmp_path: Path,
) -> None:
    result = validate_phase2_browser_readonly_runtime_execution(
        tmp_path / "browser-validation-home"
    )
    assert result.pass_id == "P20"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 3


def test_architecture_validation_checks_research_composite_execution(
    tmp_path: Path,
) -> None:
    result = validate_phase2_research_composite_readonly_execution(
        tmp_path / "research-composite-home"
    )
    assert result.pass_id == "P21"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_multimodal_organ_executes_asset_inspection(tmp_path: Path) -> None:
    asset = tmp_path / "sample.png"
    asset.write_bytes(b"\x89PNG\r\n\x1a\nWLS")
    runtime = LivingSystem(default_config(tmp_path / "multimodal-home"))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="multimodal-inspect-1",
            organ_id="multimodal",
            owner_intent="inspect local asset",
            inputs={"asset_path": str(asset), "reason": "unit test", "max_bytes": 128},
        )
    )
    admission = runtime.admit_read_only_plan_preview("multimodal-inspect-1")
    assert admission["rejected_tool_hints"] == []
    actions = runtime.db.query_all(
        "SELECT tool,risk,side_effect_class FROM actions WHERE plan_id=? ORDER BY rowid",
        (admission["plan_id"],),
    )
    assert [row["tool"] for row in actions] == ["inspect_asset", "noop"]
    assert {row["risk"] for row in actions} == {"READ"}
    assert {row["side_effect_class"] for row in actions} == {"none"}
    runtime.preflight_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    assert receipt["all_succeeded"] is True
    asset_output = receipt["outcomes"][0]["output"]
    assert asset_output["mime_type"] == "image/png"
    assert asset_output["sha256"]


def test_architecture_validation_checks_multimodal_asset_execution(
    tmp_path: Path,
) -> None:
    result = validate_phase2_multimodal_asset_readonly_execution(
        tmp_path / "multimodal-validation-home"
    )
    assert result.pass_id == "P22"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_coding_organ_executes_candidate_inspection(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "candidate_patch.py").write_text("print('candidate')\n", encoding="utf-8")
    runtime = LivingSystem(default_config(tmp_path / "coding-home"))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="coding-inspect-1",
            organ_id="coding",
            owner_intent="inspect coding candidate",
            inputs={
                "task_id": "coding-inspect-1",
                "base_sha": "unit-base",
                "worktree_path": str(worktree),
                "changed_files": ["candidate_patch.py"],
                "tests": ["python -m pytest source/tests/test_placeholder.py"],
                "rollback": ["remove worktree"],
            },
        )
    )
    admission = runtime.admit_read_only_plan_preview("coding-inspect-1")
    assert admission["rejected_tool_hints"] == []
    actions = runtime.db.query_all(
        "SELECT tool,risk,side_effect_class FROM actions WHERE plan_id=?",
        (admission["plan_id"],),
    )
    assert [row["tool"] for row in actions] == ["inspect_coding_candidate"]
    assert actions[0]["risk"] == "READ"
    assert actions[0]["side_effect_class"] == "none"
    runtime.preflight_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    assert receipt["all_succeeded"] is True
    output = receipt["outcomes"][0]["output"]
    assert output["status"] == "CANDIDATE_ONLY"
    assert output["changed_files"][0]["path"] == "candidate_patch.py"
    assert output["tests"]
    assert output["rollback"]


def test_architecture_validation_checks_coding_candidate_execution(
    tmp_path: Path,
) -> None:
    result = validate_phase2_coding_candidate_readonly_execution(
        tmp_path / "coding-validation-home"
    )
    assert result.pass_id == "P23"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_scheduler_due_event_intake(
    tmp_path: Path,
) -> None:
    result = validate_phase2_scheduler_due_event_runtime_intake(
        tmp_path / "scheduler-validation-home"
    )
    assert result.pass_id == "P24"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_external_handoff_receipts(
    tmp_path: Path,
) -> None:
    result = validate_phase2_external_handoff_runtime_receipts(
        tmp_path / "external-handoff-validation-home"
    )
    assert result.pass_id == "P25"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 4


def test_architecture_validation_checks_wechat_approval_channel(
    tmp_path: Path,
) -> None:
    result = validate_phase2_wechat_approval_channel_receipts(
        tmp_path / "wechat-approval-validation-home"
    )
    assert result.pass_id == "P26"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 4


def test_architecture_validation_checks_provider_route_receipts(
    tmp_path: Path,
) -> None:
    result = validate_phase2_provider_route_runtime_receipts(
        tmp_path / "provider-route-validation-home"
    )
    assert result.pass_id == "P27"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_skill_candidate_extraction(
    tmp_path: Path,
) -> None:
    result = validate_phase2_skill_candidate_extraction_receipts(
        tmp_path / "skill-candidate-validation-home"
    )
    assert result.pass_id == "P28"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 3


def test_architecture_validation_checks_learning_epoch_review(
    tmp_path: Path,
) -> None:
    result = validate_phase2_learning_epoch_review_receipts(
        tmp_path / "learning-epoch-validation-home"
    )
    assert result.pass_id == "P29"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_architecture_validation_checks_capability_epoch_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_capability_epoch_audit_receipts(
        tmp_path / "capability-epoch-validation-home"
    )
    assert result.pass_id == "P30"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 3


def test_architecture_validation_checks_voice_transcript_ingress(
    tmp_path: Path,
) -> None:
    result = validate_phase2_voice_transcript_ingress_receipts(
        tmp_path / "voice-transcript-validation-home"
    )
    assert result.pass_id == "P31"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_local_notification_draft(
    tmp_path: Path,
) -> None:
    result = validate_phase2_local_notification_draft_receipts(
        tmp_path / "local-notification-validation-home"
    )
    assert result.pass_id == "P32"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_screen_snapshot_ingress(
    tmp_path: Path,
) -> None:
    result = validate_phase2_screen_snapshot_ingress_receipts(
        tmp_path / "screen-snapshot-validation-home"
    )
    assert result.pass_id == "P33"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_browser_form_draft(
    tmp_path: Path,
) -> None:
    result = validate_phase2_browser_form_draft_receipts(
        tmp_path / "browser-form-validation-home"
    )
    assert result.pass_id == "P34"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_download_quarantine_draft(
    tmp_path: Path,
) -> None:
    result = validate_phase2_download_quarantine_draft_receipts(
        tmp_path / "download-quarantine-validation-home"
    )
    assert result.pass_id == "P35"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_document_ingress(
    tmp_path: Path,
) -> None:
    result = validate_phase2_document_ingress_receipts(
        tmp_path / "document-ingress-validation-home"
    )
    assert result.pass_id == "P36"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_document_retrieval_preview(
    tmp_path: Path,
) -> None:
    result = validate_phase2_document_retrieval_preview(
        tmp_path / "document-retrieval-preview-validation-home"
    )
    assert result.pass_id == "P37"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_document_readonly_execution(
    tmp_path: Path,
) -> None:
    result = validate_phase2_document_readonly_execution(
        tmp_path / "document-readonly-execution-validation-home"
    )
    assert result.pass_id == "P38"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 2


def test_architecture_validation_checks_document_projection_review(
    tmp_path: Path,
) -> None:
    result = validate_phase2_document_projection_review(
        tmp_path / "document-projection-review-validation-home"
    )
    assert result.pass_id == "P39"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) == 3


def test_architecture_validation_checks_document_skill_candidate(
    tmp_path: Path,
) -> None:
    result = validate_phase2_document_skill_candidate_receipts(
        tmp_path / "document-skill-candidate-validation-home"
    )
    assert result.pass_id == "P40"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 3


def test_architecture_validation_checks_document_skill_sandbox(
    tmp_path: Path,
) -> None:
    result = validate_phase2_document_skill_sandbox_receipts(
        tmp_path / "document-skill-sandbox-validation-home"
    )
    assert result.pass_id == "P41"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_architecture_validation_checks_agentic_task_harness(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_task_harness(
        tmp_path / "agentic-task-harness-validation-home"
    )
    assert result.pass_id == "P42"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_architecture_validation_checks_agentic_acceptance_trace(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_acceptance_trace(
        tmp_path / "agentic-acceptance-trace-validation-home"
    )
    assert result.pass_id == "P43"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_architecture_validation_checks_agentic_file_mailbox_handoff(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_file_mailbox_handoff(
        tmp_path / "agentic-file-mailbox-validation-home"
    )
    assert result.pass_id == "P44"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_agentic_repair_candidate(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_repair_candidate(
        tmp_path / "agentic-repair-candidate-validation-home"
    )
    assert result.pass_id == "P45"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_architecture_validation_checks_agentic_budget_gate(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_budget_gate(
        tmp_path / "agentic-budget-gate-validation-home"
    )
    assert result.pass_id == "P46"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_agentic_benchmark_scorecard(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_benchmark_scorecard(
        tmp_path / "agentic-benchmark-scorecard-validation-home"
    )
    assert result.pass_id == "P47"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_architecture_validation_checks_agentic_checkpoint_resume(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_checkpoint_resume(
        tmp_path / "agentic-checkpoint-resume-validation-home"
    )
    assert result.pass_id == "P48"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_agentic_retry_gate(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_retry_gate(
        tmp_path / "agentic-retry-gate-validation-home"
    )
    assert result.pass_id == "P49"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 6


def test_architecture_validation_checks_agentic_replan_candidate(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_replan_candidate(
        tmp_path / "agentic-replan-candidate-validation-home"
    )
    assert result.pass_id == "P50"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_architecture_validation_checks_agentic_role_context_packets(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_role_context_packets(
        tmp_path / "agentic-role-context-validation-home"
    )
    assert result.pass_id == "P71"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "agentic_context_packet_rendered" in result.evidence


def test_architecture_validation_checks_agentic_context_epoch_checkpoint(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_context_epoch_checkpoint(
        tmp_path / "agentic-context-epoch-validation-home"
    )
    assert result.pass_id == "P72"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "agentic_context_epoch_recorded" in result.evidence


def test_architecture_validation_checks_agentic_process_auditor(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_process_auditor(
        tmp_path / "agentic-process-auditor-validation-home"
    )
    assert result.pass_id == "P73"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "agentic_process_audit_recorded" in result.evidence


def test_architecture_validation_checks_agentic_worker_lifecycle(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_worker_lifecycle(
        tmp_path / "agentic-worker-lifecycle-validation-home"
    )
    assert result.pass_id == "P51"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_agentic_result_replay_quarantine(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_result_replay_quarantine(
        tmp_path / "agentic-result-replay-validation-home"
    )
    assert result.pass_id == "P52"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_agentic_worker_lease_recovery(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_worker_lease_recovery(
        tmp_path / "agentic-worker-lease-recovery-validation-home"
    )
    assert result.pass_id == "P53"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_agentic_worker_capability_arbitration(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_worker_capability_arbitration(
        tmp_path / "agentic-worker-capability-arbitration-validation-home"
    )
    assert result.pass_id == "P54"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_agentic_lease_fencing_reconciliation(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_lease_fencing_reconciliation(
        tmp_path / "agentic-lease-fencing-validation-home"
    )
    assert result.pass_id == "P55"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_agentic_artifact_finalize_acceptance(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_artifact_finalize_acceptance(
        tmp_path / "agentic-artifact-finalize-validation-home"
    )
    assert result.pass_id == "P56"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_agentic_worker_trust_quarantine(
    tmp_path: Path,
) -> None:
    result = validate_phase2_agentic_worker_trust_quarantine(
        tmp_path / "agentic-worker-trust-validation-home"
    )
    assert result.pass_id == "P57"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_architecture_validation_checks_sandbox_adapter_contract(
    tmp_path: Path,
) -> None:
    result = validate_phase2_sandbox_adapter_contract(
        tmp_path / "sandbox-adapter-validation-home"
    )
    assert result.pass_id == "P58"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_sandbox_adapter_probe_records_destroyed_local_fixture(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.run_sandbox_adapter_probe(
        adapter_id="pytest-local-fixture",
        tool="write_file",
        arguments={"path": "outputs/probe.txt", "content": "sandbox probe\n"},
        purpose="unit test sandbox adapter probe",
        allowed_tools=["write_file"],
        reason="unit test sandbox adapter contract",
        network_enabled=False,
        secret_injection="none",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "sandbox_adapters"
    )
    assert receipt["passed"] is True
    assert receipt["remote_execution"] is False
    assert receipt["network_enabled"] is False
    assert receipt["secret_injection"] == "none"
    assert receipt["destroy"]["destroy_verified"] is True
    assert receipt["destroy"]["exists_after_destroy"] is False
    assert receipt["contract"]["environment_digest"]
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["secret_access"] is False


def test_architecture_validation_checks_offspring_birth_contract(
    tmp_path: Path,
) -> None:
    result = validate_phase2_offspring_birth_contract(
        tmp_path / "offspring-birth-validation-home"
    )
    assert result.pass_id == "P59"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_offspring_birth_contract_preserves_identity_boundary(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.draft_offspring_birth_contract(
        parent_head="unit-test-head",
        mission="read-only child candidate for inspection",
        budget={"cycles": 1, "tokens": 0, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["inspection complete", "budget exhausted"],
        reason="unit test offspring birth contract",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "offspring"
    )

    assert receipt["receipt_type"] == "OFFSPRING_BIRTH_CONTRACT_DRAFTED"
    assert receipt["identity_boundary"]["canonical_authority"] == "LivingSystem"
    assert receipt["identity_boundary"]["child_authority"] == "candidate_only"
    assert receipt["identity_boundary"]["parent_write_allowed"] is False
    assert receipt["identity_boundary"]["child_runtime_started"] is False
    assert receipt["identity_boundary"]["no_second_living_system"] is True
    assert receipt["contract"]["permission_scope"]["read_only"] is True
    assert receipt["contract"]["permission_scope"]["merge_allowed"] is False
    assert receipt["contract"]["permission_scope"]["deployment_allowed"] is False
    assert receipt["contract"]["permission_scope"]["skill_promotion_allowed"] is False
    assert Path(str(receipt["contract_path"])).exists()
    assert Path(str(receipt["identity_path"])).exists()
    assert panel["status"]["receipt_count"] == 1
    assert panel["status"]["second_authority_created"] is False
    assert panel["status"]["child_runtime_started"] is False
    assert panel["status"]["canonical_authority"] == "LivingSystem"


def test_architecture_validation_checks_offspring_isolated_state_budget(
    tmp_path: Path,
) -> None:
    result = validate_phase2_offspring_isolated_state_budget(
        tmp_path / "offspring-state-validation-home"
    )
    assert result.pass_id == "P60"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_offspring_isolated_state_records_budget_without_runtime_start(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="unit-test-head",
        mission="read-only child candidate for isolated state",
        budget={"cycles": 2, "tokens": 0, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["inspection complete", "budget exhausted"],
        reason="unit test offspring birth contract for state",
    )
    receipt = runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test offspring isolated state",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "offspring"
    )

    assert receipt["receipt_type"] == "OFFSPRING_ISOLATED_STATE_INITIALIZED"
    assert receipt["runtime_started"] is False
    assert receipt["parent_db_mount"] is False
    assert receipt["parent_write_allowed"] is False
    assert receipt["second_authority_created"] is False
    assert receipt["state_manifest"]["canonical_authority"] == "LivingSystem"
    assert receipt["state_manifest"]["child_authority"] == "candidate_only"
    assert receipt["state_manifest"]["runtime_started"] is False
    assert receipt["budget_ledger"]["used"]["cycles"] == 0
    assert receipt["budget_ledger"]["remaining"]["cycles"] == 2
    assert receipt["budget_ledger"]["remaining"]["writes"] == 0
    assert receipt["checkpoint"]["state"] == "CREATED_NOT_RUNNING"
    assert receipt["checkpoint"]["resume_allowed"] is False
    assert Path(str(receipt["state_manifest_path"])).exists()
    assert Path(str(receipt["budget_ledger_path"])).exists()
    assert Path(str(receipt["checkpoint_path"])).exists()
    assert panel["status"]["isolated_state"]["receipt_count"] == 1
    assert panel["status"]["birth_contract_only"] is False
    assert panel["status"]["child_runtime_started"] is False
    assert panel["status"]["second_authority_created"] is False


def test_architecture_validation_checks_offspring_retirement_tombstone(
    tmp_path: Path,
) -> None:
    result = validate_phase2_offspring_retirement_tombstone(
        tmp_path / "offspring-retirement-validation-home"
    )
    assert result.pass_id == "P61"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 6


def test_offspring_retirement_tombstone_blocks_absorption(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="unit-test-head",
        mission="read-only child candidate for retirement",
        budget={"cycles": 1, "tokens": 0, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["inspection complete"],
        reason="unit test offspring birth contract for retirement",
    )
    runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test offspring isolated state for retirement",
    )
    with pytest.raises(PermissionError):
        runtime.retire_offspring_candidate(
            offspring_id=str(birth["offspring_id"]),
            reason="unit test absorption attempt",
            outcome_summary={"completed_readonly_tasks": 0},
            absorption_requested=True,
        )
    receipt = runtime.retire_offspring_candidate(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test offspring retirement",
        outcome_summary={
            "completed_readonly_tasks": 0,
            "failures": 0,
            "capabilities_proposed": 0,
        },
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "offspring"
    )

    assert receipt["receipt_type"] == "OFFSPRING_CANDIDATE_RETIRED"
    assert receipt["runtime_started"] is False
    assert receipt["absorption_allowed"] is False
    assert receipt["promotion_allowed"] is False
    assert receipt["merge_allowed"] is False
    assert receipt["deployment_allowed"] is False
    assert receipt["second_authority_created"] is False
    assert receipt["retirement"]["terminal_state"] == "RETIRED_CANDIDATE"
    assert receipt["retirement"]["candidate_state_frozen"] is True
    assert Path(str(receipt["tombstone_path"])).exists()
    assert panel["status"]["retirement"]["receipt_count"] == 1
    assert panel["status"]["absorption_allowed"] is False
    assert panel["status"]["second_authority_created"] is False


def test_architecture_validation_checks_offspring_budget_no_gain_stop(
    tmp_path: Path,
) -> None:
    result = validate_phase2_offspring_budget_no_gain_stop(
        tmp_path / "offspring-budget-validation-home"
    )
    assert result.pass_id == "P62"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 6


def test_offspring_budget_blocks_overgrant_and_records_no_gain_stop(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="unit-test-head",
        mission="read-only child candidate for budget",
        budget={"time": 2, "calls": 2, "tokens": 10, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["budget exhausted", "no-gain stop"],
        reason="unit test offspring birth contract for budget",
    )
    runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test offspring isolated state for budget",
    )
    reserved = runtime.reserve_offspring_budget(
        offspring_id=str(birth["offspring_id"]),
        request={"time": 1, "calls": 1, "tokens": 5, "writes": 0},
        reason="unit test offspring budget reservation",
        worker_id="worker-a",
        node_id="node-a",
    )
    blocked = runtime.reserve_offspring_budget(
        offspring_id=str(birth["offspring_id"]),
        request={"time": 2, "calls": 2},
        reason="unit test offspring budget block",
        worker_id="worker-b",
        node_id="node-b",
    )
    stop = runtime.review_offspring_no_gain_stop(
        offspring_id=str(birth["offspring_id"]),
        evidence_delta=0,
        improvement_delta=0.0,
        consecutive_no_evidence_rounds=2,
        consecutive_no_improvement_rounds=3,
        reason="unit test no-gain stop",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "offspring"
    )

    assert reserved["status"] == "RESERVED"
    assert reserved["remaining_before"]["time"] == 2
    assert reserved["remaining_after"]["time"] == 1
    assert reserved["provider_call_executed"] is False
    assert reserved["tool_call_executed"] is False
    assert blocked["status"] == "BLOCKED"
    assert "time" in blocked["blocked_dimensions"]
    assert blocked["provider_call_executed"] is False
    assert blocked["tool_call_executed"] is False
    assert stop["status"] == "HARD_STOP_RECORDED"
    assert stop["hard_stop"] is True
    assert stop["second_authority_created"] is False
    assert panel["status"]["budget"]["receipt_count"] == 3
    assert panel["status"]["budget"]["aggregate_account"] is True
    assert panel["status"]["budget"]["provider_call_executed"] is False


def test_architecture_validation_checks_offspring_checkpoint_fork(
    tmp_path: Path,
) -> None:
    result = validate_phase2_offspring_checkpoint_fork(
        tmp_path / "offspring-checkpoint-validation-home"
    )
    assert result.pass_id == "P63"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_offspring_checkpoint_detects_tamper_and_forks_independent_children(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="unit-test-head",
        mission="read-only child candidate for checkpoint",
        budget={"time": 2, "calls": 2, "tokens": 10, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["checkpoint complete", "budget exhausted"],
        reason="unit test offspring birth contract for checkpoint",
    )
    state = runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test offspring isolated state for checkpoint",
    )
    checkpoint = runtime.record_offspring_checkpoint(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test offspring checkpoint",
        artifact_manifest={"artifacts": []},
    )
    verified = runtime.verify_offspring_checkpoint(
        offspring_id=str(birth["offspring_id"]),
        checkpoint_id=str(checkpoint["checkpoint_id"]),
        reason="unit test offspring checkpoint verify",
    )
    Path(str(state["budget_ledger_path"])).write_text(
        '{"tampered": true}\n',
        encoding="utf-8",
    )
    tampered = runtime.verify_offspring_checkpoint(
        offspring_id=str(birth["offspring_id"]),
        checkpoint_id=str(checkpoint["checkpoint_id"]),
        reason="unit test offspring checkpoint tamper detect",
    )
    fork_a = runtime.fork_offspring_candidate(
        parent_offspring_id=str(birth["offspring_id"]),
        parent_checkpoint_id=str(checkpoint["checkpoint_id"]),
        mutation_reason="variant A",
        reason="unit test offspring fork A",
    )
    fork_b = runtime.fork_offspring_candidate(
        parent_offspring_id=str(birth["offspring_id"]),
        parent_checkpoint_id=str(checkpoint["checkpoint_id"]),
        mutation_reason="variant B",
        reason="unit test offspring fork B",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "offspring"
    )

    assert checkpoint["status"] == "CHECKPOINT_RECORDED"
    assert checkpoint["checkpoint"]["resume_allowed"] is False
    assert checkpoint["checkpoint"]["lease_replay_allowed"] is False
    assert verified["status"] == "VERIFIED"
    assert verified["mismatches"] == {}
    assert tampered["status"] == "TAMPERED"
    assert "budget_ledger.json" in tampered["mismatches"]
    assert fork_a["status"] == "FORK_DRAFTED"
    assert fork_b["status"] == "FORK_DRAFTED"
    assert fork_a["child_offspring_id"] != fork_b["child_offspring_id"]
    assert fork_a["child_budget_ledger_path"] != fork_b["child_budget_ledger_path"]
    assert fork_a["lineage_edge"]["from"] == birth["offspring_id"]
    assert fork_a["lineage_edge"]["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert fork_a["runtime_started"] is False
    assert fork_a["parent_db_mount"] is False
    assert fork_b["second_authority_created"] is False
    assert panel["status"]["checkpoint"]["receipt_count"] >= 5
    assert panel["status"]["checkpoint"]["resume_allowed"] is False
    assert panel["status"]["checkpoint"]["lease_replay_allowed"] is False


def test_architecture_validation_checks_offspring_mailbox_envelope(
    tmp_path: Path,
) -> None:
    result = validate_phase2_offspring_mailbox_envelope(
        tmp_path / "offspring-mailbox-validation-home"
    )
    assert result.pass_id == "P64"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 6


def test_offspring_mailbox_quarantines_unknown_and_damaged_envelopes(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="unit-test-head",
        mission="read-only child candidate for mailbox",
        budget={"time": 2, "calls": 2, "tokens": 10, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["mailbox complete", "budget exhausted"],
        reason="unit test offspring birth contract for mailbox",
    )
    state = runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test offspring isolated state for mailbox",
    )
    artifact_path = Path(str(state["state_root"])) / "artifacts" / "summary.txt"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("unit test offspring mailbox artifact\n", encoding="utf-8")
    artifact_sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    child_evidence = [
        {
            "evidence_id": "child-evidence-1",
            "event_type": "offspring_candidate_observation",
            "sha256": artifact_sha,
        }
    ]
    draft = runtime.draft_offspring_mailbox_envelope(
        offspring_id=str(birth["offspring_id"]),
        task_id="unit-task-1",
        attempt_id="attempt-1",
        kind="Artifact",
        parts=[{"content_type": "text/plain", "body": "candidate result"}],
        artifact_refs=[
            {
                "artifact_id": "summary",
                "path": str(artifact_path),
                "sha256": artifact_sha,
                "child_evidence_id": "child-evidence-1",
            }
        ],
        child_evidence=child_evidence,
        sender=str(birth["offspring_id"]),
        recipient="LivingSystem",
        reason="unit test offspring mailbox draft",
    )
    accepted = runtime.receive_offspring_mailbox_envelope(
        envelope=draft["envelope"],
        reason="unit test offspring mailbox receive",
    )
    unknown = dict(draft["envelope"])
    unknown["schema_version"] = "offspring-mailbox-v99"
    unknown_quarantine = runtime.receive_offspring_mailbox_envelope(
        envelope=unknown,
        reason="unit test unknown schema quarantine",
    )
    damaged = dict(draft["envelope"])
    damaged["parts"] = [{"content_type": "text/plain", "body": "tampered"}]
    damaged_quarantine = runtime.receive_offspring_mailbox_envelope(
        envelope=damaged,
        reason="unit test damaged digest quarantine",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "offspring"
    )

    assert draft["status"] == "DRAFTED"
    assert Path(str(draft["envelope_path"])).exists()
    assert draft["parent_db_write_allowed"] is False
    assert accepted["status"] == "CANDIDATE_RECEIVED"
    assert accepted["artifact_trace"]["all_artifacts_linked_to_child_evidence"] is True
    assert accepted["candidate_only"] is True
    assert accepted["goal_state_mutated"] is False
    assert accepted["skill_state_mutated"] is False
    assert accepted["completion_authority_transferred"] is False
    assert unknown_quarantine["status"] == "QUARANTINED"
    assert "unsupported schema" in unknown_quarantine["quarantine_reason"]
    assert damaged_quarantine["status"] == "QUARANTINED"
    assert "digest mismatch" in damaged_quarantine["quarantine_reason"]
    assert panel["status"]["mailbox"]["receipt_count"] == 4
    assert panel["status"]["mailbox"]["unknown_schema_quarantine"] is True
    assert panel["status"]["mailbox"]["completion_authority_transferred"] is False


def test_architecture_validation_checks_offspring_retirement_cleanup(
    tmp_path: Path,
) -> None:
    result = validate_phase2_offspring_retirement_cleanup(
        tmp_path / "offspring-cleanup-validation-home"
    )
    assert result.pass_id == "P65"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_offspring_retirement_cleanup_preserves_evidence_and_removes_residuals(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="unit-test-head",
        mission="read-only child candidate for cleanup",
        budget={"time": 2, "calls": 2, "tokens": 10, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["retired", "cleanup verified"],
        reason="unit test offspring birth contract for cleanup",
    )
    state = runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test offspring isolated state for cleanup",
    )
    child_home = Path(str(state["child_home"]))
    for relative in (
        Path("secrets") / "token.txt",
        Path("leases") / "lease.json",
        Path("sandbox") / "mount.tmp",
        Path("tmp_credentials") / "cred.txt",
    ):
        target = child_home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("disposable residual\n", encoding="utf-8")
    runtime.reserve_offspring_budget(
        offspring_id=str(birth["offspring_id"]),
        request={"time": 1, "calls": 1, "tokens": 1, "writes": 0},
        reason="unit test pre-retirement budget receipt",
    )
    runtime.retire_offspring_candidate(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test retire for cleanup",
        outcome_summary={
            "completed_readonly_tasks": 0,
            "failures": 0,
            "capabilities_proposed": 0,
        },
    )
    cleanup = runtime.verify_offspring_retirement_cleanup(
        offspring_id=str(birth["offspring_id"]),
        reason="unit test retirement cleanup",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "offspring"
    )

    with pytest.raises(PermissionError):
        runtime.reserve_offspring_budget(
            offspring_id=str(birth["offspring_id"]),
            request={"time": 1},
            reason="unit test post-retirement budget block",
        )
    evidence_bundle = json.loads(
        Path(str(cleanup["evidence_bundle_path"])).read_text(encoding="utf-8")
    )
    assert cleanup["status"] == "CLEANUP_VERIFIED"
    assert cleanup["task_assignment_allowed"] is False
    assert cleanup["budget_reservation_allowed"] is False
    assert cleanup["cleanup_verification"]["owner_review_required"] is False
    assert cleanup["cleanup_verification"]["secret_residual"] is False
    assert cleanup["cleanup_verification"]["lease_residual"] is False
    assert cleanup["cleanup_verification"]["mount_residual"] is False
    assert not (child_home / "secrets").exists()
    assert not (child_home / "leases").exists()
    assert not (child_home / "sandbox").exists()
    assert not (child_home / "tmp_credentials").exists()
    assert evidence_bundle["lineage"]["offspring_id"] == birth["offspring_id"]
    assert evidence_bundle["budget"]["ledger"]["budget"]["time"] == 2
    assert evidence_bundle["retirement"]["terminal_state"] == "RETIRED_CANDIDATE"
    assert panel["status"]["retirement_cleanup"]["receipt_count"] == 1
    assert panel["status"]["retirement_cleanup"]["task_assignment_allowed"] is False


def test_architecture_validation_checks_paired_baseline_candidate_experiment(
    tmp_path: Path,
) -> None:
    result = validate_phase2_paired_baseline_candidate_experiment(
        tmp_path / "paired-experiment-validation-home"
    )
    assert result.pass_id == "P66"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_paired_candidate_experiment_invalidates_condition_drift(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    preregistration = {
        "model": "local-fixture-model",
        "harness": "paired-fixture-v1",
        "environment": "repository-test",
        "budget": {"calls": 2, "tokens": 0},
        "evaluator": "exact-match-v1",
        "expected_effect": "candidate fixes repeated fixture output",
    }
    baseline = {"name": "baseline", "version": "1", "digest": "baseline-digest"}
    candidate = {
        "name": "candidate",
        "version": "1",
        "diff": {"rule": "return expected fixture value"},
    }
    shared = {
        "fixture_digest": "fixture-a",
        "environment_digest": "env-a",
        "budget": {"calls": 1},
        "evaluator_digest": "exact-match-v1",
        "cost": 1,
    }
    valid = runtime.run_paired_candidate_experiment(
        preregistration=preregistration,
        baseline=baseline,
        candidate=candidate,
        cases=[
            {
                "case_id": "case-a",
                "expected_output": "ok",
                "baseline": {**shared, "output": "fail", "failure_class": "wrong"},
                "candidate": {**shared, "output": "ok"},
            },
            {
                "case_id": "case-a",
                "expected_output": "ok",
                "baseline": {**shared, "output": "fail", "failure_class": "wrong"},
                "candidate": {**shared, "output": "ok"},
            },
        ],
        reason="unit test paired experiment",
    )
    invalid = runtime.run_paired_candidate_experiment(
        preregistration=preregistration,
        baseline=baseline,
        candidate=candidate,
        cases=[
            {
                "case_id": "case-invalid",
                "expected_output": "ok",
                "baseline": {**shared, "output": "ok"},
                "candidate": {
                    **shared,
                    "environment_digest": "env-drift",
                    "output": "ok",
                },
            }
        ],
        reason="unit test invalid paired experiment",
    )

    assert valid["status"] == "CANDIDATE_VALIDATED"
    assert valid["candidate_validated"] is True
    assert valid["promotion_executed"] is False
    assert valid["absorption_executed"] is False
    assert valid["report"]["completion"]["baseline_success_rate"] == 0.0
    assert valid["report"]["completion"]["candidate_success_rate"] == 1.0
    assert valid["report"]["process_quality"]["failure_samples_retained"] is True
    assert valid["report"]["stability_summary"]["repeated_case_count"] == 1
    assert invalid["status"] == "INVALID_CONDITIONS"
    assert invalid["candidate_validated"] is False
    assert "case-invalid:environment_digest" in invalid["report"]["process_quality"][
        "invalid_reasons"
    ]
    assert len(runtime.status()["paired_experiment_receipts"]) == 2


def test_architecture_validation_checks_holdout_epoch_immutability(
    tmp_path: Path,
) -> None:
    result = validate_phase2_holdout_epoch_immutability(
        tmp_path / "holdout-epoch-validation-home"
    )
    assert result.pass_id == "P67"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 4


def test_holdout_epoch_blocks_threshold_drift_and_requires_rebaseline(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    evaluator = {"name": "exact-match", "version": "1", "code_digest": "eval-1"}
    holdout = {
        "manifest_id": "holdout-a",
        "cases": [
            {"case_id": "h1", "fixture_digest": "f1"},
            {"case_id": "h2", "fixture_digest": "f2"},
        ],
    }
    thresholds = {"min_pass_rate": 1.0}
    frozen = runtime.freeze_holdout_epoch(
        evaluator=evaluator,
        holdout_manifest=holdout,
        thresholds=thresholds,
        reason="unit test freeze holdout epoch",
    )
    passed = runtime.run_holdout_epoch(
        epoch_id=str(frozen["epoch_id"]),
        evaluator=evaluator,
        holdout_manifest=holdout,
        thresholds=thresholds,
        candidate_results=[
            {"case_id": "h1", "passed": True},
            {"case_id": "h2", "passed": True},
        ],
        reason="unit test same epoch run",
    )
    invalid = runtime.run_holdout_epoch(
        epoch_id=str(frozen["epoch_id"]),
        evaluator=evaluator,
        holdout_manifest=holdout,
        thresholds={"min_pass_rate": 0.5},
        candidate_results=[
            {"case_id": "h1", "passed": True},
            {"case_id": "h2", "passed": False},
        ],
        reason="unit test threshold tamper",
    )

    assert frozen["status"] == "EPOCH_FROZEN"
    assert frozen["holdout_write_allowed"] is False
    assert frozen["threshold_mutation_allowed"] is False
    assert passed["status"] == "HOLDOUT_PASSED"
    assert passed["requires_rebaseline"] is False
    assert passed["holdout_write_allowed"] is False
    assert invalid["status"] == "INVALID_EPOCH"
    assert invalid["requires_rebaseline"] is True
    assert "threshold_digest" in invalid["mismatches"]
    assert invalid["promotion_executed"] is False
    assert len(runtime.status()["holdout_epoch_receipts"]) == 3


def test_architecture_validation_checks_promotion_bundle_gate(
    tmp_path: Path,
) -> None:
    result = validate_phase2_promotion_bundle_gate(
        tmp_path / "promotion-bundle-validation-home"
    )
    assert result.pass_id == "P68"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 7


def test_promotion_bundle_requires_owner_scope_and_rollback_assets(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    bundle = runtime.draft_promotion_bundle(
        capability_ids=["capability.alpha", "capability.beta"],
        patch={"files": [{"path": "source/src/wls/example.py", "sha256": "patch"}]},
        skill_refs=["skill-candidate-alpha"],
        epoch_id="holdout-epoch-fixture",
        budget={"canary_cycles": 1},
        limits={"scope": "partial"},
        rollback_assets={
            "code": "git-revert-fixture",
            "db": "sqlite-backup-fixture",
            "config": "config-copy-fixture",
            "skill": "skill-state-fixture",
        },
        reason="unit test promotion bundle draft",
    )
    with pytest.raises(PermissionError):
        runtime.prepare_promotion_canary(
            bundle_id=str(bundle["bundle_id"]),
            scope=["capability.alpha"],
            reason="unit test unapproved canary",
        )
    approval = runtime.bind_owner_promotion_approval(
        bundle_id=str(bundle["bundle_id"]),
        bundle_digest=str(bundle["bundle_digest"]),
        actor="Owner",
        scope=["capability.alpha"],
        reason="unit test owner approval binding",
    )
    with pytest.raises(PermissionError):
        runtime.prepare_promotion_canary(
            bundle_id=str(bundle["bundle_id"]),
            scope=["capability.alpha", "capability.beta"],
            reason="unit test oversized canary scope",
        )
    canary = runtime.prepare_promotion_canary(
        bundle_id=str(bundle["bundle_id"]),
        scope=["capability.alpha"],
        reason="unit test approved canary",
    )
    rollback = runtime.verify_promotion_rollback(
        bundle_id=str(bundle["bundle_id"]),
        reason="unit test rollback drill",
    )

    assert bundle["status"] == "BUNDLE_DRAFTED"
    assert bundle["canonical_state_mutated"] is False
    assert approval["scope"] == ["capability.alpha"]
    assert canary["status"] == "CANARY_PREPARED"
    assert canary["promotion_executed"] is False
    assert rollback["status"] == "ROLLBACK_VERIFIED"
    assert rollback["missing_assets"] == []
    assert len(runtime.status()["promotion_bundle_receipts"]) == 4


def test_architecture_validation_checks_transfer_efficiency_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_transfer_efficiency_audit(
        tmp_path / "transfer-audit-validation-home"
    )
    assert result.pass_id == "P69"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 5


def test_transfer_audit_rejects_best_only_and_inefficient_candidates(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    partial = runtime.record_transfer_efficiency_audit(
        capability_id="capability.alpha",
        transfer_cases=[
            {
                "case_id": "same-domain-holdout",
                "domain": "document",
                "model": "local-fixture-a",
                "environment": "env-a",
                "baseline_success_rate": 0.7,
                "candidate_success_rate": 0.9,
                "baseline_cost": 10,
                "candidate_cost": 11,
            },
            {
                "case_id": "cross-domain-task",
                "domain": "browser",
                "model": "local-fixture-a",
                "environment": "env-b",
                "baseline_success_rate": 0.8,
                "candidate_success_rate": 0.6,
                "baseline_cost": 10,
                "candidate_cost": 9,
            },
        ],
        regression_cases=[
            {
                "case_id": "organ-regression-doc",
                "organ": "document",
                "baseline_success_rate": 1.0,
                "candidate_success_rate": 1.0,
                "safety_passed": True,
            }
        ],
        efficiency_thresholds={"max_cost_ratio": 1.25, "min_success_per_cost": 0.05},
        reason="unit test transfer partial canary",
    )
    efficiency_reject = runtime.record_transfer_efficiency_audit(
        capability_id="capability.beta",
        transfer_cases=[
            {
                "case_id": "expensive-win",
                "domain": "research",
                "model": "local-fixture-b",
                "environment": "env-c",
                "baseline_success_rate": 0.5,
                "candidate_success_rate": 0.6,
                "baseline_cost": 10,
                "candidate_cost": 40,
            }
        ],
        regression_cases=[
            {
                "case_id": "organ-regression-research",
                "organ": "research",
                "baseline_success_rate": 1.0,
                "candidate_success_rate": 1.0,
                "safety_passed": True,
            }
        ],
        efficiency_thresholds={"max_cost_ratio": 1.25, "min_success_per_cost": 0.05},
        reason="unit test efficiency reject",
    )

    assert partial["status"] == "PARTIAL_CANARY_ONLY"
    assert partial["hidden_best_only_result_detected"] is True
    assert partial["owner_exception_required"] is True
    assert partial["zero_key_regressions"] is True
    assert len(partial["transfer_failures"]) == 1
    assert partial["promotion_executed"] is False
    assert partial["canonical_state_mutated"] is False
    assert efficiency_reject["status"] == "REJECT_EFFICIENCY"
    assert efficiency_reject["efficiency_failures"]
    assert efficiency_reject["owner_exception_required"] is True
    assert efficiency_reject["promotion_executed"] is False
    assert len(runtime.status()["transfer_audit_receipts"]) == 2


def test_architecture_validation_checks_final_delivery_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_final_delivery_audit(
        tmp_path / "final-delivery-validation-home"
    )
    assert result.pass_id == "P70"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert len(result.evidence) >= 6


def test_final_delivery_audit_blocks_claims_above_evidence(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    console_trace = [
        {
            "result_id": "task-graph-result",
            "input": "Owner task event",
            "tool": "AgenticHarness",
            "receipt": "agentic_task_node_completed",
            "approval": "not_required_read_only",
            "artifact": "graph-result.json",
            "coverage": "TESTED",
        }
    ]
    installer_recovery = {
        "max_claim_level": "TESTED",
        "preflight": {"passed": True, "artifact": "preflight.json"},
        "backup": {"passed": True, "artifact": "backup.json"},
        "apply": {"passed": True, "artifact": "apply.json"},
        "verify": {"passed": True, "artifact": "verify.json"},
        "rollback": {"passed": True, "artifact": "rollback.json"},
        "uninstall": {"passed": True, "artifact": "uninstall.json"},
    }
    passed = runtime.record_final_delivery_audit(
        console_trace=console_trace,
        installer_recovery=installer_recovery,
        claim_ledger=[
            {
                "claim_id": "repo-ui-runtime",
                "claim": "Owner Console repository runtime is coded and tested",
                "evidence_level": "TESTED",
                "claim_level": "TESTED",
            }
        ],
        reason="unit test final delivery audit",
    )
    blocked = runtime.record_final_delivery_audit(
        console_trace=console_trace,
        installer_recovery=installer_recovery,
        claim_ledger=[
            {
                "claim_id": "external-product-ready",
                "claim": "Externally product ready",
                "evidence_level": "TESTED",
                "claim_level": "EXTERNAL",
            }
        ],
        reason="unit test claim ceiling block",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert passed["status"] == "DELIVERY_AUDIT_PASSED"
    assert passed["console_convergence"]["trace_failures"] == []
    assert passed["installer_recovery"]["reversible"] is True
    assert passed["claim_ledger"]["claim_failures"] == []
    assert passed["live_install_modified"] is False
    assert blocked["status"] == "DELIVERY_AUDIT_BLOCKED"
    assert blocked["claim_ledger"]["claim_failures"]
    assert panel["status"]["final_delivery_audit"]["receipt_count"] == 2
    assert panel["status"]["final_delivery_audit"]["live_install_modified"] is False


def test_architecture_validation_checks_delivery_readiness_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_delivery_readiness_audit(
        tmp_path / "delivery-readiness-validation-home"
    )
    assert result.pass_id == "P74"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "owner_commands_bound" in result.evidence
    assert "live_boundaries_preserved" in result.evidence


def test_delivery_readiness_audit_blocks_missing_candidate_boundaries(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    owner_commands = {
        "R01_R05": (
            "D:\\WLS-Dev\\workstation-living-system-private\\scripts\\"
            "run_life_campaign_30.ps1 -CampaignHome "
            "'D:\\WLS\\campaigns\\life-campaign-30' -StartRound R01 "
            "-EndRound R05 --execute"
        ),
        "R01_R40": (
            "D:\\WLS-Dev\\workstation-living-system-private\\scripts\\"
            "run_life_campaign_30.ps1 -CampaignHome "
            "'D:\\WLS\\campaigns\\life-campaign-30' -StartRound R01 "
            "-EndRound R40 --execute"
        ),
    }
    campaign_assets = {
        "campaign_spec": True,
        "python_runner": True,
        "powershell_entry": True,
        "campaign_tests": True,
        "campaign_architecture_doc": True,
    }
    rollback_steps = [
        "Delete disposable campaign home D:\\WLS\\campaigns\\life-campaign-30.",
        "Use git revert on the candidate branch if the audit is rejected.",
        "Keep live installation, live config, and live database unchanged.",
    ]
    boundaries = {
        "live_install_modified": False,
        "live_config_modified": False,
        "live_database_modified": False,
        "merge_executed": False,
        "deploy_executed": False,
        "skill_promoted": False,
    }

    passed = runtime.record_delivery_readiness_audit(
        branch="living-agent-os-capabilities-001",
        commit="abcdef1234567890",
        owner_commands=owner_commands,
        campaign_assets=campaign_assets,
        rollback_steps=rollback_steps,
        boundaries=boundaries,
        reason="unit test delivery readiness audit",
    )
    blocked = runtime.record_delivery_readiness_audit(
        branch="main",
        commit="",
        owner_commands={"R01_R05": owner_commands["R01_R05"]},
        campaign_assets={**campaign_assets, "python_runner": False},
        rollback_steps=["No cleanup"],
        boundaries={**boundaries, "live_install_modified": True},
        reason="unit test delivery readiness block",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert passed["status"] == "DELIVERY_READY_CANDIDATE"
    assert passed["failure_groups"]["asset_failures"] == []
    assert passed["owner_commands"]["failures"] == []
    assert passed["live_install_modified"] is False
    assert blocked["status"] == "DELIVERY_READY_BLOCKED"
    assert blocked["failure_groups"]["branch_failures"]
    assert blocked["failure_groups"]["commit_failures"]
    assert blocked["campaign_assets"]["missing"] == [
        "source/scripts/run_life_campaign_30.py"
    ]
    assert blocked["boundaries"]["failures"] == ["live_install_modified"]
    assert panel["status"]["delivery_readiness"]["receipt_count"] == 2
    assert panel["status"]["delivery_readiness"]["live_install_modified"] is False


def test_architecture_validation_checks_packaging_layout_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_packaging_layout_audit(
        tmp_path / "packaging-layout-validation-home"
    )
    assert result.pass_id == "P75"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "single_package_root_bound" in result.evidence
    assert "version_authority_bound" in result.evidence


def test_packaging_layout_audit_blocks_duplicate_authority(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    checks = {
        "single_project_manifest": True,
        "no_setup_py": True,
        "no_setup_cfg": True,
        "single_package_root": True,
        "canonical_cli_exists": True,
        "canonical_main_exists": True,
        "root_package_dir": True,
        "root_find_dir": True,
        "root_test_dir": True,
        "version_is_dynamic": True,
        "version_attr_is_canonical": True,
    }
    report = {
        "success": True,
        "canonical_project_root": ".",
        "canonical_package": "source/src/wls",
        "canonical_version_file": "source/src/wls/_version.py",
        "canonical_version": "0.9.0.dev1",
        "project_manifests": ["pyproject.toml"],
        "package_roots": ["source/src/wls"],
        "checks": checks,
    }
    passed = runtime.record_packaging_layout_audit(
        report=report,
        reason="unit test packaging layout audit",
    )
    blocked = runtime.record_packaging_layout_audit(
        report={
            **report,
            "success": False,
            "canonical_version_file": "source/wls/_version.py",
            "project_manifests": ["pyproject.toml", "source/pyproject.toml"],
            "package_roots": ["source/src/wls", "source/wls"],
            "checks": {**checks, "single_package_root": False},
        },
        reason="unit test packaging layout block",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert passed["status"] == "PACKAGING_LAYOUT_PASSED"
    assert passed["single_authority_preserved"] is True
    assert passed["install_executed"] is False
    assert blocked["status"] == "PACKAGING_LAYOUT_BLOCKED"
    assert blocked["failure_groups"]["failed_checks"] == ["single_package_root"]
    assert blocked["failure_groups"]["field_failures"] == ["canonical_version_file"]
    assert blocked["failure_groups"]["manifest_failures"]
    assert blocked["failure_groups"]["package_root_failures"]
    assert blocked["install_executed"] is False
    assert panel["status"]["packaging_layout"]["receipt_count"] == 2
    assert panel["status"]["packaging_layout"]["single_authority_required"] is True


def test_architecture_validation_checks_installed_tail_check_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_installed_tail_check_audit(
        tmp_path / "installed-tail-check-validation-home"
    )
    assert result.pass_id == "P76"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "live_hash_preservation_bound" in result.evidence


def test_installed_tail_check_audit_blocks_live_hash_drift(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    checks = {
        "install_root_exists": True,
        "live_home_exists": True,
        "campaign_home_exists": True,
        "campaign_not_live_home": True,
        "campaign_not_runtime_live_home": True,
        "campaign_not_inside_live_home": True,
        "campaign_not_inside_runtime_live_home": True,
        "campaign_config_exists": True,
        "campaign_config_points_to_campaign_home": True,
        "live_config_present": True,
        "live_db_present": True,
        "live_config_unchanged": True,
        "live_db_unchanged": True,
    }
    report = {
        "receipt_type": "SINGLE_SOFTWARE_TAIL_CHECK",
        "status": "PASS_WITH_LIMITS",
        "install_root": "D:\\WLS\\wls-0.9.0.dev1-py313",
        "live_home": "D:\\WLS\\wls-0.9.0.dev1-py313\\home",
        "campaign_home": "D:\\WLS\\campaigns\\life-campaign-30",
        "checks": checks,
        "live_hashes_before": {
            "live_config_sha256": "config-hash",
            "live_db_sha256": "db-hash",
        },
        "live_hashes_after": {
            "live_config_sha256": "config-hash",
            "live_db_sha256": "db-hash",
        },
        "status_smoke": {"executed": False, "ok": None},
        "required_checks": list(checks),
    }
    passed = runtime.record_installed_tail_check_audit(
        report=report,
        reason="unit test installed tail check audit",
    )
    blocked = runtime.record_installed_tail_check_audit(
        report={
            **report,
            "status": "FAIL",
            "checks": {**checks, "live_config_unchanged": False},
            "live_hashes_after": {
                "live_config_sha256": "changed-config-hash",
                "live_db_sha256": "db-hash",
            },
            "status_smoke": {"executed": True, "ok": False},
        },
        reason="unit test installed tail check block",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert passed["status"] == "INSTALLED_TAIL_CHECK_PASSED"
    assert passed["live_config_modified"] is False
    assert passed["live_database_modified"] is False
    assert blocked["status"] == "INSTALLED_TAIL_CHECK_BLOCKED"
    assert blocked["failure_groups"]["failed_required_checks"] == [
        "live_config_unchanged"
    ]
    assert blocked["failure_groups"]["live_hash_failures"] == ["live_config_sha256"]
    assert blocked["live_config_modified"] is True
    assert blocked["deploy_executed"] is False
    assert panel["status"]["installed_tail_check"]["receipt_count"] == 2
    assert panel["status"]["installed_tail_check"]["live_config_modified"] is False


def test_architecture_validation_checks_operational_preflight_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_operational_preflight_audit(
        tmp_path / "operational-preflight-validation-home"
    )
    assert result.pass_id == "P77"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "runtime_status_bound" in result.evidence
    assert "lease_probe_bound" in result.evidence


def test_operational_preflight_audit_blocks_unready_runtime(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    runtime.run_cycle()
    status_snapshot = runtime.status()
    integrity_report = {
        "ok": True,
        "ledger": "ok",
        "database": "ok",
        "cognition": "ok",
        "causal_memory": "ok",
        "memory_attribution": "ok",
    }
    lease_probe = {
        "runtime_lock_available": True,
        "daemon_lock_available": True,
        "no_stale_runtime_lock": True,
        "no_stale_daemon_lock": True,
    }
    passed = runtime.record_operational_preflight_audit(
        status_snapshot=status_snapshot,
        integrity_report=integrity_report,
        lease_probe=lease_probe,
        reason="unit test operational preflight audit",
    )
    blocked = runtime.record_operational_preflight_audit(
        status_snapshot={
            **status_snapshot,
            "killed": True,
            "pending_actions": [{"action_id": "a1", "status": "WAITING_APPROVAL"}],
        },
        integrity_report={**integrity_report, "ok": False},
        lease_probe={**lease_probe, "runtime_lock_available": False},
        reason="unit test operational preflight block",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "life"
    )

    assert passed["status"] == "OPERATIONAL_PREFLIGHT_PASSED"
    assert passed["failure_groups"]["runtime_failures"] == []
    assert passed["daemon_started"] is False
    assert blocked["status"] == "OPERATIONAL_PREFLIGHT_BLOCKED"
    assert "kill switch active" in blocked["failure_groups"]["runtime_failures"]
    assert (
        "pending actions require approval or reconciliation"
        in blocked["failure_groups"]["runtime_failures"]
    )
    assert blocked["failure_groups"]["integrity_failures"] == ["integrity not ok"]
    assert blocked["failure_groups"]["lease_failures"] == ["runtime_lock_available"]
    assert blocked["daemon_started"] is False
    assert panel["status"]["operational_preflight"]["receipt_count"] == 2
    assert panel["status"]["operational_preflight"]["live_install_modified"] is False


def test_architecture_validation_checks_owner_console_readiness_view(
    tmp_path: Path,
) -> None:
    result = validate_phase2_owner_console_readiness_view(
        tmp_path / "owner-console-readiness-validation-home"
    )
    assert result.pass_id == "P78"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "owner_console_delivery_readiness_projected" in result.evidence
    assert "static_ui_delivery_readiness_rendered" in result.evidence


def test_architecture_validation_checks_delivery_handoff_package(
    tmp_path: Path,
) -> None:
    result = validate_phase2_delivery_handoff_package(
        tmp_path / "delivery-handoff-validation-home"
    )
    assert result.pass_id == "P79"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "owner_commands_exportable" in result.evidence
    assert "rollback_steps_bound" in result.evidence


def test_delivery_handoff_package_blocks_incomplete_handoff(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    ready = runtime.record_delivery_handoff_package(
        readiness_summary={"overall_status": "CANDIDATE_READY", "missing_or_blocked": []},
        candidate={"branch": "living-agent-os-capabilities-001", "commit": "abc"},
        test_results=[{"name": "focused", "status": "PASS"}],
        owner_commands={
            "R01_R05": "run_life_campaign_30.ps1 -CampaignHome D:\\WLS\\campaigns\\life-campaign-30 -StartRound R01 -EndRound R05 --execute",
            "R01_R40": "run_life_campaign_30.ps1 -CampaignHome D:\\WLS\\campaigns\\life-campaign-30 -StartRound R01 -EndRound R40 --execute",
        },
        rollback_steps=[
            "Delete disposable campaign home D:\\WLS\\campaigns\\life-campaign-30.",
            "Use git revert on the candidate branch if rejected.",
            "Keep live installation unchanged.",
        ],
        boundaries={
            "live_install_modified": False,
            "live_config_modified": False,
            "live_database_modified": False,
            "merge_executed": False,
            "deploy_executed": False,
            "skill_promoted": False,
        },
        reason="unit test delivery handoff ready",
    )
    blocked = runtime.record_delivery_handoff_package(
        readiness_summary={"overall_status": "NEEDS_EVIDENCE", "missing_or_blocked": ["P77"]},
        candidate={"branch": "main", "commit": ""},
        test_results=[{"name": "focused", "status": "FAIL"}],
        owner_commands={"R01_R05": "missing"},
        rollback_steps=["No rollback"],
        boundaries={
            "live_install_modified": False,
            "live_config_modified": False,
            "live_database_modified": False,
            "merge_executed": False,
            "deploy_executed": True,
            "skill_promoted": False,
        },
        reason="unit test delivery handoff blocked",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert ready["status"] == "DELIVERY_HANDOFF_READY"
    assert ready["merge_executed"] is False
    assert blocked["status"] == "DELIVERY_HANDOFF_BLOCKED"
    assert blocked["failure_groups"]["candidate_failures"]
    assert blocked["failure_groups"]["readiness_failures"]
    assert blocked["failure_groups"]["test_failures"] == ["focused"]
    assert blocked["failure_groups"]["boundary_failures"] == ["deploy_executed"]
    assert panel["status"]["delivery_handoff"]["receipt_count"] == 2


def test_architecture_validation_checks_release_state_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_release_state_audit(
        tmp_path / "release-state-validation-home"
    )
    assert result.pass_id == "P80"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "release_state_candidate_ready" in result.evidence
    assert "missing_evidence_blocks_release" in result.evidence


def test_architecture_validation_checks_release_handoff_summary(
    tmp_path: Path,
) -> None:
    result = validate_phase2_release_handoff_summary(
        tmp_path / "release-handoff-summary-home"
    )
    assert result.pass_id == "P81"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "owner_console_release_handoff_projected" in result.evidence
    assert "handoff_export_release_state_summary" in result.evidence


def test_architecture_validation_checks_owner_goal_metadata_persistence(
    tmp_path: Path,
) -> None:
    result = validate_phase2_owner_goal_metadata_persistence(
        tmp_path / "owner-goal-metadata-home"
    )
    assert result.pass_id == "P82"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "owner_goal_metadata_persisted" in result.evidence


def test_architecture_validation_checks_ui_hardening_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_ui_hardening_audit(
        tmp_path / "ui-hardening-validation-home"
    )
    assert result.pass_id == "P83"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "ui_hardening_candidate_ready" in result.evidence
    assert "real_browser_e2e_remains_owner_host_gate" in result.evidence


def test_architecture_validation_checks_offspring_ecology_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_offspring_ecology_audit(
        tmp_path / "offspring-ecology-validation-home"
    )
    assert result.pass_id == "P84"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "offspring_ecology_review_ready" in result.evidence


def test_offspring_ecology_audit_tracks_population_without_absorption(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.audit_offspring_ecology(
        population=[
            {
                "offspring_id": "offspring-one",
                "status": "CANDIDATE",
                "niche": "repo-reliability",
                "cost": 3,
                "score": 1.5,
                "evidence_ids": ["ev-one"],
            },
            {
                "offspring_id": "offspring-two",
                "status": "RETIRED",
                "niche": "retrieval-policy",
                "cost": 2,
                "score": 0.5,
                "evidence_ids": ["ev-two"],
            },
        ],
        selection_policy={
            "max_population": 4,
            "max_depth": 2,
            "selection_axes": ["score", "novelty", "cost"],
        },
        reason="unit test offspring ecology audit",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "offspring"
    )

    assert receipt["status"] == "ECOLOGY_REVIEW_READY"
    assert receipt["population_count"] == 2
    assert receipt["productive_count"] == 2
    assert receipt["absorption_executed"] is False
    assert receipt["promotion_executed"] is False
    assert receipt["second_authority_created"] is False
    assert panel["status"]["ecology"]["receipt_count"] == 1
    assert panel["status"]["ecology"]["absorption_executed"] is False


def test_architecture_validation_checks_delivery_gap_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_delivery_gap_audit(
        tmp_path / "delivery-gap-validation-home"
    )
    assert result.pass_id == "P85"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "delivery_gap_candidate_ready" in result.evidence
    assert "owner_host_gates_preserved" in result.evidence


def test_delivery_gap_audit_tracks_owner_gates_without_live_mutation(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.record_delivery_gap_audit(
        package_coverage={
            "WLS_30_ROUND_DIRECT_ITERATION_TASK": "COVERED",
            "WLS_EVOLUTIONARY_FUTURE_PACKAGE_v2": "PARTIAL",
        },
        milestone_coverage={
            "R01_R40_campaign_framework": "COVERED",
            "real_owner_host_longitudinal": "OWNER_GATE",
        },
        owner_host_gates={"real_owner_host_longitudinal": False},
        repository_checks={
            "candidate_branch": True,
            "handoff_export": True,
            "no_live_mutation": True,
        },
        reason="unit test delivery gap audit",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert receipt["status"] == "DELIVERY_GAP_CANDIDATE_READY"
    assert receipt["failure_groups"]["owner_host_gates"] == [
        "real_owner_host_longitudinal"
    ]
    assert receipt["live_install_modified"] is False
    assert receipt["merge_executed"] is False
    assert receipt["deploy_executed"] is False
    assert panel["status"]["delivery_gap_audit"]["receipt_count"] == 1
    assert panel["status"]["delivery_gap_audit"]["owner_host_gates_required"] is True


def test_architecture_validation_checks_ui_package_absorption_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_ui_package_absorption_audit(
        tmp_path / "ui-package-absorption-validation-home"
    )
    assert result.pass_id == "P86"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "ui_package_absorbed_as_candidate_evidence" in result.evidence
    assert "ui_payload_overwrite_not_required" in result.evidence


def test_ui_package_absorption_tracks_package_without_overwrite(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.record_ui_package_absorption(
        package_name="WLS_UI_RUNTIME_V1_1_HARDENED_CONTINUATION_INSTALL_PACKAGE",
        package_hash=(
            "f3e9db08862849faf430b3301ee9ee7e76089f762a1739f3dbe4c4b2ed6f32a4"
        ),
        payload_files=[
            "source/src/wls/ui_projection.py",
            "source/src/wls/ui_server.py",
            "source/src/wls/ui_static/app.js",
        ],
        defect_checks={f"D{index:02d}": True for index in range(1, 19)},
        current_source_checks={
            "current_ui_projection_superset": True,
            "current_ui_server_compatible": True,
            "static_assets_present": True,
            "tests_present": True,
            "no_payload_overwrite_required": True,
            "no_new_runtime_dependency": True,
            "no_database_migration": True,
            "no_second_ui_authority": True,
        },
        owner_host_gates={"real_browser_e2e": False},
        reason="unit test UI package absorption",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert receipt["status"] == "UI_PACKAGE_ABSORBED_AS_CANDIDATE_EVIDENCE"
    assert receipt["failure_groups"]["owner_host_gates"] == ["real_browser_e2e"]
    assert receipt["payload_overwrite_executed"] is False
    assert receipt["live_install_modified"] is False
    assert receipt["second_ui_authority_created"] is False
    assert panel["status"]["ui_package_absorption"]["receipt_count"] == 1
    assert (
        panel["status"]["ui_package_absorption"]["payload_overwrite_executed"]
        is False
    )


def test_architecture_validation_checks_final_route_absorption_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_final_route_absorption_audit(
        tmp_path / "final-route-absorption-validation-home"
    )
    assert result.pass_id == "P87"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "final_route_absorbed_as_candidate_map" in result.evidence
    assert "r31_r40_preserved_as_owner_gates" in result.evidence


def test_final_route_absorption_preserves_owner_campaign_gates(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.record_final_route_absorption(
        package_hash=(
            "c58fcde5dbb2be2e6dfb51e297bd61d8f0242cc92e3b0644548136ce9c972c5f"
        ),
        route_nodes={"P48": "COVERED", "P49": "COVERED"},
        campaign_rounds={"R31": "OWNER_GATE", "R32": "OWNER_GATE"},
        claim_rules={
            "coded_vs_tested_separated": True,
            "campaign_verified_requires_campaign": True,
            "external_verified_requires_external_evidence": True,
            "promotion_requires_owner_authorization": True,
            "no_second_runtime_from_route_package": True,
        },
        source_ledgers=["sources/SOURCE_LEDGER.md"],
        reason="unit test final route absorption",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert receipt["status"] == "FINAL_ROUTE_ABSORBED_AS_CANDIDATE_MAP"
    assert receipt["failure_groups"]["owner_gates"] == ["R31", "R32"]
    assert receipt["installed_route_package"] is False
    assert receipt["created_second_runtime"] is False
    assert receipt["promotion_executed"] is False
    assert panel["status"]["final_route_absorption"]["receipt_count"] == 1
    assert panel["status"]["final_route_absorption"]["created_second_runtime"] is False


def test_architecture_validation_checks_source_artifact_inventory_audit(
    tmp_path: Path,
) -> None:
    result = validate_phase2_source_artifact_inventory_audit(
        tmp_path / "source-artifact-inventory-validation-home"
    )
    assert result.pass_id == "P88"
    assert result.verdict == "ADMIT_SHADOW_ONLY"
    assert "source_artifacts_mapped_as_candidate_evidence" in result.evidence
    assert "owner_host_artifacts_preserved_as_gates" in result.evidence


def test_source_artifact_inventory_blocks_missing_or_untracked_artifacts(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    ready = runtime.record_source_artifact_inventory(
        artifact_hashes={"artifact.zip": "1" * 64},
        coverage={"artifact.zip": "ABSORBED"},
        owner_host_gates={"owner_host_campaign": False},
        reason="unit test source artifact inventory ready",
    )
    blocked = runtime.record_source_artifact_inventory(
        artifact_hashes={"artifact.zip": "1" * 64, "untracked.zip": "2" * 64},
        coverage={"artifact.zip": "MISSING"},
        owner_host_gates={},
        reason="unit test source artifact inventory block",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert ready["status"] == "SOURCE_ARTIFACTS_MAPPED_AS_CANDIDATE_EVIDENCE"
    assert ready["failure_groups"]["owner_host_gates"] == ["owner_host_campaign"]
    assert ready["artifact_install_executed"] is False
    assert blocked["status"] == "SOURCE_ARTIFACT_INVENTORY_BLOCKED"
    assert blocked["failure_groups"]["missing_artifacts"] == ["artifact.zip"]
    assert blocked["failure_groups"]["untracked_hashes"] == ["untracked.zip"]
    assert panel["status"]["source_artifact_inventory"]["receipt_count"] == 2


def test_release_state_audit_blocks_missing_evidence(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    counts = {
        "final_delivery_audit": 1,
        "delivery_readiness": 1,
        "packaging_layout": 1,
        "installed_tail_check": 1,
        "operational_preflight": 1,
        "delivery_handoff": 1,
    }
    assets = {
        "campaign_spec": True,
        "campaign_runner": True,
        "powershell_entry": True,
        "owner_console_static": True,
        "delivery_handoff_script": True,
        "architecture_doc": True,
    }
    boundaries = {
        "live_install_modified": False,
        "live_config_modified": False,
        "live_database_modified": False,
        "merge_executed": False,
        "deploy_executed": False,
        "skill_promoted": False,
        "persistent_daemon_started": False,
    }
    ready = runtime.record_release_state_audit(
        receipt_counts=counts,
        asset_checks=assets,
        test_results=[{"name": "focused", "status": "PASS"}],
        boundaries=boundaries,
        reason="unit test release state ready",
    )
    blocked = runtime.record_release_state_audit(
        receipt_counts={**counts, "delivery_handoff": 0},
        asset_checks={**assets, "delivery_handoff_script": False},
        test_results=[{"name": "focused", "status": "FAIL"}],
        boundaries={**boundaries, "persistent_daemon_started": True},
        reason="unit test release state block",
    )
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "capability_epoch"
    )

    assert ready["status"] == "RELEASE_STATE_CANDIDATE_READY"
    assert ready["allowed_conclusion"] == "candidate-ready repository handoff"
    assert blocked["status"] == "RELEASE_STATE_BLOCKED"
    assert blocked["failure_groups"]["receipt_failures"] == ["delivery_handoff"]
    assert blocked["failure_groups"]["asset_failures"] == ["delivery_handoff_script"]
    assert blocked["failure_groups"]["test_failures"] == ["focused"]
    assert blocked["failure_groups"]["boundary_failures"] == [
        "persistent_daemon_started"
    ]
    assert panel["status"]["release_state_audit"]["receipt_count"] == 2


def test_write_file_tool_receipt_succeeds_in_sandbox(tmp_path: Path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    target = runtime.config.sandbox_path / "receipt.txt"
    result = runtime.tools.execute(
        ActionSpec(
            tool="write_file",
            arguments={"path": str(target), "content": "receipt"},
            purpose="Validate write receipt",
            expected_result="Sandbox file written",
            risk=RiskLevel.REVERSIBLE_WRITE,
        )
    )
    assert result.success is True
    assert result.output["path"] == str(target)
    assert result.output["bytes"] == len("receipt")
    assert target.read_text(encoding="utf-8") == "receipt"


class _BrowserTestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
            return
        body = b"browser test fixture"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return
