from __future__ import annotations

from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from wls.a2a_adapter import A2AAdapter, ArtifactEnvelope, TaskContract, payload_digest
from wls.architecture_validation import (
    validate_browser_computer_organs,
    validate_coding_worktree_candidate,
    validate_external_memory_projection,
    validate_mcp_a2a_candidates,
    validate_phase2_owner_surface_and_readonly_organs,
    validate_phase2_typed_readonly_organ_profiles,
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
        "goals",
        "actions_approval",
        "memory_world",
        "evolution_lab",
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
    with pytest.raises(PermissionError, match="forbidden tool"):
        ReadOnlyOrganProfile(
            organ_id="bad",
            canonical_owner="planning",
            tool_hints=("write_file",),
            evidence_required=("receipt",),
            planner_contract="bad",
        ).to_dict()


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
