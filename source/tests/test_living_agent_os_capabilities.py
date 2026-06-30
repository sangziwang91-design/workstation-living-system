from __future__ import annotations

from pathlib import Path

import pytest

from wls.a2a_adapter import A2AAdapter, ArtifactEnvelope, TaskContract
from wls.architecture_validation import validate_p01_registry, validate_runtime_event_ingress
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
from wls.mcp_adapter import McpCandidate, McpTrustGate
from wls.multimodal import MultimodalArtifactEnvelope
from wls.provider_router import ProviderDescriptor, ProviderRouter, RouteRequest
from wls.runtime import LivingSystem
from wls.scheduler import EventScheduler, ScheduledEvent
from wls.ui_projection import OwnerConsoleProjection
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
    assert {"events", "tools", "planning", "evidence", "evolution"} <= owners


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


def test_computer_use_blocks_without_sandbox() -> None:
    with pytest.raises(PermissionError, match="sandbox"):
        ComputerUseAdapter().admit(ComputerUseContract(target="desktop"))


def test_coding_worktree_contract_is_candidate_only(tmp_path: Path) -> None:
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
    result = A2AAdapter().receive(
        contract,
        ArtifactEnvelope(task_id="t1", artifact_type="report", payload={"ok": True}),
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
    with pytest.raises(PermissionError):
        WorkbenchTemplate(
            template_id="bad",
            canonical_owner="planning",
            steps=[{"direct_db_write": True}],
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
