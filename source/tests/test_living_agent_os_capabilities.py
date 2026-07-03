from __future__ import annotations

from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    validate_phase2_agentic_task_harness,
    validate_external_memory_projection,
    validate_mcp_a2a_candidates,
    validate_phase2_capability_epoch_audit_receipts,
    validate_phase2_coding_candidate_readonly_execution,
    validate_phase2_browser_readonly_runtime_execution,
    validate_phase2_external_handoff_runtime_receipts,
    validate_phase2_multimodal_asset_readonly_execution,
    validate_phase2_owner_surface_and_readonly_organs,
    validate_phase2_preflighted_readonly_execution,
    validate_phase2_projection_review_and_rollback,
    validate_phase2_provider_route_runtime_receipts,
    validate_phase2_readonly_result_projection,
    validate_phase2_runtime_readonly_task_preview,
    validate_phase2_readonly_planner_admission,
    validate_phase2_readonly_execution_preflight,
    validate_phase2_research_composite_readonly_execution,
    validate_phase2_scheduler_due_event_runtime_intake,
    validate_phase2_skill_candidate_extraction_receipts,
    validate_phase2_learning_epoch_review_receipts,
    validate_phase2_local_notification_draft_receipts,
    validate_phase2_screen_snapshot_ingress_receipts,
    validate_phase2_typed_readonly_organ_profiles,
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
