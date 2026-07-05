from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import hashlib
import json
import threading

from .a2a_adapter import A2AAdapter, ArtifactEnvelope, TaskContract, payload_digest
from .agentic_harness import AgenticHarness
from .agentic_mailbox import AgenticFileMailbox, ResultEnvelope
from .browser_adapter import BrowserReadOnlyAdapter, BrowserReadOnlyRequest
from .capabilities import baseline_registry
from .channel_gateway import ChannelMessage
from .computer_adapter import ComputerUseAdapter, ComputerUseContract
from .config import default_config
from .coding_adapter import CodingTaskContract
from .external_memory import ExternalMemoryCandidate, ExternalMemoryProjection
from .mcp_adapter import McpCandidate, McpTrustGate
from .read_only_organs import (
    ORGAN_PROFILES,
    ReadOnlyOrganProfile,
    ReadOnlyTaskRequest,
)
from .runtime import LivingSystem
from .scheduler import ScheduledEvent
from .schemas import ActionSpec, ActionStatus, MemoryItem, Plan, RiskLevel, utc_now
from .task_graph import TaskNodeStatus
from .ui_projection import OwnerConsoleProductProjection, UIProjection
from .wechat_adapter import WeChatW0W1Adapter
from .worker_registry import WorkerProfile
from .workbench import WorkbenchTemplate


ALLOWED_VERDICTS = {
    "ADMIT",
    "ADMIT_SHADOW_ONLY",
    "ADMIT_DESIGN_ONLY",
    "REJECT",
    "BLOCKED",
}


@dataclass(slots=True)
class ArchitecturePassResult:
    validation_pass_id: str
    verdict: str
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.verdict not in ALLOWED_VERDICTS:
            raise ValueError(f"invalid verdict: {self.verdict}")

    @property
    def pass_id(self) -> str:
        return self.validation_pass_id

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pass_id"] = data.pop("validation_pass_id")
        return data


def validate_p01_registry() -> ArchitecturePassResult:
    registry = baseline_registry()
    registry.assert_no_duplicate_authority()
    return ArchitecturePassResult(
        "P01",
        "ADMIT",
        [item["capability_id"] for item in registry.list()],
        ["registry is an adapter/projection map; canonical owners remain existing WLS classes"],
    )


def validate_runtime_event_ingress(home: Path) -> list[ArchitecturePassResult]:
    runtime = LivingSystem(default_config(home))
    channel_id, channel_inserted = runtime.ingest_channel_message(
        ChannelMessage("owner_console", "owner", "status?", "validation-channel-1")
    )
    scheduled_id, scheduled_inserted = runtime.emit_scheduled_event(
        ScheduledEvent(
            schedule_id="validation-schedule-1",
            event_type="scheduled.read_only_check",
            payload={"target": "status"},
            due_at="2026-06-30T00:00:00+00:00",
        )
    )
    rows = runtime.db.query_all(
        "SELECT event_id,event_type,source FROM events WHERE event_id IN (?,?)",
        (channel_id, scheduled_id),
    )
    if not channel_inserted or not scheduled_inserted or len(rows) != 2:
        return [
            ArchitecturePassResult(
                "P02",
                "BLOCKED",
                [],
                ["runtime EventStore integration did not persist both events"],
            ),
            ArchitecturePassResult(
                "P09",
                "BLOCKED",
                [],
                ["runtime channel ingress did not persist through EventStore"],
            ),
        ]
    return [
        ArchitecturePassResult(
            "P02",
            "ADMIT_SHADOW_ONLY",
            [scheduled_id],
            ["scheduler is admitted as runtime Event source; durable restart experiment remains future gate"],
        ),
        ArchitecturePassResult(
            "P09",
            "ADMIT_SHADOW_ONLY",
            [channel_id],
            ["channel ingress reaches canonical EventStore through LivingSystem"],
        ),
    ]


def validate_runtime_provider_route(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    route = runtime.planner.route_summary()
    if route.get("provider_id") != runtime.planner.provider_type:
        return ArchitecturePassResult(
            "P08",
            "BLOCKED",
            [],
            ["Planner provider route does not match configured planner provider"],
        )
    evidence = route.get("evidence", {})
    provider = evidence.get("provider", {}) if isinstance(evidence, dict) else {}
    if provider.get("remote") or provider.get("paid"):
        return ArchitecturePassResult(
            "P08",
            "BLOCKED",
            [],
            ["Default planner route is not local/free"],
        )
    return ArchitecturePassResult(
        "P08",
        "ADMIT_SHADOW_ONLY",
        [str(route.get("provider_id", "UNKNOWN"))],
        ["Planner owns provider routing evidence; route is local-first and free by default"],
    )


def validate_runtime_approval_receipts(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    approved_action = _insert_waiting_write_action(
        runtime,
        runtime.config.sandbox_path / "approved-note.json",
        "approved action",
    )
    approval_id = runtime.approvals.issue(
        approved_action.action_id,
        approve=True,
        ttl_minutes=5,
        reason="architecture validation approval",
    )
    envelope = runtime.approvals.envelope(approval_id)
    outcome = runtime.resume_action(approved_action.action_id)
    replay_allowed = runtime.approvals.validate_and_consume(approved_action, approval_id)

    expired_action = _insert_waiting_write_action(
        runtime,
        runtime.config.sandbox_path / "expired-note.json",
        "expired action",
    )
    expired_approval = runtime.approvals.issue(
        expired_action.action_id,
        approve=True,
        ttl_minutes=5,
        reason="architecture validation expiry",
    )
    runtime.db.execute(
        "UPDATE approvals SET expires_at=? WHERE approval_id=?",
        ("2000-01-01T00:00:00+00:00", expired_approval),
    )
    expired_allowed = runtime.approvals.validate_and_consume(
        expired_action, expired_approval
    )

    completed = runtime.db.query_one(
        "SELECT result_json,status FROM actions WHERE action_id=?",
        (approved_action.action_id,),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type FROM evidence
        WHERE event_type IN ('approval_issued','approval_consumed','action_completed')
        ORDER BY seq
        """
    )
    event_types = [str(row["event_type"]) for row in evidence]
    if (
        not outcome.get("success")
        or replay_allowed
        or expired_allowed
        or envelope.get("consumed_at") is not None
        or not envelope.get("nonce")
        or not envelope.get("signature")
        or completed is None
        or completed["status"] != "SUCCEEDED"
        or "approval_issued" not in event_types
        or "approval_consumed" not in event_types
        or "action_completed" not in event_types
    ):
        return ArchitecturePassResult(
            "P03",
            "BLOCKED",
            [approved_action.action_id, approval_id],
            ["approval/tool receipt runtime validation failed"],
        )
    return ArchitecturePassResult(
        "P03",
        "ADMIT_SHADOW_ONLY",
        [approved_action.action_id, approval_id, expired_approval],
        [
            "Approval envelope has nonce/expiry/signature; exact approval executes once; replay and expired approval are rejected; action receipt is evidence-bound",
        ],
    )


def validate_browser_computer_organs() -> ArchitecturePassResult:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BrowserFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        adapter = BrowserReadOnlyAdapter()
        receipt = adapter.fetch_text(
            BrowserReadOnlyRequest(
                url=f"http://127.0.0.1:{port}/page",
                allowed_hosts={"127.0.0.1"},
                session_digest="architecture-validation-session",
                max_bytes=4096,
            )
        )
        redirect_blocked = False
        try:
            adapter.fetch_text(
                BrowserReadOnlyRequest(
                    url=f"http://127.0.0.1:{port}/redirect",
                    allowed_hosts={"127.0.0.1"},
                    session_digest="architecture-validation-session",
                )
            )
        except Exception:
            redirect_blocked = True
        external_blocked = False
        try:
            adapter.fetch_text(
                BrowserReadOnlyRequest(
                    url="https://example.com/",
                    allowed_hosts={"127.0.0.1"},
                    session_digest="architecture-validation-session",
                )
            )
        except PermissionError:
            external_blocked = True
        computer_blocked = False
        try:
            ComputerUseAdapter().admit(ComputerUseContract(target="desktop"))
        except PermissionError:
            computer_blocked = True
        if (
            receipt.status_code != 200
            or not receipt.text_sha256
            or receipt.downloads
            or not redirect_blocked
            or not external_blocked
            or not computer_blocked
        ):
            return ArchitecturePassResult(
                "P04",
                "BLOCKED",
                [receipt.to_dict().get("text_sha256", "")],
                ["browser/computer organ validation failed"],
            )
        return ArchitecturePassResult(
            "P04",
            "ADMIT_SHADOW_ONLY",
            [receipt.text_sha256],
            [
                "Loopback browser read-only receipt captured; redirect and external host blocked; computer use remains sandbox-blocked",
            ],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def validate_coding_worktree_candidate(worktree: Path) -> ArchitecturePassResult:
    worktree.mkdir(parents=True, exist_ok=True)
    candidate = worktree / "candidate_patch.py"
    candidate.write_text(
        "def candidate_value() -> str:\n    return 'candidate-only'\n",
        encoding="utf-8",
    )
    contract = CodingTaskContract(
        task_id="architecture-validation-coding",
        base_sha="fixture-base-sha",
        worktree=worktree.resolve(),
        changed_files=["candidate_patch.py"],
        tests=["pytest source/tests/test_living_agent_os_capabilities.py"],
        rollback=[f"remove disposable worktree {worktree}"],
    )
    receipt = contract.candidate_receipt()
    escaped_blocked = False
    try:
        CodingTaskContract(
            task_id="architecture-validation-escape",
            base_sha="fixture-base-sha",
            worktree=worktree.resolve(),
            changed_files=["..\\escaped.py"],
            tests=["pytest"],
            rollback=["remove disposable worktree"],
        ).candidate_receipt()
    except ValueError:
        escaped_blocked = True
    if (
        receipt.status != "CANDIDATE_ONLY"
        or not receipt.changed_files
        or not receipt.changed_files[0].get("sha256")
        or not escaped_blocked
    ):
        return ArchitecturePassResult(
            "P05",
            "BLOCKED",
            [worktree.as_posix()],
            ["coding worktree candidate validation failed"],
        )
    return ArchitecturePassResult(
        "P05",
        "ADMIT_SHADOW_ONLY",
        [receipt.changed_files[0]["sha256"]],
        [
            "Disposable coding worktree produced a candidate-only receipt with file hash, tests, rollback, and path-escape rejection",
        ],
    )


def validate_mcp_a2a_candidates() -> ArchitecturePassResult:
    gate = McpTrustGate()
    unpinned_blocked = False
    try:
        gate.admit(McpCandidate("validation-mcp", None, "stdio"))
    except PermissionError:
        unpinned_blocked = True
    mcp_result = gate.admit(
        McpCandidate(
            server_id="validation-mcp",
            identity_digest="sha256:architecture-validation",
            transport="stdio",
            side_effect_class="none",
            review_status="REVIEWED",
        )
    )
    contract = TaskContract(
        task_id="architecture-validation-a2a",
        objective="inspect candidate artifact",
        scope={"paths": []},
        allowed_outputs=["report"],
        expires_at="2026-07-01T00:00:00+00:00",
    )
    payload = {"finding": "candidate-only", "confidence": "local-fixture"}
    digest = payload_digest(payload)
    a2a_result = A2AAdapter().receive(
        contract,
        ArtifactEnvelope(
            task_id=contract.task_id,
            artifact_type="report",
            payload=payload,
            hashes={"payload_sha256": digest},
        ),
    )
    canonical_blocked = False
    try:
        A2AAdapter().receive(
            contract,
            ArtifactEnvelope(
                task_id=contract.task_id,
                artifact_type="report",
                payload=payload,
                candidate_only=False,
                hashes={"payload_sha256": digest},
            ),
        )
    except PermissionError:
        canonical_blocked = True
    hash_blocked = False
    try:
        A2AAdapter().receive(
            contract,
            ArtifactEnvelope(
                task_id=contract.task_id,
                artifact_type="report",
                payload=payload,
                hashes={"payload_sha256": "sha256:wrong"},
            ),
        )
    except PermissionError:
        hash_blocked = True
    if (
        not unpinned_blocked
        or mcp_result.get("status") != "VALIDATED_CANDIDATE"
        or a2a_result.get("status") != "CANDIDATE_ONLY"
        or not canonical_blocked
        or not hash_blocked
    ):
        return ArchitecturePassResult(
            "P07",
            "BLOCKED",
            [],
            ["MCP/A2A candidate validation failed"],
        )
    return ArchitecturePassResult(
        "P07",
        "ADMIT_SHADOW_ONLY",
        [str(mcp_result["candidate"]["identity_digest"]), digest],
        [
            "Pinned reviewed MCP identity and hashed A2A artifact are admitted only as candidates; unpinned, canonical-claim, and hash-mismatch paths are rejected",
        ],
    )


def validate_workbench_templates() -> ArchitecturePassResult:
    template = WorkbenchTemplate(
        template_id="architecture-validation-workbench",
        canonical_owner="planning",
        steps=[
            {
                "action": "prepare_candidate_plan",
                "uses_authority": "planning",
                "output": "candidate_plan",
            },
            {
                "action": "record_evidence_requirement",
                "uses_authority": "evidence",
                "output": "receipt_request",
            },
        ],
        evidence_required=["candidate_plan_hash", "owner_review_receipt"],
    )
    admitted = template.to_dict()
    db_blocked = False
    try:
        WorkbenchTemplate(
            template_id="bad-db",
            canonical_owner="planning",
            steps=[{"direct_db_write": True}],
            evidence_required=["receipt"],
        ).to_dict()
    except PermissionError:
        db_blocked = True
    promotion_blocked = False
    try:
        WorkbenchTemplate(
            template_id="bad-skill",
            canonical_owner="skills",
            steps=[{"promote_skill": True}],
            evidence_required=["receipt"],
        ).to_dict()
    except PermissionError:
        promotion_blocked = True
    unknown_owner_blocked = False
    try:
        WorkbenchTemplate(
            template_id="bad-owner",
            canonical_owner="new_runtime",
            steps=[{"action": "plan"}],
            evidence_required=["receipt"],
        ).to_dict()
    except ValueError:
        unknown_owner_blocked = True
    if (
        admitted.get("status") != "TEMPLATE_ONLY"
        or not db_blocked
        or not promotion_blocked
        or not unknown_owner_blocked
    ):
        return ArchitecturePassResult(
            "P10",
            "BLOCKED",
            [],
            ["workbench template authority validation failed"],
        )
    return ArchitecturePassResult(
        "P10",
        "ADMIT_SHADOW_ONLY",
        [str(admitted["template_id"])],
        [
            "Workbench templates bind to existing planning/evolution/skills authorities, require evidence, and reject direct DB writes, Skill promotion, and unknown owners",
        ],
    )


def validate_external_memory_projection() -> ArchitecturePassResult:
    projection = ExternalMemoryProjection()
    receipt = projection.admit(
        ExternalMemoryCandidate(
            source_id="architecture-validation-external-memory",
            source_digest="sha256:external-source",
            content={
                "summary": "candidate observation from external memory source",
                "claim_ceiling": "candidate-only",
            },
            evidence_hashes=["sha256:evidence"],
            tags=["shadow"],
        )
    )
    unpinned_blocked = False
    try:
        projection.admit(
            ExternalMemoryCandidate(
                source_id="bad-unpinned",
                source_digest="external-source",
                content={"summary": "bad"},
                evidence_hashes=["sha256:evidence"],
            )
        )
    except PermissionError:
        unpinned_blocked = True
    canonical_blocked = False
    try:
        projection.admit(
            ExternalMemoryCandidate(
                source_id="bad-canonical",
                source_digest="sha256:external-source",
                content={"summary": "bad"},
                evidence_hashes=["sha256:evidence"],
                candidate_only=False,
            )
        )
    except PermissionError:
        canonical_blocked = True
    store_field_blocked = False
    try:
        projection.admit(
            ExternalMemoryCandidate(
                source_id="bad-store-field",
                source_digest="sha256:external-source",
                content={"memory_id": "mem_external", "summary": "bad"},
                evidence_hashes=["sha256:evidence"],
            )
        )
    except PermissionError:
        store_field_blocked = True
    if (
        receipt.status != "CANDIDATE_ONLY"
        or not receipt.content_sha256
        or not unpinned_blocked
        or not canonical_blocked
        or not store_field_blocked
    ):
        return ArchitecturePassResult(
            "P06",
            "BLOCKED",
            [],
            ["external memory projection validation failed"],
        )
    return ArchitecturePassResult(
        "P06",
        "ADMIT_SHADOW_ONLY",
        [receipt.content_sha256],
        [
            "External memory is admitted only as a pinned, evidence-hashed candidate projection; canonical MemoryStore fields and canonical claims are rejected",
        ],
    )


def validate_phase2_owner_surface_and_readonly_organs(
    home: Path,
) -> list[ArchitecturePassResult]:
    runtime = LivingSystem(default_config(home))
    projection = OwnerConsoleProductProjection().project(runtime.status())
    notification = WeChatW0W1Adapter("W1").console_digest_notification(projection)
    panel_ids = set(projection.get("panel_ids", []))
    required_panels = {
        "life",
        "attention",
        "provider_routes",
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
    }
    if (
        projection.get("mode") != "READ_ONLY_PROJECTION"
        or projection.get("writes_canonical_state")
        or projection.get("direct_tool_execution")
        or not required_panels <= panel_ids
        or notification.get("direct_tool_execution")
        or notification.get("writes_canonical_state")
    ):
        console_result = ArchitecturePassResult(
            "P11",
            "BLOCKED",
            [],
            ["Owner Console product projection or WeChat digest contract failed"],
        )
    else:
        console_result = ArchitecturePassResult(
            "P11",
            "ADMIT_SHADOW_ONLY",
            [str(projection["projection_digest"])],
            [
                "Owner Console product panels and WeChat W0/W1 digest are read-only projections with no canonical writes or tool execution",
            ],
        )

    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    request = ReadOnlyTaskRequest(
        request_id="phase2-readonly-research-1",
        organ_id="research",
        owner_intent="Summarize current Phase 1 evidence",
        inputs={"scope": "campaign evidence"},
    )
    receipt = request.submit(runtime.events)
    template = request.to_workbench_template()
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    side_effect_blocked = False
    try:
        ReadOnlyTaskRequest(
            request_id="bad-write",
            organ_id="research",
            owner_intent="write somewhere",
            side_effect_class="external",
        ).to_event()
    except PermissionError:
        side_effect_blocked = True
    if (
        receipt.status != "QUEUED_EVENT_ONLY"
        or not receipt.inserted
        or receipt.writes_canonical_state
        or receipt.direct_tool_execution
        or template.get("status") != "TEMPLATE_ONLY"
        or before_actions is None
        or after_actions is None
        or before_goals is None
        or after_goals is None
        or before_actions["count"] != after_actions["count"]
        or before_goals["count"] != after_goals["count"]
        or not side_effect_blocked
    ):
        organ_result = ArchitecturePassResult(
            "P12",
            "BLOCKED",
            [str(receipt.event_id)],
            ["Read-only real-task organ contract failed"],
        )
    else:
        organ_result = ArchitecturePassResult(
            "P12",
            "ADMIT_SHADOW_ONLY",
            [str(receipt.event_id), str(template["template_id"])],
            [
                "Read-only task organ submits only a canonical Event and planning template; it creates no Actions or Goals and blocks side effects",
            ],
        )
    return [console_result, organ_result]


def validate_phase2_typed_readonly_organ_profiles() -> ArchitecturePassResult:
    evidence: list[str] = []
    for organ_id, profile in ORGAN_PROFILES.items():
        profile.to_dict()
        candidate = ReadOnlyTaskRequest(
            request_id=f"typed-{organ_id}",
            organ_id=organ_id,
            owner_intent=f"Prepare read-only {organ_id} work",
            inputs={"fixture": organ_id},
        ).to_plan_candidate()
        actions = candidate.get("candidate_actions", [])
        if (
            candidate.get("status") != "PLAN_CANDIDATE_ONLY"
            or candidate.get("canonical_owner") != "Planner"
            or candidate.get("writes_canonical_state")
            or candidate.get("direct_tool_execution")
            or not actions
            or any(action.get("risk") != "READ" for action in actions)
            or "event_receipt" not in candidate.get("evidence_required", [])
        ):
            return ArchitecturePassResult(
                "P13",
                "BLOCKED",
                evidence,
                [f"typed read-only organ profile failed: {organ_id}"],
            )
        evidence.append(str(candidate["organ_id"]))
    blocked_write_tool = False
    try:
        ReadOnlyOrganProfile(
            organ_id="bad",
            canonical_owner="planning",
            tool_hints=("write_file",),
            evidence_required=("receipt",),
            planner_contract="bad",
        ).to_dict()
    except PermissionError:
        blocked_write_tool = True
    if not blocked_write_tool:
        return ArchitecturePassResult(
            "P13",
            "BLOCKED",
            evidence,
            ["forbidden tool profile was not rejected"],
        )
    return ArchitecturePassResult(
        "P13",
        "ADMIT_SHADOW_ONLY",
        evidence,
        [
            "Typed read-only organ profiles produce Planner-owned plan candidates for common Agent abilities and reject forbidden write tool hints",
        ],
    )


def validate_phase2_runtime_readonly_task_preview(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    result = runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="phase2-preview-file-1",
            organ_id="file",
            owner_intent="Inspect a disposable folder",
            inputs={"path": str(home)},
        )
    )
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    evidence = runtime.db.query_all(
        """
        SELECT evidence_id FROM evidence
        WHERE event_type='read_only_plan_preview_recorded'
        ORDER BY seq DESC LIMIT 1
        """
    )
    projection = OwnerConsoleProductProjection().project(runtime.status())
    preview_panel = next(
        (
            panel
            for panel in projection["panels"]
            if panel.get("panel_id") == "task_previews"
        ),
        None,
    )
    preview = result.get("preview", {})
    if (
        before_plans is None
        or after_plans is None
        or before_actions is None
        or after_actions is None
        or before_plans["count"] != after_plans["count"]
        or before_actions["count"] != after_actions["count"]
        or preview.get("status") != "PREVIEW_ONLY"
        or preview.get("creates_plan_row")
        or preview.get("creates_action_row")
        or not evidence
        or preview_panel is None
        or preview_panel["status"]["preview_count"] < 1
    ):
        return ArchitecturePassResult(
            "P14",
            "BLOCKED",
            [str(result)],
            ["runtime read-only task preview validation failed"],
        )
    return ArchitecturePassResult(
        "P14",
        "ADMIT_SHADOW_ONLY",
        [str(result["receipt"]["event_id"]), str(evidence[0]["evidence_id"])],
        [
            "Runtime read-only task intake records an Event, evidence-bound preview, and Owner Console task preview without creating Plan or Action rows",
        ],
    )


def validate_phase2_readonly_planner_admission(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="phase2-admit-file-1",
            organ_id="file",
            owner_intent="Inspect a disposable folder",
            inputs={"path": str(home)},
        )
    )
    result = runtime.admit_read_only_plan_preview(
        "phase2-admit-file-1", reason="architecture validation admission"
    )
    plan = runtime.db.query_one(
        "SELECT status FROM plans WHERE plan_id=?", (result["plan_id"],)
    )
    actions = runtime.db.query_all(
        "SELECT status,risk,side_effect_class FROM actions WHERE plan_id=?",
        (result["plan_id"],),
    )
    executed = [row for row in actions if row["status"] != "PLANNED"]
    evidence = runtime.db.query_all(
        """
        SELECT evidence_id FROM evidence
        WHERE event_type='read_only_plan_preview_admitted'
        ORDER BY seq DESC LIMIT 1
        """
    )
    previews = runtime.status()["read_only_plan_previews"]
    if (
        result.get("status") != "ADMITTED_AS_PLAN"
        or plan is None
        or plan["status"] != "PLANNED"
        or not actions
        or executed
        or any(row["risk"] != "READ" for row in actions)
        or any(row["side_effect_class"] != "none" for row in actions)
        or not evidence
        or previews[0].get("status") != "ADMITTED_AS_PLAN"
        or previews[0].get("direct_tool_execution")
    ):
        return ArchitecturePassResult(
            "P15",
            "BLOCKED",
            [str(result)],
            ["read-only Planner admission validation failed"],
        )
    return ArchitecturePassResult(
        "P15",
        "ADMIT_SHADOW_ONLY",
        [str(result["plan_id"]), str(evidence[0]["evidence_id"])],
        [
            "Read-only preview admission creates a PLANNED Plan with only READ/none Actions, records evidence, updates Owner Console preview state, and executes nothing",
        ],
    )


def validate_phase2_readonly_execution_preflight(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="phase2-preflight-file-1",
            organ_id="file",
            owner_intent="Inspect a disposable folder",
            inputs={"path": str(home)},
        )
    )
    admission = runtime.admit_read_only_plan_preview(
        "phase2-preflight-file-1", reason="architecture validation preflight"
    )
    preflight = runtime.preflight_read_only_plan(str(admission["plan_id"]))
    actions = runtime.db.query_all(
        "SELECT status,result_json FROM actions WHERE plan_id=?",
        (admission["plan_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT evidence_id FROM evidence
        WHERE event_type='read_only_execution_preflight_recorded'
        ORDER BY seq DESC LIMIT 1
        """
    )
    projection = OwnerConsoleProductProjection().project(runtime.status())
    preflight_panel = next(
        (
            panel
            for panel in projection["panels"]
            if panel.get("panel_id") == "execution_preflight"
        ),
        None,
    )
    if (
        preflight.get("status") != "READY_FOR_EXECUTION"
        or preflight.get("direct_tool_execution")
        or preflight.get("writes_canonical_state")
        or not preflight.get("actions")
        or any(not item.get("preflight_ok") for item in preflight["actions"])
        or any(row["status"] != "PLANNED" for row in actions)
        or any(row["result_json"] is not None for row in actions)
        or not evidence
        or preflight_panel is None
        or preflight_panel["status"]["preflight_count"] < 1
    ):
        return ArchitecturePassResult(
            "P16",
            "BLOCKED",
            [str(preflight)],
            ["read-only execution preflight validation failed"],
        )
    return ArchitecturePassResult(
        "P16",
        "ADMIT_SHADOW_ONLY",
        [str(admission["plan_id"]), str(evidence[0]["evidence_id"])],
        [
            "Execution preflight reuses PolicyEngine against admitted READ/none Actions, records evidence, exposes Owner Console state, and executes nothing",
        ],
    )


def validate_phase2_preflighted_readonly_execution(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="phase2-execute-file-1",
            organ_id="file",
            owner_intent="Inspect a disposable folder",
            inputs={"path": str(home), "limit": 5},
        )
    )
    admission = runtime.admit_read_only_plan_preview(
        "phase2-execute-file-1", reason="architecture validation execution"
    )
    runtime.preflight_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    plan = runtime.db.query_one(
        "SELECT status FROM plans WHERE plan_id=?", (admission["plan_id"],)
    )
    actions = runtime.db.query_all(
        "SELECT status,risk,side_effect_class,result_json FROM actions WHERE plan_id=?",
        (admission["plan_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type IN ('action_completed','read_only_plan_executed')
        ORDER BY seq
        """
    )
    projection = OwnerConsoleProductProjection().project(runtime.status())
    receipt_panel = next(
        (
            panel
            for panel in projection["panels"]
            if panel.get("panel_id") == "execution_receipts"
        ),
        None,
    )
    event_types = {row["event_type"] for row in evidence}
    if (
        receipt.get("status") != "EXECUTED_READ_ONLY"
        or not receipt.get("all_succeeded")
        or receipt.get("writes_canonical_state")
        or plan is None
        or plan["status"] != "COMPLETED"
        or not actions
        or any(row["status"] != "SUCCEEDED" for row in actions)
        or any(row["risk"] != "READ" for row in actions)
        or any(row["side_effect_class"] != "none" for row in actions)
        or any(row["result_json"] is None for row in actions)
        or not {"action_completed", "read_only_plan_executed"} <= event_types
        or receipt_panel is None
        or receipt_panel["status"]["receipt_count"] < 1
    ):
        return ArchitecturePassResult(
            "P17",
            "BLOCKED",
            [str(receipt)],
            ["preflighted read-only execution validation failed"],
        )
    return ArchitecturePassResult(
        "P17",
        "ADMIT_SHADOW_ONLY",
        [
            str(admission["plan_id"]),
            *[str(row["evidence_id"]) for row in evidence[-2:]],
        ],
        [
            "Preflighted read-only execution runs only READY READ/none Actions through the existing executor, records action and plan receipts, and exposes Owner Console evidence",
        ],
    )


def validate_phase2_readonly_result_projection(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="phase2-project-file-1",
            organ_id="file",
            owner_intent="Inspect and project a disposable folder result",
            inputs={"path": str(home), "limit": 5},
        )
    )
    admission = runtime.admit_read_only_plan_preview(
        "phase2-project-file-1", reason="architecture validation projection"
    )
    runtime.preflight_read_only_plan(str(admission["plan_id"]))
    runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    projection = runtime.project_read_only_execution_receipt(str(admission["plan_id"]))
    memory = runtime.db.query_one(
        "SELECT memory_type,content_json,active FROM memories WHERE memory_id=?",
        (projection["memory_id"],),
    )
    fact = runtime.db.query_one(
        "SELECT verification,source_kind,value_json FROM world_facts WHERE fact_id=?",
        (projection["fact_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type IN (
            'memory_created',
            'world_model_assimilated',
            'read_only_execution_result_projected'
        )
        ORDER BY seq
        """
    )
    projection_panel = next(
        (
            panel
            for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
            if panel.get("panel_id") == "result_projections"
        ),
        None,
    )
    memory_content = json.loads(memory["content_json"]) if memory is not None else {}
    fact_value = json.loads(fact["value_json"]) if fact is not None else {}
    event_types = {row["event_type"] for row in evidence}
    if (
        projection.get("status") != "PROJECTED_CANDIDATE"
        or not projection.get("candidate_only")
        or memory is None
        or memory["memory_type"] != "read_only_execution_candidate"
        or not memory_content.get("candidate_only")
        or not memory_content.get("does_not_complete_goal")
        or fact is None
        or fact["verification"] != "INFERENCE"
        or fact["source_kind"] != "INFERENCE"
        or not fact_value.get("candidate_only")
        or not {
            "memory_created",
            "world_model_assimilated",
            "read_only_execution_result_projected",
        }
        <= event_types
        or projection_panel is None
        or projection_panel["status"]["projection_count"] < 1
    ):
        return ArchitecturePassResult(
            "P18",
            "BLOCKED",
            [str(projection)],
            ["read-only result projection validation failed"],
        )
    return ArchitecturePassResult(
        "P18",
        "ADMIT_SHADOW_ONLY",
        [
            str(projection["memory_id"]),
            str(projection["fact_id"]),
            *[str(row["evidence_id"]) for row in evidence[-3:]],
        ],
        [
            "Read-only execution receipts project into candidate MemoryStore and inferred WorldModel entries with evidence, without claiming goal completion or Skill promotion",
        ],
    )


def validate_phase2_projection_review_and_rollback(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="phase2-review-file-1",
            organ_id="file",
            owner_intent="Inspect and review a disposable folder result",
            inputs={"path": str(home), "limit": 5},
        )
    )
    admission = runtime.admit_read_only_plan_preview(
        "phase2-review-file-1", reason="architecture validation review"
    )
    runtime.preflight_read_only_plan(str(admission["plan_id"]))
    runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    projection = runtime.project_read_only_execution_receipt(str(admission["plan_id"]))
    review = runtime.review_read_only_result_projection(
        str(admission["plan_id"]),
        "ROLLBACK_CANDIDATE",
        reason="architecture validation rollback",
    )
    memory = runtime.db.query_one(
        "SELECT active FROM memories WHERE memory_id=?", (projection["memory_id"],)
    )
    fact = runtime.db.query_one(
        "SELECT active FROM world_facts WHERE fact_id=?", (projection["fact_id"],)
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='read_only_result_projection_reviewed'
        ORDER BY seq DESC LIMIT 1
        """
    )
    review_panel = next(
        (
            panel
            for panel in OwnerConsoleProductProjection().project(runtime.status())["panels"]
            if panel.get("panel_id") == "projection_reviews"
        ),
        None,
    )
    if (
        review.get("decision") != "ROLLBACK_CANDIDATE"
        or not review.get("rolled_back")
        or memory is None
        or int(memory["active"]) != 0
        or fact is None
        or int(fact["active"]) != 0
        or not evidence
        or review_panel is None
        or review_panel["status"]["review_count"] < 1
    ):
        return ArchitecturePassResult(
            "P19",
            "BLOCKED",
            [str(review)],
            ["projection review/rollback validation failed"],
        )
    return ArchitecturePassResult(
        "P19",
        "ADMIT_SHADOW_ONLY",
        [
            str(projection["memory_id"]),
            str(projection["fact_id"]),
            str(evidence[0]["evidence_id"]),
        ],
        [
            "Candidate read-only projections can be reviewed and rolled back, deactivating candidate memory/world entries without Skill promotion or goal completion",
        ],
    )


def validate_phase2_browser_readonly_runtime_execution(home: Path) -> ArchitecturePassResult:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BrowserFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_port)
        runtime = LivingSystem(default_config(home))
        runtime.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id="phase2-browser-runtime-1",
                organ_id="browser",
                owner_intent="Inspect a loopback browser fixture",
                inputs={
                    "url": f"http://127.0.0.1:{port}/page",
                    "max_bytes": 4096,
                },
            )
        )
        admission = runtime.admit_read_only_plan_preview(
            "phase2-browser-runtime-1",
            reason="architecture validation browser read-only execution",
        )
        preflight = runtime.preflight_read_only_plan(str(admission["plan_id"]))
        receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
        actions = runtime.db.query_all(
            """
            SELECT tool,status,risk,side_effect_class,result_json FROM actions
            WHERE plan_id=?
            """,
            (admission["plan_id"],),
        )
        evidence = runtime.db.query_all(
            """
            SELECT event_type,evidence_id FROM evidence
            WHERE event_type IN ('action_completed','read_only_plan_executed')
            ORDER BY seq
            """
        )
        receipt_panel = next(
            (
                panel
                for panel in OwnerConsoleProductProjection().project(runtime.status())[
                    "panels"
                ]
                if panel.get("panel_id") == "execution_receipts"
            ),
            None,
        )
        output = receipt.get("outcomes", [{}])[0].get("output", {})
        event_types = {row["event_type"] for row in evidence}
        if (
            admission.get("status") != "ADMITTED_AS_PLAN"
            or preflight.get("status") != "READY_FOR_EXECUTION"
            or receipt.get("status") != "EXECUTED_READ_ONLY"
            or not receipt.get("all_succeeded")
            or not actions
            or any(row["tool"] != "http_get" for row in actions)
            or any(row["status"] != "SUCCEEDED" for row in actions)
            or any(row["risk"] != "READ" for row in actions)
            or any(row["side_effect_class"] != "none" for row in actions)
            or output.get("status") != 200
            or "WLS browser fixture" not in str(output.get("body", ""))
            or not {"action_completed", "read_only_plan_executed"} <= event_types
            or receipt_panel is None
            or receipt_panel["status"]["receipt_count"] < 1
        ):
            return ArchitecturePassResult(
                "P20",
                "BLOCKED",
                [str(receipt)],
                ["browser read-only runtime execution validation failed"],
            )
        return ArchitecturePassResult(
            "P20",
            "ADMIT_SHADOW_ONLY",
            [
                str(admission["plan_id"]),
                *[str(row["evidence_id"]) for row in evidence[-2:]],
            ],
            [
                "Browser read-only organs can execute allowlisted loopback http_get actions through Planner, Policy, ToolRegistry, receipts, evidence, and Owner Console without external writes",
            ],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def validate_phase2_research_composite_readonly_execution(
    home: Path,
) -> ArchitecturePassResult:
    home.mkdir(parents=True, exist_ok=True)
    fixture = home / "research-source.txt"
    fixture.write_text("traceable research fixture", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BrowserFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_port)
        runtime = LivingSystem(default_config(home / "runtime"))
        runtime.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id="phase2-research-composite-1",
                organ_id="research",
                owner_intent="Inspect local and loopback research sources",
                inputs={
                    "file_path": str(fixture),
                    "dir_path": str(home),
                    "url": f"http://127.0.0.1:{port}/page",
                    "max_bytes": 4096,
                    "limit": 10,
                },
            )
        )
        admission = runtime.admit_read_only_plan_preview(
            "phase2-research-composite-1",
            reason="architecture validation composite research execution",
        )
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
        projection = runtime.project_read_only_execution_receipt(
            str(admission["plan_id"])
        )
        review = runtime.review_read_only_result_projection(
            str(admission["plan_id"]),
            "ACCEPT_CANDIDATE",
            reason="architecture validation research candidate review",
        )
        actions = runtime.db.query_all(
            "SELECT tool,status,risk,side_effect_class FROM actions WHERE plan_id=? ORDER BY rowid",
            (admission["plan_id"],),
        )
        evidence = runtime.db.query_all(
            """
            SELECT event_type,evidence_id FROM evidence
            WHERE event_type IN (
                'action_completed',
                'read_only_plan_executed',
                'read_only_execution_result_projected',
                'read_only_result_projection_reviewed'
            )
            ORDER BY seq
            """
        )
        tools = [str(row["tool"]) for row in actions]
        event_types = {row["event_type"] for row in evidence}
        if (
            admission.get("rejected_tool_hints")
            or tools != ["read_file", "list_directory", "http_get"]
            or not receipt.get("all_succeeded")
            or len(receipt.get("outcomes", [])) != 3
            or any(row["status"] != "SUCCEEDED" for row in actions)
            or any(row["risk"] != "READ" for row in actions)
            or any(row["side_effect_class"] != "none" for row in actions)
            or projection.get("status") != "PROJECTED_CANDIDATE"
            or review.get("decision") != "ACCEPT_CANDIDATE"
            or review.get("rolled_back")
            or not {
                "action_completed",
                "read_only_plan_executed",
                "read_only_execution_result_projected",
                "read_only_result_projection_reviewed",
            }
            <= event_types
        ):
            return ArchitecturePassResult(
                "P21",
                "BLOCKED",
                [str(admission), str(receipt), str(projection), str(review)],
                ["composite research read-only execution validation failed"],
            )
        return ArchitecturePassResult(
            "P21",
            "ADMIT_SHADOW_ONLY",
            [
                str(admission["plan_id"]),
                str(projection["memory_id"]),
                str(projection["fact_id"]),
                *[str(row["evidence_id"]) for row in evidence[-4:]],
            ],
            [
                "Research organs can combine local file, directory, and allowlisted loopback HTTP evidence in one Planner-owned read-only execution receipt, then project and review it only as a candidate",
            ],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def validate_phase2_multimodal_asset_readonly_execution(
    home: Path,
) -> ArchitecturePassResult:
    home.mkdir(parents=True, exist_ok=True)
    asset = home / "sample.png"
    asset.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    )
    runtime = LivingSystem(default_config(home / "runtime"))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="phase2-multimodal-asset-1",
            organ_id="multimodal",
            owner_intent="Inspect a local media asset before multimodal work",
            inputs={
                "asset_path": str(asset),
                "reason": "retain candidate-only multimodal review trace",
                "max_bytes": 4096,
            },
        )
    )
    admission = runtime.admit_read_only_plan_preview(
        "phase2-multimodal-asset-1",
        reason="architecture validation multimodal asset inspection",
    )
    runtime.preflight_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    projection = runtime.project_read_only_execution_receipt(str(admission["plan_id"]))
    actions = runtime.db.query_all(
        "SELECT tool,status,risk,side_effect_class,result_json FROM actions WHERE plan_id=? ORDER BY rowid",
        (admission["plan_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type IN (
            'action_completed',
            'read_only_plan_executed',
            'read_only_execution_result_projected'
        )
        ORDER BY seq
        """
    )
    tools = [str(row["tool"]) for row in actions]
    outputs = [item.get("output", {}) for item in receipt.get("outcomes", [])]
    asset_output: dict[str, Any] = next(
        (item for item in outputs if item.get("path") == str(asset.resolve())),
        {},
    )
    event_types = {row["event_type"] for row in evidence}
    if (
        admission.get("rejected_tool_hints")
        or tools != ["inspect_asset", "noop"]
        or not receipt.get("all_succeeded")
        or asset_output.get("mime_type") != "image/png"
        or not asset_output.get("sha256")
        or projection.get("status") != "PROJECTED_CANDIDATE"
        or any(row["status"] != "SUCCEEDED" for row in actions)
        or any(row["risk"] != "READ" for row in actions)
        or any(row["side_effect_class"] != "none" for row in actions)
        or not {
            "action_completed",
            "read_only_plan_executed",
            "read_only_execution_result_projected",
        }
        <= event_types
    ):
        return ArchitecturePassResult(
            "P22",
            "BLOCKED",
            [str(admission), str(receipt), str(projection)],
            ["multimodal asset read-only execution validation failed"],
        )
    return ArchitecturePassResult(
        "P22",
        "ADMIT_SHADOW_ONLY",
        [
            str(admission["plan_id"]),
            str(asset_output["sha256"]),
            str(projection["memory_id"]),
            *[str(row["evidence_id"]) for row in evidence[-3:]],
        ],
        [
            "Multimodal organs can inspect local media assets through a path-scoped read-only tool receipt and project candidate memory/world evidence without parsing, generation, Skill promotion, or goal completion",
        ],
    )


def validate_phase2_coding_candidate_readonly_execution(
    home: Path,
) -> ArchitecturePassResult:
    worktree = home / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    candidate_file = worktree / "candidate_patch.py"
    candidate_file.write_text("print('candidate receipt')\n", encoding="utf-8")
    runtime = LivingSystem(default_config(home / "runtime"))
    runtime.intake_read_only_task(
        ReadOnlyTaskRequest(
            request_id="phase2-coding-candidate-1",
            organ_id="coding",
            owner_intent="Inspect a disposable coding candidate",
            inputs={
                "task_id": "phase2-coding-candidate-1",
                "base_sha": "base-sha-for-architecture-validation",
                "worktree_path": str(worktree),
                "changed_files": ["candidate_patch.py"],
                "tests": ["python -m pytest source/tests/test_placeholder.py"],
                "rollback": [f"remove disposable worktree {worktree}"],
            },
        )
    )
    admission = runtime.admit_read_only_plan_preview(
        "phase2-coding-candidate-1",
        reason="architecture validation coding candidate inspection",
    )
    runtime.preflight_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    projection = runtime.project_read_only_execution_receipt(str(admission["plan_id"]))
    actions = runtime.db.query_all(
        "SELECT tool,status,risk,side_effect_class,result_json FROM actions WHERE plan_id=? ORDER BY rowid",
        (admission["plan_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type IN (
            'action_completed',
            'read_only_plan_executed',
            'read_only_execution_result_projected'
        )
        ORDER BY seq
        """
    )
    output = receipt.get("outcomes", [{}])[0].get("output", {})
    changed_files = output.get("changed_files", [])
    first_file = changed_files[0] if changed_files else {}
    event_types = {row["event_type"] for row in evidence}
    if (
        admission.get("rejected_tool_hints")
        or [str(row["tool"]) for row in actions] != ["inspect_coding_candidate"]
        or not receipt.get("all_succeeded")
        or output.get("status") != "CANDIDATE_ONLY"
        or output.get("base_sha") != "base-sha-for-architecture-validation"
        or first_file.get("path") != "candidate_patch.py"
        or not first_file.get("sha256")
        or not output.get("tests")
        or not output.get("rollback")
        or projection.get("status") != "PROJECTED_CANDIDATE"
        or any(row["status"] != "SUCCEEDED" for row in actions)
        or any(row["risk"] != "READ" for row in actions)
        or any(row["side_effect_class"] != "none" for row in actions)
        or not {
            "action_completed",
            "read_only_plan_executed",
            "read_only_execution_result_projected",
        }
        <= event_types
    ):
        return ArchitecturePassResult(
            "P23",
            "BLOCKED",
            [str(admission), str(receipt), str(projection)],
            ["coding candidate read-only execution validation failed"],
        )
    return ArchitecturePassResult(
        "P23",
        "ADMIT_SHADOW_ONLY",
        [
            str(admission["plan_id"]),
            str(first_file["sha256"]),
            str(projection["memory_id"]),
            *[str(row["evidence_id"]) for row in evidence[-3:]],
        ],
        [
            "Coding organs can inspect disposable worktree candidate files, tests, and rollback metadata through a read-only receipt and project candidate evidence without running commands, merging, deployment, or Skill promotion",
        ],
    )


def validate_phase2_scheduler_due_event_runtime_intake(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    receipt = runtime.intake_scheduled_event(
        ScheduledEvent(
            schedule_id="phase2-scheduler-due-1",
            event_type="scheduled.read_only_check",
            payload={"target": "owner_console_status"},
            due_at="2026-07-02T00:00:00+00:00",
        )
    )
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    event_row = runtime.db.query_one(
        "SELECT event_type,source,status FROM events WHERE event_id=?",
        (receipt["event_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='scheduled_event_queued'
        ORDER BY seq DESC LIMIT 1
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "scheduled_events"
        ),
        None,
    )
    if (
        receipt.get("status") != "QUEUED_EVENT_ONLY"
        or not receipt.get("inserted")
        or receipt.get("creates_goal")
        or receipt.get("creates_action")
        or receipt.get("direct_tool_execution")
        or event_row is None
        or event_row["event_type"] != "scheduled.read_only_check"
        or event_row["source"] != "scheduler"
        or before_goals is None
        or after_goals is None
        or before_goals["count"] != after_goals["count"]
        or before_plans is None
        or after_plans is None
        or before_plans["count"] != after_plans["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or not evidence
        or panel is None
        or panel["status"]["receipt_count"] < 1
        or panel["status"]["creates_goal"]
        or panel["status"]["creates_action"]
    ):
        return ArchitecturePassResult(
            "P24",
            "BLOCKED",
            [str(receipt)],
            ["scheduler due-event runtime intake validation failed"],
        )
    return ArchitecturePassResult(
        "P24",
        "ADMIT_SHADOW_ONLY",
        [str(receipt["event_id"]), str(evidence[0]["evidence_id"])],
        [
            "Scheduler due events can enter runtime as canonical Events with evidence and Owner Console receipts without creating Goals, Actions, standing tasks, or direct tool execution",
        ],
    )


def validate_phase2_external_handoff_runtime_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    before_memories = runtime.db.query_one("SELECT COUNT(*) AS count FROM memories")
    mcp_receipt = runtime.admit_mcp_candidate(
        McpCandidate(
            server_id="phase2-reviewed-mcp",
            identity_digest="sha256:phase2-reviewed-mcp",
            transport="stdio",
            side_effect_class="none",
            review_status="REVIEWED",
        ),
        reason="architecture validation external handoff",
    )
    contract = TaskContract(
        task_id="phase2-a2a-worker-1",
        objective="produce candidate-only handoff artifact",
        scope={"paths": [], "authority": "none"},
        allowed_outputs=["report"],
        expires_at="2026-07-02T00:00:00+00:00",
    )
    payload = {"summary": "candidate-only", "confidence": "fixture"}
    digest = payload_digest(payload)
    a2a_receipt = runtime.receive_a2a_artifact(
        contract,
        ArtifactEnvelope(
            task_id=contract.task_id,
            artifact_type="report",
            payload=payload,
            hashes={"payload_sha256": digest},
        ),
        reason="architecture validation external worker artifact",
    )
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    after_memories = runtime.db.query_one("SELECT COUNT(*) AS count FROM memories")
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type IN ('mcp_candidate_admitted','a2a_artifact_received')
        ORDER BY seq
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "external_handoffs"
        ),
        None,
    )
    event_types = {row["event_type"] for row in evidence}
    if (
        mcp_receipt.get("status") != "VALIDATED_CANDIDATE"
        or a2a_receipt.get("status") != "CANDIDATE_ONLY"
        or not mcp_receipt.get("candidate_only")
        or not a2a_receipt.get("candidate_only")
        or mcp_receipt.get("direct_tool_execution")
        or a2a_receipt.get("direct_tool_execution")
        or mcp_receipt.get("creates_action")
        or a2a_receipt.get("creates_action")
        or before_goals is None
        or after_goals is None
        or before_goals["count"] != after_goals["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or before_memories is None
        or after_memories is None
        or before_memories["count"] != after_memories["count"]
        or not {"mcp_candidate_admitted", "a2a_artifact_received"} <= event_types
        or panel is None
        or panel["status"]["receipt_count"] != 2
        or not panel["status"]["candidate_only"]
        or panel["status"]["authority_transfer_allowed"]
    ):
        return ArchitecturePassResult(
            "P25",
            "BLOCKED",
            [str(mcp_receipt), str(a2a_receipt)],
            ["external handoff runtime receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P25",
        "ADMIT_SHADOW_ONLY",
        [
            str(mcp_receipt["identity_digest"]),
            str(a2a_receipt["payload_hash"]),
            *[str(row["evidence_id"]) for row in evidence[-2:]],
        ],
        [
            "MCP and A2A external handoffs can be recorded as candidate-only runtime receipts with evidence and Owner Console visibility, without creating actions, goals, canonical memory, or authority transfer",
        ],
    )


def validate_phase2_wechat_approval_channel_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    target = runtime.config.sandbox_path / "wechat-approval.txt"
    action = _insert_waiting_write_action(runtime, target, "approval candidate")
    before_approvals = runtime.db.query_one("SELECT COUNT(*) AS count FROM approvals")
    request_receipt = runtime.draft_wechat_approval_request(action.action_id)
    decision_receipt = runtime.intake_wechat_approval_decision(
        ChannelMessage("wechat", "owner", "approve", "wechat-approval-message-1"),
        action_id=action.action_id,
        decision="APPROVE",
        reason="architecture validation queued approval decision",
    )
    after_approvals = runtime.db.query_one("SELECT COUNT(*) AS count FROM approvals")
    action_row = runtime.db.query_one(
        "SELECT status,approval_id FROM actions WHERE action_id=?", (action.action_id,)
    )
    event_row = runtime.db.query_one(
        "SELECT event_type,source,status FROM events WHERE event_id=?",
        (decision_receipt["event_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type IN (
            'wechat_approval_request_drafted',
            'wechat_approval_decision_queued'
        )
        ORDER BY seq
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "approval_channels"
        ),
        None,
    )
    event_types = {row["event_type"] for row in evidence}
    if (
        request_receipt.get("status") != "DRAFT_APPROVAL_REQUEST"
        or request_receipt.get("creates_approval")
        or request_receipt.get("direct_tool_execution")
        or decision_receipt.get("status") != "QUEUED_EVENT_ONLY"
        or decision_receipt.get("creates_approval")
        or decision_receipt.get("executes_action")
        or before_approvals is None
        or after_approvals is None
        or before_approvals["count"] != after_approvals["count"]
        or action_row is None
        or action_row["status"] != "WAITING_APPROVAL"
        or action_row["approval_id"] is not None
        or event_row is None
        or event_row["event_type"] != "wechat.approval_decision.requested"
        or event_row["source"] != "channel:wechat"
        or not {
            "wechat_approval_request_drafted",
            "wechat_approval_decision_queued",
        }
        <= event_types
        or panel is None
        or panel["status"]["receipt_count"] != 2
        or panel["status"]["channel_executes_actions"]
        or panel["status"]["channel_issues_approvals"]
    ):
        return ArchitecturePassResult(
            "P26",
            "BLOCKED",
            [str(request_receipt), str(decision_receipt)],
            ["WeChat approval channel receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P26",
        "ADMIT_SHADOW_ONLY",
        [
            str(action.action_id),
            str(decision_receipt["event_id"]),
            *[str(row["evidence_id"]) for row in evidence[-2:]],
        ],
        [
            "WeChat approval channels can draft approval requests and queue Owner decision Events with evidence and Owner Console visibility, while ApprovalManager remains the only approval authority and no action executes",
        ],
    )


def validate_phase2_provider_route_runtime_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    receipt = runtime.record_provider_route_receipt(
        reason="architecture validation provider route"
    )
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='provider_route_recorded'
        ORDER BY seq DESC LIMIT 1
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "provider_routes"
        ),
        None,
    )
    provider = receipt.get("provider", {})
    request = receipt.get("request", {})
    if (
        receipt.get("status") != "RECORDED_ROUTE"
        or receipt.get("provider_id") != runtime.planner.provider_type
        or provider.get("remote")
        or provider.get("paid")
        or provider.get("cost_class") != "free"
        or request.get("privacy") != "local_only"
        or request.get("max_cost_class") != "free"
        or receipt.get("creates_plan")
        or receipt.get("creates_action")
        or receipt.get("direct_model_call")
        or receipt.get("direct_tool_execution")
        or before_plans is None
        or after_plans is None
        or before_plans["count"] != after_plans["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or not evidence
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or panel["status"]["direct_model_call"]
        or panel["status"]["direct_tool_execution"]
    ):
        return ArchitecturePassResult(
            "P27",
            "BLOCKED",
            [str(receipt)],
            ["provider route runtime receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P27",
        "ADMIT_SHADOW_ONLY",
        [str(receipt["provider_id"]), str(evidence[0]["evidence_id"])],
        [
            "Planner provider routes can be recorded as runtime evidence receipts and Owner Console projections while preserving local/free policy evidence and creating no plan, action, model call, or tool execution",
        ],
    )


def validate_phase2_skill_candidate_extraction_receipts(
    home: Path,
) -> ArchitecturePassResult:
    source = home / "skill-source.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("repeatable read-only skill evidence\n", encoding="utf-8")
    runtime = LivingSystem(default_config(home / "runtime"))
    before_active = len(runtime.skills.active())
    for index in range(3):
        request_id = f"phase2-skill-source-{index}"
        runtime.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id=request_id,
                organ_id="file",
                owner_intent="inspect repeated local evidence for skill candidate",
                inputs={"path": str(source), "max_bytes": 1024},
            )
        )
        admission = runtime.admit_read_only_plan_preview(
            request_id,
            reason="architecture validation skill candidate source",
        )
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.propose_skill_candidates_from_receipts(
        minimum_repeats=3,
        reason="architecture validation skill extraction",
    )
    skill_rows = [
        row
        for skill_id in receipt.get("created_skill_ids", [])
        if (
            row := runtime.db.query_one(
                "SELECT skill_id,status,definition_json FROM skills WHERE skill_id=?",
                (skill_id,),
            )
        )
        is not None
    ]
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN ('skill_created','skill_candidates_extracted')
            """
        )
    }
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "skill_candidates"
        ),
        None,
    )
    if (
        receipt.get("status") != "CANDIDATES_PROPOSED"
        or receipt.get("candidate_count", 0) < 1
        or not skill_rows
        or {str(row["status"]) for row in skill_rows} != {"PROPOSED"}
        or len(runtime.skills.active()) != before_active
        or receipt.get("promotion_executed")
        or receipt.get("approval_executed")
        or receipt.get("sandbox_executed")
        or not receipt.get("candidate_only")
        or {"skill_created", "skill_candidates_extracted"} - event_types
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or not panel["status"]["candidate_only"]
        or panel["status"]["promotion_executed"]
    ):
        return ArchitecturePassResult(
            "P28",
            "BLOCKED",
            [str(receipt)],
            ["skill candidate extraction receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P28",
        "ADMIT_SHADOW_ONLY",
        [
            *[str(row["skill_id"]) for row in skill_rows],
            *sorted(event_types),
        ],
        [
            "Repeated successful read-only action receipts can propose Skill candidates with evidence and Owner Console visibility, while remaining PROPOSED and never executing sandbox, approval, promotion, or rollback",
        ],
    )


def validate_phase2_learning_epoch_review_receipts(
    home: Path,
) -> ArchitecturePassResult:
    source = home / "learning-source.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("repeatable learning epoch evidence\n", encoding="utf-8")
    runtime = LivingSystem(default_config(home / "runtime"))
    for index in range(3):
        request_id = f"phase2-learning-source-{index}"
        runtime.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id=request_id,
                organ_id="file",
                owner_intent="inspect repeated local evidence for learning epoch",
                inputs={"path": str(source), "max_bytes": 1024},
            )
        )
        admission = runtime.admit_read_only_plan_preview(
            request_id,
            reason="architecture validation learning epoch source",
        )
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.review_learning_epoch_from_receipts(
        reason="architecture validation learning epoch",
        minimum_repeats=3,
        allow_candidate_extraction=True,
        owner_authorization="architecture-validation-owner-authorization",
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
              'learning_epoch_reviewed',
              'skill_candidates_extracted',
              'skill_created'
            )
            """
        )
    }
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "learning_epoch"
        ),
        None,
    )
    modes = {
        str(item.get("mode")): item
        for item in receipt.get("learning_modes", [])
        if isinstance(item, dict)
    }
    if (
        receipt.get("status") != "REVIEW_RECORDED"
        or "learning_frozen" not in modes
        or "candidate_only" not in modes
        or modes["learning_frozen"].get("candidate_extraction_executed")
        or not modes["candidate_only"].get("candidate_extraction_executed")
        or modes["candidate_only"].get("candidate_count", 0) < 1
        or receipt.get("active_skill_count_before")
        != receipt.get("active_skill_count_after")
        or receipt.get("promotion_executed")
        or receipt.get("approval_executed")
        or receipt.get("sandbox_executed")
        or {"learning_epoch_reviewed", "skill_candidates_extracted", "skill_created"}
        - event_types
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or panel["status"]["default_mode"] != "learning_frozen"
        or panel["status"]["promotion_executed"]
    ):
        return ArchitecturePassResult(
            "P29",
            "BLOCKED",
            [str(receipt)],
            ["learning epoch review receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P29",
        "ADMIT_SHADOW_ONLY",
        [
            *modes["candidate_only"].get("created_skill_ids", []),
            *sorted(event_types),
        ],
        [
            "Learning epoch review records frozen and candidate-only modes from real read-only receipts with Owner authorization, while preserving the no-promotion, no-approval, no-sandbox ceiling",
        ],
    )


def validate_phase2_capability_epoch_audit_receipts(
    home: Path,
) -> ArchitecturePassResult:
    source = home / "epoch-source.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("repeatable capability epoch evidence\n", encoding="utf-8")
    runtime = LivingSystem(default_config(home / "runtime"))
    for index in range(3):
        request_id = f"phase2-epoch-source-{index}"
        runtime.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id=request_id,
                organ_id="file",
                owner_intent="inspect repeated local evidence for capability epoch",
                inputs={"path": str(source), "max_bytes": 1024},
            )
        )
        admission = runtime.admit_read_only_plan_preview(
            request_id,
            reason="architecture validation capability epoch source",
        )
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    runtime.review_learning_epoch_from_receipts(
        reason="architecture validation capability epoch learning review",
        minimum_repeats=3,
        allow_candidate_extraction=True,
        owner_authorization="architecture-validation-owner-authorization",
    )
    completed = [f"P{index:02d}" for index in range(1, 30)]
    receipt = runtime.record_capability_epoch_audit(
        reason="architecture validation capability epoch audit",
        completed_passes=completed,
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='capability_epoch_audited'
        ORDER BY seq DESC LIMIT 1
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "capability_epoch"
        ),
        None,
    )
    capability_state = receipt.get("capability_state", {})
    decision = receipt.get("phase2_admission_decision", {})
    counts = receipt.get("receipt_counts", {})
    if (
        receipt.get("status") != "AUDIT_RECORDED"
        or receipt.get("highest_pass") != "P29"
        or receipt.get("allowed_conclusion") != "FUNCTIONAL_RUNTIME_ONLY"
        or decision.get("status") != "ADMIT_LOW_RISK_PREPARATION_ONLY"
        or capability_state.get("second_authority_admitted")
        or capability_state.get("skill_promotion_executed")
        or capability_state.get("live_deployment_executed")
        or capability_state.get("external_system_modified")
        or counts.get("read_only_execution", 0) < 1
        or counts.get("skill_candidate", 0) < 1
        or counts.get("learning_epoch", 0) < 1
        or not evidence
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or panel["status"]["allowed_conclusion"] != "FUNCTIONAL_RUNTIME_ONLY"
        or panel["status"]["live_deployment_executed"]
    ):
        return ArchitecturePassResult(
            "P30",
            "BLOCKED",
            [str(receipt)],
            ["capability epoch audit receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P30",
        "ADMIT_SHADOW_ONLY",
        [
            str(evidence[0]["evidence_id"]),
            str(receipt["highest_pass"]),
            str(decision["status"]),
        ],
        [
            "Capability epoch audit records the P01-P29 shadow admission state with low-risk preparation only, preserving no second authority, no Skill promotion, no live deployment, and no external writes",
        ],
    )


def validate_phase2_voice_transcript_ingress_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    receipt = runtime.intake_voice_transcript(
        transcript_id="phase2-voice-1",
        speaker_id="owner",
        transcript="status check from local voice transcript",
        locale="en-US",
        confidence=0.91,
        source="architecture_validation_fixture",
    )
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    event = runtime.db.query_one(
        "SELECT event_type,source,payload_json FROM events WHERE event_id=?",
        (receipt["event_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='voice_transcript_queued'
        ORDER BY seq DESC LIMIT 1
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "voice_ingress"
        ),
        None,
    )
    if (
        receipt.get("status") != "QUEUED_EVENT_ONLY"
        or receipt.get("audio_captured")
        or receipt.get("stt_executed")
        or receipt.get("creates_goal")
        or receipt.get("creates_action")
        or receipt.get("direct_tool_execution")
        or receipt.get("writes_canonical_state")
        or before_goals is None
        or after_goals is None
        or before_goals["count"] != after_goals["count"]
        or before_plans is None
        or after_plans is None
        or before_plans["count"] != after_plans["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or event is None
        or event["event_type"] != "channel.message"
        or event["source"] != "channel:voice"
        or not evidence
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or panel["status"]["creates_goal"]
        or panel["status"]["creates_action"]
        or panel["status"]["audio_captured"]
        or panel["status"]["stt_executed"]
    ):
        return ArchitecturePassResult(
            "P31",
            "BLOCKED",
            [str(receipt)],
            ["voice transcript ingress receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P31",
        "ADMIT_SHADOW_ONLY",
        [str(receipt["event_id"]), str(evidence[0]["evidence_id"])],
        [
            "Voice transcript ingress queues an already-transcribed local utterance as a canonical channel Event with evidence and Owner Console visibility, without audio capture, STT, Goals, Plans, Actions, or tool execution",
        ],
    )


def validate_phase2_local_notification_draft_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    receipt = runtime.draft_local_notification(
        channel="voice",
        mode="speech_script",
        purpose="architecture validation owner status reply draft",
        body="WLS status draft is ready for Owner review.",
    )
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    draft_path = Path(str(receipt.get("outbox_path", "")))
    draft = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path.exists() else {}
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='local_notification_drafted'
        ORDER BY seq DESC LIMIT 1
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "notification_drafts"
        ),
        None,
    )
    if (
        receipt.get("status") != "DRAFT_WRITTEN"
        or receipt.get("delivery_executed")
        or receipt.get("tts_executed")
        or receipt.get("audio_played")
        or receipt.get("external_send_executed")
        or receipt.get("creates_goal")
        or receipt.get("creates_action")
        or receipt.get("direct_tool_execution")
        or before_goals is None
        or after_goals is None
        or before_goals["count"] != after_goals["count"]
        or before_plans is None
        or after_plans is None
        or before_plans["count"] != after_plans["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or not draft_path.exists()
        or draft.get("delivery_executed")
        or draft.get("tts_executed")
        or draft.get("audio_played")
        or not evidence
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or panel["status"]["delivery_executed"]
        or panel["status"]["tts_executed"]
        or panel["status"]["audio_played"]
        or panel["status"]["external_send_executed"]
    ):
        return ArchitecturePassResult(
            "P32",
            "BLOCKED",
            [str(receipt)],
            ["local notification draft receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P32",
        "ADMIT_SHADOW_ONLY",
        [str(draft_path), str(evidence[0]["evidence_id"])],
        [
            "Local notification drafting writes a reversible outbox draft with evidence and Owner Console visibility, without external send, TTS, audio playback, Goals, Plans, Actions, or tool execution",
        ],
    )


def validate_phase2_screen_snapshot_ingress_receipts(
    home: Path,
) -> ArchitecturePassResult:
    snapshot = home / "screen.png"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"\x89PNG\r\n\x1a\nWLS-SCREEN")
    runtime = LivingSystem(default_config(home / "runtime"))
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    receipt = runtime.intake_screen_snapshot_asset(
        snapshot_id="phase2-screen-1",
        path=snapshot,
        source="architecture_validation_fixture",
        purpose="screen context fixture",
    )
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    event = runtime.db.query_one(
        "SELECT event_type,source,payload_json FROM events WHERE event_id=?",
        (receipt["event_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='screen_snapshot_queued'
        ORDER BY seq DESC LIMIT 1
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "screen_snapshots"
        ),
        None,
    )
    if (
        receipt.get("status") != "QUEUED_EVENT_ONLY"
        or receipt.get("ocr_executed")
        or receipt.get("ui_control_executed")
        or receipt.get("external_upload_executed")
        or receipt.get("creates_goal")
        or receipt.get("creates_action")
        or receipt.get("direct_tool_execution")
        or before_goals is None
        or after_goals is None
        or before_goals["count"] != after_goals["count"]
        or before_plans is None
        or after_plans is None
        or before_plans["count"] != after_plans["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or event is None
        or event["event_type"] != "channel.message"
        or event["source"] != "channel:screen"
        or not evidence
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or panel["status"]["ocr_executed"]
        or panel["status"]["ui_control_executed"]
        or panel["status"]["external_upload_executed"]
    ):
        return ArchitecturePassResult(
            "P33",
            "BLOCKED",
            [str(receipt)],
            ["screen snapshot ingress receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P33",
        "ADMIT_SHADOW_ONLY",
        [str(receipt["event_id"]), str(evidence[0]["evidence_id"])],
        [
            "Screen snapshot ingress queues a local screenshot asset as a canonical channel Event with evidence and Owner Console visibility, without OCR, UI control, upload, Goals, Plans, Actions, or tool execution",
        ],
    )


def validate_phase2_browser_form_draft_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    receipt = runtime.draft_browser_form_submission(
        form_id="phase2-form-1",
        url="http://127.0.0.1/form",
        fields={"query": "local evidence", "mode": "readonly"},
        purpose="architecture validation browser form draft",
    )
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    blocked = False
    try:
        runtime.draft_browser_form_submission(
            form_id="phase2-form-blocked",
            url="http://example.com/form",
            fields={"query": "unsafe"},
            purpose="blocked form draft",
        )
    except PermissionError:
        blocked = True
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='browser_form_drafted'
        ORDER BY seq DESC LIMIT 1
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "browser_form_drafts"
        ),
        None,
    )
    if (
        receipt.get("status") != "DRAFT_RECORDED"
        or receipt.get("browser_opened")
        or receipt.get("form_submitted")
        or receipt.get("network_post_executed")
        or receipt.get("download_executed")
        or receipt.get("creates_goal")
        or receipt.get("creates_action")
        or receipt.get("direct_tool_execution")
        or not receipt.get("approval_required_for_submission")
        or not blocked
        or before_goals is None
        or after_goals is None
        or before_goals["count"] != after_goals["count"]
        or before_plans is None
        or after_plans is None
        or before_plans["count"] != after_plans["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or not evidence
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or panel["status"]["browser_opened"]
        or panel["status"]["form_submitted"]
        or panel["status"]["network_post_executed"]
        or not panel["status"]["approval_required_for_submission"]
    ):
        return ArchitecturePassResult(
            "P34",
            "BLOCKED",
            [str(receipt)],
            ["browser form draft receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P34",
        "ADMIT_SHADOW_ONLY",
        [str(receipt["form_id"]), str(evidence[0]["evidence_id"])],
        [
            "Browser form drafting records a policy-bounded form intent with field digest and Owner Console visibility, without opening a browser, submitting, POSTing, downloading, Goals, Plans, Actions, or tool execution",
        ],
    )


def validate_phase2_download_quarantine_draft_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    receipt = runtime.draft_download_quarantine(
        download_id="phase2-download-1",
        url="http://127.0.0.1/file.txt",
        filename="file.txt",
        purpose="architecture validation download quarantine draft",
    )
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    blocked = False
    try:
        runtime.draft_download_quarantine(
            download_id="phase2-download-blocked",
            url="http://example.com/file.txt",
            filename="file.txt",
            purpose="blocked download quarantine",
        )
    except PermissionError:
        blocked = True
    manifest_path = Path(str(receipt.get("manifest_path", "")))
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='download_quarantine_drafted'
        ORDER BY seq DESC LIMIT 1
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "download_quarantine"
        ),
        None,
    )
    if (
        receipt.get("status") != "QUARANTINE_DRAFT_RECORDED"
        or receipt.get("network_fetch_executed")
        or receipt.get("file_materialized")
        or receipt.get("external_write_executed")
        or receipt.get("creates_goal")
        or receipt.get("creates_action")
        or receipt.get("direct_tool_execution")
        or not receipt.get("approval_required_for_fetch")
        or manifest.get("network_fetch_executed")
        or manifest.get("file_materialized")
        or not blocked
        or before_goals is None
        or after_goals is None
        or before_goals["count"] != after_goals["count"]
        or before_plans is None
        or after_plans is None
        or before_plans["count"] != after_plans["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or not evidence
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or panel["status"]["network_fetch_executed"]
        or panel["status"]["file_materialized"]
        or panel["status"]["external_write_executed"]
        or not panel["status"]["approval_required_for_fetch"]
    ):
        return ArchitecturePassResult(
            "P35",
            "BLOCKED",
            [str(receipt)],
            ["download quarantine draft receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P35",
        "ADMIT_SHADOW_ONLY",
        [str(manifest_path), str(evidence[0]["evidence_id"])],
        [
            "Download quarantine drafting records a sandbox manifest for a policy-bounded download intent with evidence and Owner Console visibility, without network fetch, file materialization, external writes, Goals, Plans, Actions, or tool execution",
        ],
    )


def validate_phase2_document_ingress_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    document_path = home / "fixtures" / "phase2-document.pdf"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_bytes(b"%PDF-1.4\n% WLS local document fixture\n")
    before_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    receipt = runtime.intake_document_asset(
        document_id="phase2-document-1",
        path=document_path,
        source="architecture_validation_fixture",
        purpose="architecture validation document ingress",
    )
    after_goals = runtime.db.query_one("SELECT COUNT(*) AS count FROM goals")
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    event = runtime.db.query_one(
        "SELECT event_type,source,payload_json FROM events WHERE event_id=?",
        (receipt.get("event_id"),),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type='document_asset_queued'
        ORDER BY seq DESC LIMIT 1
        """
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "document_ingress"
        ),
        None,
    )
    if (
        receipt.get("status") != "QUEUED_EVENT_ONLY"
        or receipt.get("text_extracted")
        or receipt.get("ocr_executed")
        or receipt.get("vector_indexed")
        or receipt.get("external_upload_executed")
        or receipt.get("creates_goal")
        or receipt.get("creates_action")
        or receipt.get("direct_tool_execution")
        or before_goals is None
        or after_goals is None
        or before_goals["count"] != after_goals["count"]
        or before_plans is None
        or after_plans is None
        or before_plans["count"] != after_plans["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or event is None
        or event["event_type"] != "channel.message"
        or event["source"] != "channel:document"
        or not evidence
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or panel["status"]["text_extracted"]
        or panel["status"]["ocr_executed"]
        or panel["status"]["vector_indexed"]
        or panel["status"]["external_upload_executed"]
    ):
        return ArchitecturePassResult(
            "P36",
            "BLOCKED",
            [str(receipt)],
            ["document ingress receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P36",
        "ADMIT_SHADOW_ONLY",
        [str(receipt["event_id"]), str(evidence[0]["evidence_id"])],
        [
            "Document ingress queues a local document asset as a canonical Event with hashes and Owner Console visibility, without parsing, OCR, vector indexing, upload, Goals, Plans, Actions, or tool execution",
        ],
    )


def validate_phase2_document_retrieval_preview(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    document_path = home / "fixtures" / "phase2-document-preview.pdf"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_bytes(b"%PDF-1.4\n% WLS document preview fixture\n")
    source_receipt = runtime.intake_document_asset(
        document_id="phase2-document-preview-1",
        path=document_path,
        source="architecture_validation_fixture",
        purpose="architecture validation document retrieval preview",
    )
    before_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    before_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    result = runtime.prepare_document_retrieval_preview(
        document_id="phase2-document-preview-1",
        request_id="phase2-document-preview-request-1",
        owner_intent="Prepare a read-only local document inspection preview",
    )
    after_plans = runtime.db.query_one("SELECT COUNT(*) AS count FROM plans")
    after_actions = runtime.db.query_one("SELECT COUNT(*) AS count FROM actions")
    preview = result.get("read_only_task", {}).get("preview", {})
    candidate = preview.get("candidate", {})
    action_tools = {
        item.get("tool")
        for item in candidate.get("candidate_actions", [])
        if isinstance(item, dict)
    }
    evidence = runtime.db.query_all(
        """
        SELECT evidence_id FROM evidence
        WHERE event_type='read_only_plan_preview_recorded'
        ORDER BY seq DESC LIMIT 1
        """
    )
    projection = OwnerConsoleProductProjection().project(runtime.status())
    preview_panel = next(
        (
            panel
            for panel in projection["panels"]
            if panel.get("panel_id") == "task_previews"
        ),
        None,
    )
    if (
        source_receipt.get("status") != "QUEUED_EVENT_ONLY"
        or result.get("status") != "PREVIEW_ONLY"
        or result.get("creates_plan")
        or result.get("creates_action")
        or result.get("direct_tool_execution")
        or result.get("text_extracted")
        or result.get("ocr_executed")
        or result.get("vector_indexed")
        or preview.get("status") != "PREVIEW_ONLY"
        or candidate.get("organ_id") != "document"
        or not {"inspect_asset", "read_file"} <= action_tools
        or before_plans is None
        or after_plans is None
        or before_plans["count"] != after_plans["count"]
        or before_actions is None
        or after_actions is None
        or before_actions["count"] != after_actions["count"]
        or not evidence
        or preview_panel is None
        or preview_panel["status"]["preview_count"] != 1
    ):
        return ArchitecturePassResult(
            "P37",
            "BLOCKED",
            [str(result)],
            ["document retrieval preview validation failed"],
        )
    return ArchitecturePassResult(
        "P37",
        "ADMIT_SHADOW_ONLY",
        [str(preview["event_id"]), str(evidence[0]["evidence_id"])],
        [
            "Document retrieval preview turns a local document ingress receipt into a Planner-owned read-only plan candidate with Owner Console visibility, without parsing, OCR, vector indexing, Plan rows, Action rows, or tool execution",
        ],
    )


def validate_phase2_document_readonly_execution(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    document_path = home / "fixtures" / "phase2-document-execution.pdf"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_bytes(b"%PDF-1.4\n% WLS document execution fixture\n")
    source_receipt = runtime.intake_document_asset(
        document_id="phase2-document-execution-1",
        path=document_path,
        source="architecture_validation_fixture",
        purpose="architecture validation document read-only execution",
    )
    preview = runtime.prepare_document_retrieval_preview(
        document_id="phase2-document-execution-1",
        request_id="phase2-document-execution-request-1",
        owner_intent="Inspect a local document through read-only tools",
    )
    admission = runtime.admit_read_only_plan_preview(
        "phase2-document-execution-request-1",
        reason="architecture validation document admission",
    )
    preflight = runtime.preflight_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    actions = runtime.db.query_all(
        "SELECT status,risk,side_effect_class,tool FROM actions WHERE plan_id=?",
        (admission["plan_id"],),
    )
    event_types = {
        row["event_type"]
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'read_only_plan_preview_recorded',
                'read_only_plan_preview_admitted',
                'read_only_execution_preflight_recorded',
                'action_completed',
                'read_only_plan_executed'
            )
            """
        )
    }
    outcomes = receipt.get("outcomes", [])
    output_keys = {
        key
        for outcome in outcomes
        if isinstance(outcome, dict)
        for key in outcome.get("output", {})
    }
    if (
        source_receipt.get("status") != "QUEUED_EVENT_ONLY"
        or preview.get("status") != "PREVIEW_ONLY"
        or admission.get("status") != "ADMITTED_AS_PLAN"
        or preflight.get("status") != "READY_FOR_EXECUTION"
        or receipt.get("status") != "EXECUTED_READ_ONLY"
        or not receipt.get("all_succeeded")
        or receipt.get("direct_tool_execution") is not True
        or not actions
        or {row["tool"] for row in actions} != {"inspect_asset", "read_file"}
        or any(row["risk"] != "READ" for row in actions)
        or any(row["side_effect_class"] != "none" for row in actions)
        or any(row["status"] != "SUCCEEDED" for row in actions)
        or not {"sha256", "mime_type", "text", "path"} <= output_keys
        or not {
            "read_only_plan_preview_recorded",
            "read_only_plan_preview_admitted",
            "read_only_execution_preflight_recorded",
            "action_completed",
            "read_only_plan_executed",
        }
        <= event_types
    ):
        return ArchitecturePassResult(
            "P38",
            "BLOCKED",
            [str(receipt)],
            ["document read-only execution validation failed"],
        )
    return ArchitecturePassResult(
        "P38",
        "ADMIT_SHADOW_ONLY",
        [str(admission["plan_id"]), str(source_receipt["event_id"])],
        [
            "Document read-only execution admits a local document preview through Planner, Policy preflight, ToolRegistry, receipts, and evidence using only inspect_asset/read_file with no external writes, OCR, vector indexing, Skill promotion, or live deployment",
        ],
    )


def validate_phase2_document_projection_review(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    document_path = home / "fixtures" / "phase2-document-projection.pdf"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_bytes(b"%PDF-1.4\n% WLS document projection fixture\n")
    source_receipt = runtime.intake_document_asset(
        document_id="phase2-document-projection-1",
        path=document_path,
        source="architecture_validation_fixture",
        purpose="architecture validation document projection review",
    )
    runtime.prepare_document_retrieval_preview(
        document_id="phase2-document-projection-1",
        request_id="phase2-document-projection-request-1",
        owner_intent="Project and review a local document read-only result",
    )
    admission = runtime.admit_read_only_plan_preview(
        "phase2-document-projection-request-1",
        reason="architecture validation document projection admission",
    )
    runtime.preflight_read_only_plan(str(admission["plan_id"]))
    execution = runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    projection = runtime.project_read_only_execution_receipt(str(admission["plan_id"]))
    review = runtime.review_read_only_result_projection(
        str(admission["plan_id"]),
        "ROLLBACK_CANDIDATE",
        reason="architecture validation document rollback",
    )
    memory = runtime.db.query_one(
        "SELECT memory_type,content_json,active FROM memories WHERE memory_id=?",
        (projection["memory_id"],),
    )
    fact = runtime.db.query_one(
        "SELECT verification,source_kind,value_json,active FROM world_facts WHERE fact_id=?",
        (projection["fact_id"],),
    )
    evidence = runtime.db.query_all(
        """
        SELECT event_type,evidence_id FROM evidence
        WHERE event_type IN (
            'memory_created',
            'world_model_assimilated',
            'read_only_execution_result_projected',
            'read_only_result_projection_reviewed'
        )
        ORDER BY seq
        """
    )
    projection_panel = next(
        (
            panel
            for panel in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if panel.get("panel_id") == "result_projections"
        ),
        None,
    )
    review_panel = next(
        (
            panel
            for panel in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if panel.get("panel_id") == "projection_reviews"
        ),
        None,
    )
    memory_content = json.loads(memory["content_json"]) if memory is not None else {}
    fact_value = json.loads(fact["value_json"]) if fact is not None else {}
    receipt = memory_content.get("receipt", {})
    action_tools = {
        outcome.get("output", {}).get("mime_type", outcome.get("output", {}).get("path"))
        for outcome in receipt.get("outcomes", [])
        if isinstance(outcome, dict)
    }
    event_types = {row["event_type"] for row in evidence}
    if (
        source_receipt.get("status") != "QUEUED_EVENT_ONLY"
        or execution.get("status") != "EXECUTED_READ_ONLY"
        or not execution.get("all_succeeded")
        or projection.get("status") != "PROJECTED_CANDIDATE"
        or not projection.get("candidate_only")
        or review.get("decision") != "ROLLBACK_CANDIDATE"
        or not review.get("rolled_back")
        or memory is None
        or memory["memory_type"] != "read_only_execution_candidate"
        or memory["active"] != 0
        or not memory_content.get("candidate_only")
        or not memory_content.get("does_not_complete_goal")
        or not memory_content.get("does_not_promote_skill")
        or "application/pdf" not in action_tools
        or fact is None
        or fact["verification"] != "INFERENCE"
        or fact["source_kind"] != "INFERENCE"
        or fact["active"] != 0
        or not fact_value.get("candidate_only")
        or projection_panel is None
        or projection_panel["status"]["projection_count"] < 1
        or review_panel is None
        or review_panel["status"]["review_count"] < 1
        or not {
            "memory_created",
            "world_model_assimilated",
            "read_only_execution_result_projected",
            "read_only_result_projection_reviewed",
        }
        <= event_types
    ):
        return ArchitecturePassResult(
            "P39",
            "BLOCKED",
            [str(projection), str(review)],
            ["document projection review validation failed"],
        )
    return ArchitecturePassResult(
        "P39",
        "ADMIT_SHADOW_ONLY",
        [
            str(projection["memory_id"]),
            str(projection["fact_id"]),
            str(source_receipt["event_id"]),
        ],
        [
            "Document read-only execution results can enter candidate Memory/World projections and be rolled back, preserving inference status, candidate-only limits, evidence, and no Skill promotion or goal completion",
        ],
    )


def validate_phase2_document_skill_candidate_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    document_path = home / "fixtures" / "phase2-document-skill.pdf"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_bytes(b"%PDF-1.4\n% WLS document skill fixture\n")
    before_active = len(runtime.skills.active())
    for index in range(3):
        document_id = f"phase2-document-skill-{index}"
        request_id = f"phase2-document-skill-request-{index}"
        runtime.intake_document_asset(
            document_id=document_id,
            path=document_path,
            source="architecture_validation_fixture",
            purpose="architecture validation document skill source",
        )
        runtime.prepare_document_retrieval_preview(
            document_id=document_id,
            request_id=request_id,
            owner_intent="Repeat local document inspection for Skill candidate",
        )
        admission = runtime.admit_read_only_plan_preview(
            request_id,
            reason="architecture validation document skill admission",
        )
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    receipt = runtime.propose_skill_candidates_from_receipts(
        minimum_repeats=3,
        reason="architecture validation document skill extraction",
    )
    skill_rows = [
        row
        for skill_id in receipt.get("created_skill_ids", [])
        if (
            row := runtime.db.query_one(
                "SELECT skill_id,status,definition_json FROM skills WHERE skill_id=?",
                (skill_id,),
            )
        )
        is not None
    ]
    definitions = [json.loads(str(row["definition_json"])) for row in skill_rows]
    skill_tools = {
        step.get("tool")
        for definition in definitions
        for step in definition.get("steps", [])
        if isinstance(step, dict)
    }
    source_episode_count = sum(
        len(definition.get("source_episode_ids", [])) for definition in definitions
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN ('skill_created','skill_candidates_extracted')
            """
        )
    }
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "skill_candidates"
        ),
        None,
    )
    if (
        receipt.get("status") != "CANDIDATES_PROPOSED"
        or receipt.get("candidate_count", 0) < 1
        or not skill_rows
        or {str(row["status"]) for row in skill_rows} != {"PROPOSED"}
        or len(runtime.skills.active()) != before_active
        or not {"inspect_asset", "read_file"} <= skill_tools
        or source_episode_count < 6
        or receipt.get("promotion_executed")
        or receipt.get("approval_executed")
        or receipt.get("sandbox_executed")
        or not receipt.get("candidate_only")
        or {"skill_created", "skill_candidates_extracted"} - event_types
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or not panel["status"]["candidate_only"]
        or panel["status"]["promotion_executed"]
    ):
        return ArchitecturePassResult(
            "P40",
            "BLOCKED",
            [str(receipt)],
            ["document skill candidate receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P40",
        "ADMIT_SHADOW_ONLY",
        [
            *[str(row["skill_id"]) for row in skill_rows],
            *sorted(event_types),
        ],
        [
            "Repeated document read-only execution receipts can propose PROPOSED Skill candidates from inspect_asset/read_file trajectories while preserving no sandbox, no approval, no promotion, no active Skill, and Owner Console visibility",
        ],
    )


def validate_phase2_document_skill_sandbox_receipts(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    document_path = home / "fixtures" / "phase2-document-sandbox.pdf"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_bytes(b"%PDF-1.4\n% WLS document sandbox fixture\n")
    before_active = len(runtime.skills.active())
    for index in range(3):
        document_id = f"phase2-document-sandbox-{index}"
        request_id = f"phase2-document-sandbox-request-{index}"
        runtime.intake_document_asset(
            document_id=document_id,
            path=document_path,
            source="architecture_validation_fixture",
            purpose="architecture validation document sandbox source",
        )
        runtime.prepare_document_retrieval_preview(
            document_id=document_id,
            request_id=request_id,
            owner_intent="Repeat local document inspection for Skill sandbox candidate",
        )
        admission = runtime.admit_read_only_plan_preview(
            request_id,
            reason="architecture validation document sandbox admission",
        )
        runtime.preflight_read_only_plan(str(admission["plan_id"]))
        runtime.execute_preflighted_read_only_plan(str(admission["plan_id"]))
    candidate_receipt = runtime.propose_skill_candidates_from_receipts(
        minimum_repeats=3,
        reason="architecture validation document sandbox extraction",
    )
    skill_id = str(candidate_receipt["created_skill_ids"][0])
    sandbox = runtime.start_skill_sandbox_validation(
        skill_id=skill_id,
        reason="architecture validation document sandbox start",
    )
    skill = runtime.db.query_one(
        "SELECT status,definition_json FROM skills WHERE skill_id=?", (skill_id,)
    )
    experiment = runtime.db.query_one(
        "SELECT status,manifest_json,result_json FROM skill_experiments WHERE experiment_id=?",
        (sandbox["experiment_id"],),
    )
    event_types = {
        str(row["event_type"])
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
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "skill_sandbox"
        ),
        None,
    )
    definition = json.loads(str(skill["definition_json"])) if skill is not None else {}
    manifest = json.loads(str(experiment["manifest_json"])) if experiment else {}
    if (
        sandbox.get("status") != "SANDBOX_STARTED"
        or sandbox.get("skill_status_before") != "PROPOSED"
        or sandbox.get("skill_status_after") != "SANDBOXED"
        or sandbox.get("validation_passed")
        or sandbox.get("approval_executed")
        or sandbox.get("promotion_executed")
        or sandbox.get("deployment_executed")
        or len(runtime.skills.active()) != before_active
        or skill is None
        or skill["status"] != "SANDBOXED"
        or definition.get("status") != "SANDBOXED"
        or experiment is None
        or experiment["status"] != "RUNNING"
        or experiment["result_json"] is not None
        or manifest.get("mode") != "sandbox_candidate_only"
        or manifest.get("promotion_executed")
        or manifest.get("approval_executed")
        or not Path(str(sandbox.get("manifest_path", ""))).is_file()
        or panel is None
        or panel["status"]["receipt_count"] != 1
        or not panel["status"]["sandbox_started"]
        or panel["status"]["validation_passed"]
        or panel["status"]["promotion_executed"]
        or {
            "skill_sandbox_validation_started",
            "skill_transition",
            "skill_sandbox_receipt_recorded",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P41",
            "BLOCKED",
            [str(sandbox)],
            ["document skill sandbox receipt validation failed"],
        )
    return ArchitecturePassResult(
        "P41",
        "ADMIT_SHADOW_ONLY",
        [skill_id, str(sandbox["experiment_id"]), *sorted(event_types)],
        [
            "Document Skill candidates can enter a SANDBOXED running experiment state with evidence and Owner Console visibility while preserving no validation pass, no approval, no promotion, no active Skill, and no deployment",
        ],
    )


def validate_phase2_agentic_task_harness(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents in parallel",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
        model_hints={"allow_parallel": True, "domain": "RESEARCH"},
    )
    graph_id = str(receipt["graph"]["graph_id"])
    reloaded = LivingSystem(default_config(home / "runtime"))
    graph = reloaded.agentic.load_graph(graph_id)
    leases = reloaded.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=2
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(reloaded.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in reloaded.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_graph_compiled',
                'agentic_task_node_leased'
            )
            """
        )
    }
    if (
        receipt["intent"]["risk_floor"] != "READ"
        or receipt["route"]["mode"] != "TASK_GRAPH"
        or graph.graph_id != graph_id
        or [node.node_id for node in graph.ready_frontier()] != ["scope"]
        or len(leases) != 1
        or leases[0].conflict_domain != "readonly"
        or panel is None
        or panel["status"]["second_authority_created"]
        or panel["status"]["direct_worker_execution"]
        or {"agentic_task_graph_compiled", "agentic_task_node_leased"} - event_types
    ):
        return ArchitecturePassResult(
            "P42",
            "BLOCKED",
            [graph_id],
            ["agentic task harness validation failed"],
        )
    return ArchitecturePassResult(
        "P42",
        "ADMIT_SHADOW_ONLY",
        [graph_id, leases[0].lease_id, *sorted(event_types)],
        [
            "Agentic task harness admits, compiles, persists, reloads, leases, and projects bounded task graphs through canonical LivingSystem state without creating a second authority or executing external workers",
        ],
    )


def validate_phase2_agentic_acceptance_trace(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    artifact_root = home / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact = artifact_root / "acceptance.txt"
    artifact.write_text("agentic acceptance trace fixture\n", encoding="utf-8")
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    completion = runtime.agentic.complete_node_with_acceptance(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        result={
            "status": "SUCCEEDED",
            "summary": "inspection result is recorded",
            "evidence": ["agentic_task_graph_compiled"],
            "payload": {"ok": True},
        },
        acceptance_checks=[
            {"check_id": "status", "type": "result_status"},
            {
                "check_id": "summary",
                "type": "regex",
                "config": {"field": "summary", "pattern": "recorded"},
            },
            {
                "check_id": "artifact",
                "type": "artifact_exists",
                "config": {"path": artifact.name},
            },
        ],
        artifact_root=artifact_root,
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_node_acceptance_evaluated',
                'agentic_task_node_completed'
            )
            """
        )
    }
    graph = runtime.agentic.load_graph(graph_id)
    if (
        completion["acceptance_report"]["passed"] is not True
        or not completion["trace_event"].get("trace_digest")
        or graph.nodes[lease.node_id].status.value != "SUCCEEDED"
        or panel is None
        or panel["status"]["acceptance_trace_v1"]["receipt_count"] != 1
        or {
            "agentic_task_node_acceptance_evaluated",
            "agentic_task_node_completed",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P43",
            "BLOCKED",
            [graph_id],
            ["agentic acceptance trace validation failed"],
        )
    return ArchitecturePassResult(
        "P43",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            lease.lease_id,
            completion["trace_event"]["trace_digest"],
            *sorted(event_types),
        ],
        [
            "Agentic node completion can be gated by deterministic acceptance checks and trace digests while preserving EvidenceLedger and Owner Console visibility as the only authorities",
        ],
    )


def validate_phase2_agentic_file_mailbox_handoff(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    mailbox_root = home / "mailbox"
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    exported = runtime.agentic.export_node_task_envelope(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        mailbox_root=mailbox_root,
        recipient="local-worker-shadow",
    )
    mailbox = AgenticFileMailbox(mailbox_root)
    result_envelope = ResultEnvelope.create(
        message_id="result-validation-1",
        in_reply_to=str(exported["message_id"]),
        graph_id=graph_id,
        node_id=lease.node_id,
        lease_id=lease.lease_id,
        sender="local-worker-shadow",
        recipient="LivingSystem.AgenticHarness",
        status="SUCCEEDED",
        payload={"summary": "inspection result is recorded", "evidence_count": 1},
    )
    mailbox.write_result(result_envelope)
    imported = runtime.agentic.import_node_result_envelope(
        mailbox_root=mailbox_root,
        message_id=result_envelope.message_id,
        acceptance_checks=[
            {
                "check_id": "summary",
                "type": "regex",
                "config": {"field": "payload", "pattern": "recorded"},
            }
        ],
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_envelope_exported',
                'agentic_result_envelope_imported',
                'agentic_task_node_acceptance_evaluated'
            )
            """
        )
    }
    graph = runtime.agentic.load_graph(graph_id)
    if (
        imported["status"] != "SUCCEEDED"
        or graph.nodes[lease.node_id].status.value != "SUCCEEDED"
        or panel is None
        or panel["status"]["file_mailbox_v1"]["receipt_count"] < 2
        or {
            "agentic_task_envelope_exported",
            "agentic_result_envelope_imported",
            "agentic_task_node_acceptance_evaluated",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P44",
            "BLOCKED",
            [graph_id],
            ["agentic file mailbox handoff validation failed"],
        )
    return ArchitecturePassResult(
        "P44",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            lease.lease_id,
            str(exported["message_id"]),
            result_envelope.message_id,
            *sorted(event_types),
        ],
        [
            "Agentic file mailbox handoff exports and imports local task/result envelopes as transport artifacts while canonical completion remains with LivingSystem.AgenticHarness",
        ],
    )


def validate_phase2_agentic_repair_candidate(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    failure = runtime.agentic.fail_node(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        error="fixture validation mismatch: inspection evidence missing",
    )
    repair = runtime.agentic.propose_repair_candidate(
        graph_id,
        lease.node_id,
        reason="architecture validation failed node requires bounded repair candidate",
    )
    graph = runtime.agentic.load_graph(graph_id)
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_node_failed',
                'agentic_repair_candidate_proposed'
            )
            """
        )
    }
    if (
        failure["status"] != "FAILED"
        or graph.nodes[lease.node_id].status.value != "FAILED"
        or repair["state_mutated"] is not False
        or repair["direct_execution"] is not False
        or not repair["provenance"].get("failure_attribution")
        or not repair["candidate_steps"]
        or panel is None
        or panel["status"]["repair_candidates_v1"]["receipt_count"] != 1
        or {"agentic_task_node_failed", "agentic_repair_candidate_proposed"}
        - event_types
    ):
        return ArchitecturePassResult(
            "P45",
            "BLOCKED",
            [graph_id],
            ["agentic repair candidate validation failed"],
        )
    return ArchitecturePassResult(
        "P45",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            lease.lease_id,
            str(repair["repair_id"]),
            *sorted(event_types),
        ],
        [
            "Agentic repair candidates preserve failure provenance and Owner Console visibility without resetting node state or executing repair outside the canonical harness",
        ],
    )


def validate_phase2_agentic_budget_gate(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    reserved = runtime.agentic.reserve_node_budget(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        request={"tokens": 100, "seconds": 1, "calls": 1, "cost_usd": 0.0},
        limit={"max_tokens": 150, "max_seconds": 10, "max_calls": 1, "max_cost_usd": 0.0},
        reason="architecture validation bounded read-only node budget",
    )
    blocked = runtime.agentic.reserve_node_budget(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        request={"tokens": 75, "seconds": 1, "calls": 1, "cost_usd": 0.0},
        limit={"max_tokens": 150, "max_seconds": 10, "max_calls": 1, "max_cost_usd": 0.0},
        reason="architecture validation over-budget node attempt",
    )
    graph = runtime.agentic.load_graph(graph_id)
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_node_budget_reserved',
                'agentic_node_budget_blocked'
            )
            """
        )
    }
    if (
        reserved["status"] != "RESERVED"
        or blocked["status"] != "BLOCKED"
        or "tokens" not in blocked["exceeded"]
        or "calls" not in blocked["exceeded"]
        or graph.nodes[lease.node_id].status.value != "LEASED"
        or panel is None
        or panel["status"]["budget_gate_v1"]["receipt_count"] != 2
        or {"agentic_node_budget_reserved", "agentic_node_budget_blocked"}
        - event_types
    ):
        return ArchitecturePassResult(
            "P46",
            "BLOCKED",
            [graph_id],
            ["agentic budget gate validation failed"],
        )
    return ArchitecturePassResult(
        "P46",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            lease.lease_id,
            str(reserved["budget_id"]),
            str(blocked["budget_id"]),
            *sorted(event_types),
        ],
        [
            "Agentic node budget gates reserve and block bounded usage through EvidenceLedger and Owner Console without provider calls, tool execution, or node state mutation",
        ],
    )


def validate_phase2_agentic_benchmark_scorecard(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    trace_receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    trace_graph_id = str(trace_receipt["graph"]["graph_id"])
    trace_lease = runtime.agentic.acquire_ready_leases(
        trace_graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    runtime.agentic.complete_node_with_acceptance(
        trace_graph_id,
        trace_lease.node_id,
        lease_id=trace_lease.lease_id,
        result={
            "status": "SUCCEEDED",
            "summary": "inspection result is recorded",
            "evidence": ["agentic_task_graph_compiled"],
        },
        acceptance_checks=[
            {"check_id": "status", "type": "result_status"},
            {
                "check_id": "evidence",
                "type": "evidence_min",
                "config": {"minimum": 1},
            },
        ],
    )
    failed_receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    failed_graph_id = str(failed_receipt["graph"]["graph_id"])
    failed_lease = runtime.agentic.acquire_ready_leases(
        failed_graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    runtime.agentic.fail_node(
        failed_graph_id,
        failed_lease.node_id,
        lease_id=failed_lease.lease_id,
        error="fixture validation mismatch: inspection evidence missing",
    )
    runtime.agentic.propose_repair_candidate(
        failed_graph_id,
        failed_lease.node_id,
        reason="architecture validation benchmark failure closure",
    )
    budget_receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    budget_graph_id = str(budget_receipt["graph"]["graph_id"])
    budget_lease = runtime.agentic.acquire_ready_leases(
        budget_graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    runtime.agentic.reserve_node_budget(
        budget_graph_id,
        budget_lease.node_id,
        lease_id=budget_lease.lease_id,
        request={"tokens": 50, "seconds": 2, "calls": 1, "cost_usd": 0.0},
        limit={"max_tokens": 100, "max_seconds": 10, "max_calls": 1, "max_cost_usd": 0.0},
        reason="architecture validation benchmark budget source",
    )
    scorecard = runtime.record_agentic_benchmark_scorecard(
        reason="architecture validation agentic benchmark scorecard"
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_benchmark_scorecard_recorded',
                'agentic_task_node_acceptance_evaluated',
                'agentic_repair_candidate_proposed',
                'agentic_node_budget_reserved'
            )
            """
        )
    }
    summary = scorecard["summary"]
    if (
        scorecard["status"] != "RECORDED"
        or summary["cases"] != 1
        or summary["task_success_rate"] != 1.0
        or summary["acceptance_coverage"] != 1.0
        or summary["evidence_coverage"] != 1.0
        or summary["hidden_failures"] != 0
        or summary["latency_seconds"] != 2.0
        or panel is None
        or panel["status"]["benchmark_scorecard_v1"]["receipt_count"] != 1
        or {
            "agentic_benchmark_scorecard_recorded",
            "agentic_task_node_acceptance_evaluated",
            "agentic_repair_candidate_proposed",
            "agentic_node_budget_reserved",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P47",
            "BLOCKED",
            [str(scorecard)],
            ["agentic benchmark scorecard validation failed"],
        )
    return ArchitecturePassResult(
        "P47",
        "ADMIT_SHADOW_ONLY",
        [
            trace_graph_id,
            failed_graph_id,
            budget_graph_id,
            *sorted(event_types),
        ],
        [
            "Agentic benchmark scorecards summarize existing acceptance, failure, repair, and budget receipts through LivingSystem without executing external benchmarks or claiming product readiness",
        ],
    )


def validate_phase2_agentic_checkpoint_resume(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id="readonly-inspector",
        limit=1,
        ttl_seconds=1,
    )[0]
    checkpoint = runtime.agentic.record_graph_checkpoint(
        graph_id, reason="architecture validation checkpoint before restart"
    )
    resume_at = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
    resumed = runtime.agentic.resume_expired_leases(
        graph_id,
        reason="architecture validation resume expired node lease",
        now=resume_at,
    )
    restarted = AgenticHarness(runtime.db, runtime.ledger)
    reacquired = restarted.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=1
    )
    graph = restarted.load_graph(graph_id)
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_graph_checkpoint_recorded',
                'agentic_graph_resume_recorded',
                'agentic_task_node_leased'
            )
            """
        )
    }
    if (
        checkpoint["active_lease_count"] != 1
        or resumed["resumed_node_count"] != 1
        or not reacquired
        or reacquired[0].node_id != lease.node_id
        or reacquired[0].lease_id == lease.lease_id
        or graph.nodes[lease.node_id].status.value != "LEASED"
        or panel is None
        or panel["status"]["checkpoint_resume_v1"]["receipt_count"] != 2
        or {
            "agentic_graph_checkpoint_recorded",
            "agentic_graph_resume_recorded",
            "agentic_task_node_leased",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P48",
            "BLOCKED",
            [graph_id],
            ["agentic checkpoint/resume validation failed"],
        )
    return ArchitecturePassResult(
        "P48",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            lease.lease_id,
            reacquired[0].lease_id,
            *sorted(event_types),
        ],
        [
            "Agentic checkpoint/resume records graph and lease state, expires abandoned leases, and lets a restarted canonical harness reacquire work without inferring worker success or executing retries",
        ],
    )


def validate_phase2_agentic_retry_gate(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    runtime.agentic.fail_node(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        error="fixture validation mismatch: inspection evidence missing",
    )
    repair = runtime.agentic.propose_repair_candidate(
        graph_id,
        lease.node_id,
        reason="architecture validation retry repair candidate",
    )
    retry = runtime.agentic.prepare_node_retry(
        graph_id,
        lease.node_id,
        repair_id=str(repair["repair_id"]),
        reason="architecture validation prepare local retry",
    )
    reacquired = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=1
    )
    graph = runtime.agentic.load_graph(graph_id)
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_node_failed',
                'agentic_repair_candidate_proposed',
                'agentic_node_retry_prepared',
                'agentic_task_node_leased'
            )
            """
        )
    }
    if (
        retry["new_status"] != "READY"
        or retry["retry_executed"]
        or retry["downstream_unblocked"]
        or not reacquired
        or reacquired[0].node_id != lease.node_id
        or graph.nodes[lease.node_id].status.value != "LEASED"
        or panel is None
        or panel["status"]["retry_gate_v1"]["receipt_count"] != 1
        or {
            "agentic_task_node_failed",
            "agentic_repair_candidate_proposed",
            "agentic_node_retry_prepared",
            "agentic_task_node_leased",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P49",
            "BLOCKED",
            [graph_id],
            ["agentic retry gate validation failed"],
        )
    return ArchitecturePassResult(
        "P49",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            lease.lease_id,
            reacquired[0].lease_id,
            str(retry["retry_id"]),
            *sorted(event_types),
        ],
        [
            "Agentic retry gates can return a failed node with a repair candidate to READY and allow a new lease without executing the retry or inferring repair success",
        ],
    )


def validate_phase2_agentic_replan_candidate(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    runtime.agentic.fail_node(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        error="fixture validation mismatch: inspection evidence missing",
    )
    before = runtime.agentic.load_graph(graph_id).snapshot()["graph_digest"]
    replan = runtime.agentic.propose_replan_candidate(
        graph_id,
        trigger_node_id=lease.node_id,
        reason="architecture validation failed graph needs replan candidate",
    )
    after_graph = runtime.agentic.load_graph(graph_id)
    after = after_graph.snapshot()["graph_digest"]
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_node_failed',
                'agentic_replan_candidate_proposed'
            )
            """
        )
    }
    if (
        replan["state_mutated"]
        or replan["graph_changed"]
        or not replan["graph_revision"]["can_revise"]
        or not replan["problem_nodes"]
        or before != after
        or after_graph.nodes[lease.node_id].status.value != "FAILED"
        or panel is None
        or panel["status"]["replan_candidates_v1"]["receipt_count"] != 1
        or {"agentic_task_node_failed", "agentic_replan_candidate_proposed"}
        - event_types
    ):
        return ArchitecturePassResult(
            "P50",
            "BLOCKED",
            [graph_id],
            ["agentic replan candidate validation failed"],
        )
    return ArchitecturePassResult(
        "P50",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            lease.lease_id,
            str(replan["replan_id"]),
            *sorted(event_types),
        ],
        [
            "Agentic replan candidates describe bounded graph revision options from failed graph state without mutating the graph or inferring task completion",
        ],
    )


def validate_phase2_agentic_role_context_packets(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    safe_memory_id = runtime.memories.add(
        MemoryItem(
            memory_type="project_note",
            content={"summary": "Architecture validation context note."},
            importance=0.9,
            confidence=0.8,
            source_ids=["architecture-validation:safe-context"],
            tags=["agentic_context"],
        )
    )
    secret_memory_id = runtime.memories.add(
        MemoryItem(
            memory_type="project_note",
            content={"summary": "temporary token must be suppressed"},
            importance=1.0,
            confidence=0.8,
            source_ids=["architecture-validation:sensitive-context"],
            tags=["agentic_context"],
        )
    )
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    packet_receipt = runtime.agentic.render_context_packet(
        graph_id,
        role="executor",
        token_budget=1200,
        reason="architecture validation role context packet",
    )
    packet = packet_receipt["packet"]
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_graph_compiled',
                'agentic_context_packet_rendered'
            )
            """
        )
    }
    if (
        safe_memory_id not in packet["included_record_ids"]
        or secret_memory_id in packet["included_record_ids"]
        or {
            "record_id": secret_memory_id,
            "reason": "sensitive_context_filter",
        }
        not in packet["suppressed_records"]
        or packet["secret_material_present"] is not False
        or packet["raw_database_export"] is not False
        or packet_receipt["worker_execution"] is not False
        or panel is None
        or panel["status"]["role_context_packets_v1"]["receipt_count"] != 1
        or {
            "agentic_task_graph_compiled",
            "agentic_context_packet_rendered",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P71",
            "BLOCKED",
            [graph_id, str(packet_receipt)],
            ["agentic role context packet validation failed"],
        )
    return ArchitecturePassResult(
        "P71",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            str(packet["packet_id"]),
            str(packet["packet_digest"]),
            *sorted(event_types),
        ],
        [
            "Role-scoped context packets render from canonical manifests with token budget, sensitive-record suppression, and Owner Console visibility without exporting raw database state or executing workers",
        ],
    )


def validate_phase2_agentic_context_epoch_checkpoint(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    planner = runtime.agentic.render_context_packet(
        graph_id,
        role="planner",
        reason="architecture validation planner packet",
    )
    reviewer = runtime.agentic.render_context_packet(
        graph_id,
        role="reviewer",
        reason="architecture validation reviewer packet",
    )
    epoch = runtime.agentic.record_context_epoch(
        graph_id,
        reason="architecture validation context epoch checkpoint",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_graph_compiled',
                'agentic_context_packet_rendered',
                'agentic_context_epoch_recorded'
            )
            """
        )
    }
    if (
        epoch["manifest_ref"]["manifest_digest"]
        != receipt["context_manifest"]["manifest_digest"]
        or {
            planner["packet_digest"],
            reviewer["packet_digest"],
        }
        != {item["packet_digest"] for item in epoch["packet_refs"]}
        or epoch["critical_evidence_ref_count"] < 3
        or epoch["safe_boundary"] is not True
        or epoch["raw_transcript_replaced"] is not False
        or epoch["original_receipts_preserved"] is not True
        or epoch["worker_execution"] is not False
        or epoch["memory_write"] is not False
        or panel is None
        or panel["status"]["context_epoch_v1"]["receipt_count"] != 1
        or {
            "agentic_task_graph_compiled",
            "agentic_context_packet_rendered",
            "agentic_context_epoch_recorded",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P72",
            "BLOCKED",
            [graph_id, str(epoch)],
            ["agentic context epoch checkpoint validation failed"],
        )
    return ArchitecturePassResult(
        "P72",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            str(epoch["epoch_id"]),
            str(epoch["epoch_digest"]),
            *sorted(event_types),
        ],
        [
            "Context epoch checkpoints preserve manifest, role-packet, graph, and evidence references at a safe boundary without replacing original receipts, writing memory, or executing workers",
        ],
    )


def validate_phase2_agentic_process_auditor(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    pass_receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    pass_graph_id = str(pass_receipt["graph"]["graph_id"])
    pass_lease = runtime.agentic.acquire_ready_leases(
        pass_graph_id,
        worker_id="readonly-inspector",
    )[0]
    runtime.agentic.complete_node_with_acceptance(
        pass_graph_id,
        pass_lease.node_id,
        lease_id=pass_lease.lease_id,
        result={
            "status": "SUCCEEDED",
            "summary": "inspection result is recorded",
            "evidence": ["agentic_task_graph_compiled"],
        },
        acceptance_checks=[
            {"check_id": "status", "type": "result_status"},
            {
                "check_id": "evidence",
                "type": "evidence_min",
                "config": {"minimum": 1},
            },
        ],
    )
    passed = runtime.agentic.audit_graph_process(
        pass_graph_id,
        reason="architecture validation process audit pass",
    )
    review_receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    review_graph_id = str(review_receipt["graph"]["graph_id"])
    review_lease = runtime.agentic.acquire_ready_leases(
        review_graph_id,
        worker_id="readonly-inspector",
    )[0]
    runtime.agentic.complete_node(
        review_graph_id,
        review_lease.node_id,
        lease_id=review_lease.lease_id,
        result={"status": "SUCCEEDED", "summary": "inspection result is recorded"},
    )
    reviewed = runtime.agentic.audit_graph_process(
        review_graph_id,
        reason="architecture validation process audit review",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_node_acceptance_evaluated',
                'agentic_task_node_completed',
                'agentic_process_audit_recorded'
            )
            """
        )
    }
    if (
        passed["status"] != "PASS"
        or passed["issues"]
        or reviewed["status"] != "REVIEW_REQUIRED"
        or reviewed["critical_count"] != 0
        or reviewed["issues"][0]["type"] != "succeeded_without_acceptance_trace"
        or reviewed["completion_inferred"] is not False
        or reviewed["repair_inferred"] is not False
        or reviewed["worker_execution"] is not False
        or panel is None
        or panel["status"]["process_auditor_v1"]["receipt_count"] != 2
        or {
            "agentic_task_node_acceptance_evaluated",
            "agentic_task_node_completed",
            "agentic_process_audit_recorded",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P73",
            "BLOCKED",
            [pass_graph_id, review_graph_id, str(reviewed)],
            ["agentic process auditor validation failed"],
        )
    return ArchitecturePassResult(
        "P73",
        "ADMIT_SHADOW_ONLY",
        [
            pass_graph_id,
            review_graph_id,
            str(reviewed["audit_id"]),
            *sorted(event_types),
        ],
        [
            "Agentic process auditor distinguishes machine-accepted PASS from green-but-unaccepted REVIEW_REQUIRED nodes and records the gap without completing, repairing, or executing worker tasks",
        ],
    )


def validate_phase2_agentic_worker_lifecycle(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    worker = WorkerProfile(
        worker_id="validation-stale-worker",
        worker_type="LOCAL_SHADOW",
        label="Validation stale worker",
        allowed_domains=["RESEARCH", "MIXED", "CODE"],
        max_risk=RiskLevel.READ,
        metadata={"validation": "worker lifecycle"},
    )
    registered = runtime.agentic.worker_registry.register_profile(
        worker,
        reason="architecture validation worker lifecycle registration",
    )
    heartbeat = runtime.agentic.worker_registry.record_heartbeat(
        worker.worker_id,
        details={"phase": "validation-heartbeat"},
    )
    stale_cutoff = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    stale = runtime.agentic.worker_registry.mark_stale_workers(
        stale_before=stale_cutoff,
        reason="architecture validation stale worker cutoff",
    )
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository documents",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    stale_blocked = False
    try:
        runtime.agentic.acquire_ready_leases(
            graph_id,
            worker_id=worker.worker_id,
            limit=1,
        )
    except PermissionError:
        stale_blocked = True
    default_lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id="readonly-inspector",
        limit=1,
    )[0]
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_worker_profile_registered',
                'agentic_worker_heartbeat_recorded',
                'agentic_worker_lifecycle_marked_stale',
                'agentic_task_node_leased'
            )
            """
        )
    }
    if (
        registered["worker_id"] != worker.worker_id
        or heartbeat["status"] != "ACTIVE"
        or stale["stale_count"] < 1
        or not stale_blocked
        or default_lease.worker_id != "readonly-inspector"
        or panel is None
        or panel["status"]["worker_lifecycle_v1"]["receipt_count"] < 3
        or {
            "agentic_worker_profile_registered",
            "agentic_worker_heartbeat_recorded",
            "agentic_worker_lifecycle_marked_stale",
            "agentic_task_node_leased",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P51",
            "BLOCKED",
            [graph_id, str(stale)],
            ["agentic worker lifecycle validation failed"],
        )
    return ArchitecturePassResult(
        "P51",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            worker.worker_id,
            default_lease.lease_id,
            *sorted(event_types),
        ],
        [
            "Agentic worker lifecycle records registration, heartbeat, stale marking, and stale-worker lease rejection while preserving LivingSystem as the only authority",
        ],
    )


def validate_phase2_agentic_result_replay_quarantine(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    mailbox_root = home / "mailbox"
    receipt = runtime.agentic.admit_and_compile(
        "Inspect replay-safe worker result",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id="readonly-inspector",
    )[0]
    exported = runtime.agentic.export_node_task_envelope(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        mailbox_root=mailbox_root,
        recipient="validation-worker",
    )
    result = ResultEnvelope.create(
        message_id="validation-result-replay",
        in_reply_to=str(exported["message_id"]),
        graph_id=graph_id,
        node_id=lease.node_id,
        lease_id=lease.lease_id,
        sender="validation-worker",
        recipient="LivingSystem.AgenticHarness",
        status="SUCCEEDED",
        payload={"summary": "first import"},
    )
    mailbox = AgenticFileMailbox(mailbox_root)
    mailbox.write_result(result)
    imported = runtime.agentic.import_node_result_envelope(
        mailbox_root=mailbox_root,
        message_id=result.message_id,
    )
    mailbox.write_result(result)
    quarantined = runtime.agentic.import_node_result_envelope(
        mailbox_root=mailbox_root,
        message_id=result.message_id,
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_task_envelope_exported',
                'agentic_result_envelope_imported',
                'agentic_result_envelope_quarantined',
                'agentic_task_node_completed'
            )
            """
        )
    }
    rejected_path = mailbox_root / "rejected" / f"{result.message_id}.json"
    if (
        imported.get("receipt_type") != "AGENTIC_RESULT_ENVELOPE_IMPORTED"
        or quarantined.get("receipt_type")
        != "AGENTIC_RESULT_ENVELOPE_QUARANTINED"
        or quarantined.get("completion_attempted") is not False
        or quarantined.get("reason") != "duplicate_result_replay"
        or not rejected_path.exists()
        or panel is None
        or panel["status"]["file_mailbox_v1"]["receipt_count"] < 3
        or {
            "agentic_task_envelope_exported",
            "agentic_result_envelope_imported",
            "agentic_result_envelope_quarantined",
            "agentic_task_node_completed",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P52",
            "BLOCKED",
            [graph_id, str(quarantined)],
            ["agentic result replay quarantine validation failed"],
        )
    return ArchitecturePassResult(
        "P52",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            result.message_id,
            str(rejected_path),
            *sorted(event_types),
        ],
        [
            "Agentic result replay quarantine rejects duplicate worker result envelopes before a second completion attempt while preserving LivingSystem as canonical completion authority",
        ],
    )


def validate_phase2_agentic_worker_lease_recovery(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    worker = WorkerProfile(
        worker_id="validation-recover-worker",
        worker_type="LOCAL_SHADOW",
        label="Validation recover worker",
        allowed_domains=["RESEARCH", "MIXED", "CODE"],
        max_risk=RiskLevel.READ,
        metadata={"validation": "worker lease recovery"},
    )
    runtime.agentic.worker_registry.register_profile(
        worker,
        reason="architecture validation lease recovery registration",
    )
    receipt = runtime.agentic.admit_and_compile(
        "Inspect stale worker recovery",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id=worker.worker_id,
        ttl_seconds=30,
    )[0]
    heartbeat = runtime.agentic.record_lease_heartbeat(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        extend_seconds=60,
        reason="architecture validation lease heartbeat",
    )
    stale_cutoff = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    runtime.agentic.worker_registry.mark_stale_workers(
        stale_before=stale_cutoff,
        reason="architecture validation worker stale before recovery",
    )
    runtime.agentic.worker_registry.record_heartbeat(
        "readonly-inspector",
        details={"phase": "validation-reacquire-worker-active"},
    )
    recovery = runtime.agentic.recover_stale_worker_leases(
        graph_id,
        worker_id=worker.worker_id,
        reason="architecture validation stale worker lease recovery",
    )
    reacquired = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id="readonly-inspector",
    )[0]
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_lease_heartbeat_recorded',
                'agentic_stale_worker_leases_recovered',
                'agentic_worker_lifecycle_marked_stale',
                'agentic_task_node_leased'
            )
            """
        )
    }
    graph = runtime.agentic.load_graph(graph_id)
    if (
        heartbeat["lease_id"] != lease.lease_id
        or heartbeat["expires_at"] == heartbeat["previous_expires_at"]
        or recovery["recovered_lease_count"] != 1
        or recovery["recovered_nodes"][0]["expired_lease_id"] != lease.lease_id
        or reacquired.node_id != lease.node_id
        or reacquired.lease_id == lease.lease_id
        or graph.nodes[lease.node_id].status is not TaskNodeStatus.LEASED
        or graph.nodes[lease.node_id].worker_id != "readonly-inspector"
        or panel is None
        or panel["status"]["worker_lease_recovery_v1"]["receipt_count"] < 2
        or {
            "agentic_lease_heartbeat_recorded",
            "agentic_stale_worker_leases_recovered",
            "agentic_worker_lifecycle_marked_stale",
            "agentic_task_node_leased",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P53",
            "BLOCKED",
            [graph_id, str(recovery)],
            ["agentic worker lease recovery validation failed"],
        )
    return ArchitecturePassResult(
        "P53",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            lease.lease_id,
            reacquired.lease_id,
            *sorted(event_types),
        ],
        [
            "Agentic worker lease recovery records lease heartbeat and returns stale-worker leases to READY for canonical reacquisition without executing retry or inferring worker success",
        ],
    )


def validate_phase2_agentic_worker_capability_arbitration(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect worker capability card routing",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    graph = runtime.agentic.load_graph(graph_id)
    node = graph.ready_frontier()[0]
    incompatible = WorkerProfile(
        worker_id="validation-incompatible-worker",
        worker_type="LOCAL_SHADOW",
        label="Validation incompatible worker",
        allowed_domains=["CODE"],
        max_risk=RiskLevel.READ,
        metadata={"version": "validation-1", "auth_scheme": "none"},
    )
    runtime.agentic.worker_registry.register_profile(
        incompatible,
        status="STALE",
        reason="architecture validation incompatible worker card",
    )
    arbitration = runtime.agentic.propose_worker_candidates(
        graph_id,
        node.node_id,
        reason="architecture validation worker capability arbitration",
    )
    after = runtime.agentic.load_graph(graph_id)
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_worker_candidate_arbitrated',
                'agentic_worker_profile_registered'
            )
            """
        )
    }
    eligible = arbitration.get("eligible_workers", [])
    first_card = eligible[0]["card"] if eligible else {}
    if (
        arbitration["eligible_count"] < 1
        or arbitration["rejected_count"] < 1
        or arbitration["recommended_worker_id"] is None
        or arbitration["lease_created"] is not False
        or arbitration["selection_executed"] is not False
        or first_card.get("schema_version") != "wls.worker_card.v1"
        or first_card.get("secret_material_present") is not False
        or after.nodes[node.node_id].status is not TaskNodeStatus.READY
        or panel is None
        or panel["status"]["worker_arbitration_v1"]["receipt_count"] < 1
        or {
            "agentic_worker_candidate_arbitrated",
            "agentic_worker_profile_registered",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P54",
            "BLOCKED",
            [graph_id, str(arbitration)],
            ["agentic worker capability arbitration validation failed"],
        )
    return ArchitecturePassResult(
        "P54",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            node.node_id,
            str(arbitration["recommended_worker_id"]),
            *sorted(event_types),
        ],
        [
            "Agentic worker capability cards and candidate arbitration select compatible active workers without creating leases, executing workers, or delegating canonical authority",
        ],
    )


def validate_phase2_agentic_lease_fencing_reconciliation(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    mailbox_root = home / "mailbox"
    worker = WorkerProfile(
        worker_id="validation-fenced-worker",
        worker_type="LOCAL_SHADOW",
        label="Validation fenced worker",
        allowed_domains=["RESEARCH", "MIXED", "CODE"],
        max_risk=RiskLevel.READ,
        metadata={"validation": "lease fencing"},
    )
    runtime.agentic.worker_registry.register_profile(
        worker,
        reason="architecture validation fenced worker registration",
    )
    receipt = runtime.agentic.admit_and_compile(
        "Inspect stale lease fencing",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    old_lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id=worker.worker_id,
        ttl_seconds=30,
    )[0]
    exported = runtime.agentic.export_node_task_envelope(
        graph_id,
        old_lease.node_id,
        lease_id=old_lease.lease_id,
        mailbox_root=mailbox_root,
        recipient=worker.worker_id,
    )
    mailbox = AgenticFileMailbox(mailbox_root)
    task = mailbox.read_task(str(exported["message_id"]))
    runtime.agentic.worker_registry.mark_stale_workers(
        stale_before=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        reason="architecture validation stale worker before fencing",
    )
    runtime.agentic.worker_registry.record_heartbeat(
        "readonly-inspector",
        details={"phase": "validation-fencing-reacquire-worker-active"},
    )
    recovery = runtime.agentic.recover_stale_worker_leases(
        graph_id,
        worker_id=worker.worker_id,
        reason="architecture validation stale lease fencing recovery",
    )
    new_lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id="readonly-inspector",
    )[0]
    late_result = ResultEnvelope.create(
        message_id="validation-late-result",
        in_reply_to=task.message_id,
        graph_id=graph_id,
        node_id=old_lease.node_id,
        lease_id=old_lease.lease_id,
        sender=worker.worker_id,
        recipient="LivingSystem.AgenticHarness",
        status="SUCCEEDED",
        payload={
            "summary": "late stale worker result",
            "lease_fencing_token": task.payload["lease_fencing_token"],
        },
    )
    mailbox.write_result(late_result)
    quarantined = runtime.agentic.import_node_result_envelope(
        mailbox_root=mailbox_root,
        message_id=late_result.message_id,
    )
    graph = runtime.agentic.load_graph(graph_id)
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_result_envelope_quarantined',
                'agentic_stale_worker_leases_recovered',
                'agentic_task_node_completed',
                'agentic_task_node_leased'
            )
            """
        )
    }
    rejected_path = mailbox_root / "rejected" / f"{late_result.message_id}.json"
    if (
        recovery["recovered_lease_count"] != 1
        or new_lease.node_id != old_lease.node_id
        or new_lease.lease_id == old_lease.lease_id
        or quarantined.get("receipt_type") != "AGENTIC_RESULT_ENVELOPE_QUARANTINED"
        or quarantined.get("reason") != "inactive_lease"
        or quarantined.get("completion_attempted") is not False
        or not rejected_path.exists()
        or graph.nodes[old_lease.node_id].status is not TaskNodeStatus.LEASED
        or graph.nodes[old_lease.node_id].lease_id != new_lease.lease_id
        or "agentic_task_node_completed" in event_types
        or {
            "agentic_result_envelope_quarantined",
            "agentic_stale_worker_leases_recovered",
            "agentic_task_node_leased",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P55",
            "BLOCKED",
            [graph_id, str(quarantined)],
            ["agentic lease fencing reconciliation validation failed"],
        )
    return ArchitecturePassResult(
        "P55",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            old_lease.lease_id,
            new_lease.lease_id,
            str(rejected_path),
            *sorted(event_types),
        ],
        [
            "Agentic lease fencing quarantines late stale-worker results after recovery without completing the node or granting worker authority",
        ],
    )


def validate_phase2_agentic_artifact_finalize_acceptance(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    mailbox_root = home / "mailbox"
    receipt = runtime.agentic.admit_and_compile(
        "Inspect finalized mailbox artifact",
        acceptance=["finalized artifact digest is accepted"],
        evidence_required=["agentic_mailbox_artifact_finalized"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id="readonly-inspector",
    )[0]
    exported = runtime.agentic.export_node_task_envelope(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        mailbox_root=mailbox_root,
        recipient="validation-artifact-worker",
    )
    mailbox = AgenticFileMailbox(mailbox_root)
    task = mailbox.read_task(str(exported["message_id"]))
    artifact_id = "validation-artifact"
    chunks = [b"WLS finalized ", b"artifact evidence\n"]
    expected_sha256 = hashlib.sha256(b"".join(chunks)).hexdigest()
    for index, chunk in enumerate(chunks):
        mailbox.write_artifact_chunk(artifact_id, index, chunk)
    finalized = runtime.agentic.finalize_mailbox_artifact(
        mailbox_root=mailbox_root,
        artifact_id=artifact_id,
        chunk_count=len(chunks),
        expected_sha256=expected_sha256,
        reason="architecture validation finalized artifact",
    )
    result = ResultEnvelope.create(
        message_id="validation-artifact-result",
        in_reply_to=task.message_id,
        graph_id=graph_id,
        node_id=lease.node_id,
        lease_id=lease.lease_id,
        sender="validation-artifact-worker",
        recipient="LivingSystem.AgenticHarness",
        status="SUCCEEDED",
        payload={
            "summary": "finalized artifact result",
            "artifact_id": artifact_id,
            "artifact_sha256": expected_sha256,
            "lease_fencing_token": task.payload["lease_fencing_token"],
        },
    )
    mailbox.write_result(result)
    imported = runtime.agentic.import_node_result_envelope(
        mailbox_root=mailbox_root,
        message_id=result.message_id,
        acceptance_checks=[
            {
                "check_id": "finalized-artifact-sha256",
                "type": "artifact_sha256",
                "config": {
                    "path": f"{artifact_id}.bin",
                    "sha256": expected_sha256,
                },
                "critical": True,
            }
        ],
        artifact_root=mailbox_root / "artifacts",
    )
    graph = runtime.agentic.load_graph(graph_id)
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_mailbox_artifact_finalized',
                'agentic_result_envelope_imported',
                'agentic_task_node_acceptance_evaluated',
                'agentic_task_node_completed'
            )
            """
        )
    }
    if (
        finalized["sha256"] != expected_sha256
        or finalized["completion_attempted"] is not False
        or imported["completion"]["acceptance_report"]["passed"] is not True
        or graph.nodes[lease.node_id].status is not TaskNodeStatus.SUCCEEDED
        or {
            "agentic_mailbox_artifact_finalized",
            "agentic_result_envelope_imported",
            "agentic_task_node_acceptance_evaluated",
            "agentic_task_node_completed",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P56",
            "BLOCKED",
            [graph_id, str(imported)],
            ["agentic artifact finalize acceptance validation failed"],
        )
    return ArchitecturePassResult(
        "P56",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            artifact_id,
            expected_sha256,
            *sorted(event_types),
        ],
        [
            "Agentic mailbox artifact chunks finalize to a digest-checked artifact before canonical acceptance consumes the worker result",
        ],
    )


def validate_phase2_agentic_worker_trust_quarantine(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    mailbox_root = home / "mailbox"
    worker = WorkerProfile(
        worker_id="validation-trust-worker",
        worker_type="LOCAL_SHADOW",
        label="Validation trust worker",
        allowed_domains=["RESEARCH", "MIXED", "CODE"],
        max_risk=RiskLevel.READ,
        metadata={"validation": "worker trust"},
    )
    runtime.agentic.worker_registry.register_profile(
        worker,
        reason="architecture validation trust worker registration",
    )
    receipt = runtime.agentic.admit_and_compile(
        "Inspect worker trust quarantine",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_worker_trust_reviewed"],
    )
    graph_id = str(receipt["graph"]["graph_id"])
    old_lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id=worker.worker_id,
        ttl_seconds=30,
    )[0]
    exported = runtime.agentic.export_node_task_envelope(
        graph_id,
        old_lease.node_id,
        lease_id=old_lease.lease_id,
        mailbox_root=mailbox_root,
        recipient=worker.worker_id,
    )
    mailbox = AgenticFileMailbox(mailbox_root)
    task = mailbox.read_task(str(exported["message_id"]))
    runtime.agentic.worker_registry.mark_stale_workers(
        stale_before=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        reason="architecture validation trust worker stale",
    )
    runtime.agentic.worker_registry.record_heartbeat(
        "readonly-inspector",
        details={"phase": "validation-trust-reacquire-worker-active"},
    )
    runtime.agentic.recover_stale_worker_leases(
        graph_id,
        worker_id=worker.worker_id,
        reason="architecture validation trust recovery",
    )
    runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id="readonly-inspector",
    )[0]
    late_result = ResultEnvelope.create(
        message_id="validation-trust-late-result",
        in_reply_to=task.message_id,
        graph_id=graph_id,
        node_id=old_lease.node_id,
        lease_id=old_lease.lease_id,
        sender=worker.worker_id,
        recipient="LivingSystem.AgenticHarness",
        status="SUCCEEDED",
        payload={
            "summary": "late trust review result",
            "lease_fencing_token": task.payload["lease_fencing_token"],
        },
    )
    mailbox.write_result(late_result)
    quarantined = runtime.agentic.import_node_result_envelope(
        mailbox_root=mailbox_root,
        message_id=late_result.message_id,
    )
    trust = runtime.agentic.worker_registry.review_worker_trust(
        worker.worker_id,
        reason="architecture validation trust quarantine review",
    )
    blocked = False
    new_receipt = runtime.agentic.admit_and_compile(
        "Inspect disabled worker lease block",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    try:
        runtime.agentic.acquire_ready_leases(
            str(new_receipt["graph"]["graph_id"]),
            worker_id=worker.worker_id,
        )
    except PermissionError:
        blocked = True
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "agentic_tasks"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'agentic_result_envelope_quarantined',
                'agentic_worker_trust_reviewed'
            )
            """
        )
    }
    if (
        quarantined.get("receipt_type") != "AGENTIC_RESULT_ENVELOPE_QUARANTINED"
        or trust["trust_state"] != "QUARANTINED"
        or trust["status_after_review"] != "DISABLED"
        or trust["quarantine_count"] < 1
        or trust["self_report_used"] is not False
        or not blocked
        or panel is None
        or panel["status"]["worker_trust_v1"]["receipt_count"] < 1
        or {
            "agentic_result_envelope_quarantined",
            "agentic_worker_trust_reviewed",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P57",
            "BLOCKED",
            [graph_id, str(trust)],
            ["agentic worker trust quarantine validation failed"],
        )
    return ArchitecturePassResult(
        "P57",
        "ADMIT_SHADOW_ONLY",
        [
            graph_id,
            worker.worker_id,
            str(trust["review_id"]),
            *sorted(event_types),
        ],
        [
            "Agentic worker trust review derives quarantine from canonical evidence and disables future leases without using worker self-report",
        ],
    )


def validate_phase2_sandbox_adapter_contract(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.run_sandbox_adapter_probe(
        adapter_id="validation-local-fixture",
        tool="write_file",
        arguments={
            "path": "outputs/probe.txt",
            "content": "P58 sandbox adapter probe\n",
        },
        purpose="architecture validation sandbox adapter write probe",
        allowed_tools=["write_file"],
        reason="architecture validation sandbox adapter contract",
        network_enabled=False,
        secret_injection="none",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "sandbox_adapters"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='sandbox_adapter_probe_recorded'
            """
        )
    }
    contract = receipt["contract"]
    if (
        receipt["receipt_type"] != "SANDBOX_ADAPTER_PROBE"
        or receipt["passed"] is not True
        or receipt["network_enabled"] is not False
        or receipt["secret_injection"] != "none"
        or receipt["remote_execution"] is not False
        or receipt["destroy"]["destroy_verified"] is not True
        or receipt["destroy"]["exists_after_destroy"] is not False
        or contract["network_enabled"] is not False
        or contract["secret_injection"] != "none"
        or not contract.get("environment_digest")
        or not contract.get("contract_digest")
        or panel is None
        or panel["status"]["receipt_count"] < 1
        or panel["status"]["network_access"] is not False
        or "sandbox_adapter_probe_recorded" not in event_types
    ):
        return ArchitecturePassResult(
            "P58",
            "BLOCKED",
            [str(receipt)],
            ["sandbox adapter contract validation failed"],
        )
    return ArchitecturePassResult(
        "P58",
        "ADMIT_SHADOW_ONLY",
        [
            str(receipt["probe_id"]),
            str(contract["environment_digest"]),
            str(receipt["destroy"]["tree_digest_before_destroy"]),
            *sorted(event_types),
        ],
        [
            "Sandbox adapter contract records local fixture environment, network and secret denial, path-scoped execution, and destroy verification without remote execution or delegated authority",
        ],
    )


def validate_phase2_offspring_birth_contract(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt = runtime.draft_offspring_birth_contract(
        parent_head="architecture-validation-head",
        mission="bounded read-only inspection offspring candidate",
        budget={"cycles": 1, "tokens": 0, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile", "public_schema", "task_contract"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=[
            "one read-only inspection task completes",
            "budget exhausted",
            "identity boundary violation detected",
        ],
        reason="architecture validation offspring birth contract",
    )
    contract_path = Path(str(receipt["contract_path"]))
    identity_path = Path(str(receipt["identity_path"]))
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "offspring"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='offspring_birth_contract_drafted'
            """
        )
    }
    boundary = receipt["identity_boundary"]
    contract = receipt["contract"]
    if (
        receipt["receipt_type"] != "OFFSPRING_BIRTH_CONTRACT_DRAFTED"
        or receipt["status"] != "BIRTH_CONTRACT_DRAFTED"
        or not contract_path.exists()
        or not identity_path.exists()
        or boundary["canonical_authority"] != "LivingSystem"
        or boundary["child_authority"] != "candidate_only"
        or boundary["parent_write_allowed"] is not False
        or boundary["child_runtime_started"] is not False
        or boundary["birth_executed"] is not False
        or boundary["no_second_living_system"] is not True
        or contract["permission_scope"]["read_only"] is not True
        or contract["permission_scope"]["parent_write_allowed"] is not False
        or contract["permission_scope"]["merge_allowed"] is not False
        or contract["permission_scope"]["deployment_allowed"] is not False
        or contract["permission_scope"]["skill_promotion_allowed"] is not False
        or receipt["merge_allowed"] is not False
        or receipt["deployment_allowed"] is not False
        or receipt["skill_promotion_allowed"] is not False
        or panel is None
        or panel["status"]["receipt_count"] < 1
        or panel["status"]["second_authority_created"] is not False
        or "offspring_birth_contract_drafted" not in event_types
    ):
        return ArchitecturePassResult(
            "P59",
            "BLOCKED",
            [str(receipt), str(panel)],
            ["offspring birth contract identity boundary validation failed"],
        )
    return ArchitecturePassResult(
        "P59",
        "ADMIT_SHADOW_ONLY",
        [
            str(receipt["offspring_id"]),
            str(contract["contract_digest"]),
            str(receipt["receipt_digest"]),
            *sorted(event_types),
        ],
        [
            "Offspring birth contract is materialized only as an isolated candidate with read-only budget, explicit inheritance limits, termination conditions, and no second authority",
        ],
    )


def validate_phase2_offspring_isolated_state_budget(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="architecture-validation-head",
        mission="bounded read-only inspection offspring candidate",
        budget={"cycles": 2, "tokens": 0, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile", "task_contract"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=[
            "read-only inspection complete",
            "budget exhausted",
            "checkpoint cannot be resumed safely",
        ],
        reason="architecture validation offspring birth contract for isolated state",
    )
    receipt = runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation offspring isolated state budget",
    )
    state_path = Path(str(receipt["state_manifest_path"]))
    budget_path = Path(str(receipt["budget_ledger_path"]))
    checkpoint_path = Path(str(receipt["checkpoint_path"]))
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "offspring"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'offspring_birth_contract_drafted',
                'offspring_isolated_state_initialized'
            )
            """
        )
    }
    state = receipt["state_manifest"]
    budget = receipt["budget_ledger"]
    checkpoint = receipt["checkpoint"]
    if (
        receipt["receipt_type"] != "OFFSPRING_ISOLATED_STATE_INITIALIZED"
        or receipt["status"] != "ISOLATED_STATE_READY"
        or not state_path.exists()
        or not budget_path.exists()
        or not checkpoint_path.exists()
        or state["canonical_authority"] != "LivingSystem"
        or state["child_authority"] != "candidate_only"
        or state["runtime_started"] is not False
        or state["parent_db_mount"] is not False
        or state["read_only"] is not True
        or budget["used"]["cycles"] != 0
        or budget["remaining"]["cycles"] != 2
        or budget["remaining"]["writes"] != 0
        or budget["parent_write_allowed"] is not False
        or checkpoint["state"] != "CREATED_NOT_RUNNING"
        or checkpoint["resume_allowed"] is not False
        or receipt["parent_write_allowed"] is not False
        or receipt["second_authority_created"] is not False
        or panel is None
        or panel["status"]["isolated_state"]["receipt_count"] < 1
        or panel["status"]["child_runtime_started"] is not False
        or panel["status"]["candidate_only"] is not True
        or {
            "offspring_birth_contract_drafted",
            "offspring_isolated_state_initialized",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P60",
            "BLOCKED",
            [str(receipt), str(panel)],
            ["offspring isolated state and budget validation failed"],
        )
    return ArchitecturePassResult(
        "P60",
        "ADMIT_SHADOW_ONLY",
        [
            str(receipt["offspring_id"]),
            str(checkpoint["checkpoint_id"]),
            str(receipt["receipt_digest"]),
            *sorted(event_types),
        ],
        [
            "Offspring isolated state initializes a read-only state manifest, budget ledger, and non-resumable checkpoint without starting a child runtime or mounting parent state",
        ],
    )


def validate_phase2_offspring_retirement_tombstone(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="architecture-validation-head",
        mission="bounded read-only inspection offspring candidate",
        budget={"cycles": 1, "tokens": 0, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["read-only inspection complete"],
        reason="architecture validation offspring birth contract for retirement",
    )
    runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation offspring isolated state for retirement",
    )
    receipt = runtime.retire_offspring_candidate(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation offspring retirement tombstone",
        outcome_summary={
            "completed_readonly_tasks": 0,
            "failures": 0,
            "capabilities_proposed": 0,
        },
    )
    tombstone_path = Path(str(receipt["tombstone_path"]))
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "offspring"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'offspring_birth_contract_drafted',
                'offspring_isolated_state_initialized',
                'offspring_candidate_retired'
            )
            """
        )
    }
    retirement = receipt["retirement"]
    if (
        receipt["receipt_type"] != "OFFSPRING_CANDIDATE_RETIRED"
        or receipt["status"] != "RETIRED_CANDIDATE"
        or not tombstone_path.exists()
        or retirement["terminal_state"] != "RETIRED_CANDIDATE"
        or retirement["runtime_started"] is not False
        or retirement["absorption_requested"] is not False
        or retirement["absorption_allowed"] is not False
        or retirement["candidate_state_frozen"] is not True
        or receipt["promotion_allowed"] is not False
        or receipt["merge_allowed"] is not False
        or receipt["deployment_allowed"] is not False
        or receipt["second_authority_created"] is not False
        or panel is None
        or panel["status"]["retirement"]["receipt_count"] < 1
        or panel["status"]["absorption_allowed"] is not False
        or panel["status"]["second_authority_created"] is not False
        or {
            "offspring_birth_contract_drafted",
            "offspring_isolated_state_initialized",
            "offspring_candidate_retired",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P61",
            "BLOCKED",
            [str(receipt), str(panel)],
            ["offspring retirement tombstone validation failed"],
        )
    return ArchitecturePassResult(
        "P61",
        "ADMIT_SHADOW_ONLY",
        [
            str(receipt["offspring_id"]),
            str(retirement["retirement_id"]),
            str(receipt["receipt_digest"]),
            *sorted(event_types),
        ],
        [
            "Offspring candidate retirement freezes isolated candidate state with a tombstone before any future absorption gate, without promotion or second authority",
        ],
    )


def validate_phase2_offspring_budget_no_gain_stop(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="architecture-validation-head",
        mission="bounded read-only inspection offspring candidate",
        budget={
            "time": 2,
            "calls": 2,
            "tokens": 10,
            "cost": 0,
            "disk": 1024,
            "failures": 1,
            "writes": 0,
        },
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["budget exhausted", "no-gain stop"],
        reason="architecture validation offspring birth contract for budget",
    )
    runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation offspring isolated state for budget",
    )
    reserved = runtime.reserve_offspring_budget(
        offspring_id=str(birth["offspring_id"]),
        request={"time": 1, "calls": 1, "tokens": 5, "writes": 0},
        reason="architecture validation offspring budget reservation",
        worker_id="worker-a",
        node_id="node-a",
    )
    blocked = runtime.reserve_offspring_budget(
        offspring_id=str(birth["offspring_id"]),
        request={"time": 2, "calls": 2},
        reason="architecture validation offspring budget block",
        worker_id="worker-b",
        node_id="node-b",
    )
    stop = runtime.review_offspring_no_gain_stop(
        offspring_id=str(birth["offspring_id"]),
        evidence_delta=0,
        improvement_delta=0.0,
        consecutive_no_evidence_rounds=2,
        consecutive_no_improvement_rounds=3,
        reason="architecture validation no-gain stop",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "offspring"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'offspring_budget_reserved',
                'offspring_budget_blocked',
                'offspring_no_gain_stop_reviewed'
            )
            """
        )
    }
    if (
        reserved["status"] != "RESERVED"
        or reserved["remaining_before"]["time"] != 2
        or reserved["remaining_after"]["time"] != 1
        or reserved["provider_call_executed"] is not False
        or reserved["tool_call_executed"] is not False
        or blocked["status"] != "BLOCKED"
        or "time" not in blocked["blocked_dimensions"]
        or blocked["provider_call_executed"] is not False
        or blocked["tool_call_executed"] is not False
        or stop["status"] != "HARD_STOP_RECORDED"
        or stop["hard_stop"] is not True
        or stop["provider_call_executed"] is not False
        or stop["tool_call_executed"] is not False
        or panel is None
        or panel["status"]["budget"]["receipt_count"] < 3
        or panel["status"]["budget"]["provider_call_executed"] is not False
        or panel["status"]["budget"]["tool_call_executed"] is not False
        or {
            "offspring_budget_reserved",
            "offspring_budget_blocked",
            "offspring_no_gain_stop_reviewed",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P62",
            "BLOCKED",
            [str(reserved), str(blocked), str(stop), str(panel)],
            ["offspring budget and no-gain stop validation failed"],
        )
    return ArchitecturePassResult(
        "P62",
        "ADMIT_SHADOW_ONLY",
        [
            str(reserved["budget_id"]),
            str(blocked["budget_id"]),
            str(stop["review_id"]),
            *sorted(event_types),
        ],
        [
            "Offspring budget gate reserves aggregate dimensions, blocks over-grant requests before tools/providers, and records hard no-gain stop evidence",
        ],
    )


def validate_phase2_offspring_checkpoint_fork(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="architecture-validation-head",
        mission="bounded read-only inspection offspring candidate",
        budget={"time": 2, "calls": 2, "tokens": 10, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["checkpoint complete", "budget exhausted"],
        reason="architecture validation offspring birth contract for checkpoint",
    )
    runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation offspring isolated state for checkpoint",
    )
    checkpoint = runtime.record_offspring_checkpoint(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation checkpoint",
        artifact_manifest={"artifacts": []},
    )
    verified = runtime.verify_offspring_checkpoint(
        offspring_id=str(birth["offspring_id"]),
        checkpoint_id=str(checkpoint["checkpoint_id"]),
        reason="architecture validation checkpoint verify",
    )
    budget_path = Path(str(runtime.offspring.state_receipts(1)[0]["budget_ledger_path"]))
    budget_path.write_text('{"tampered": true}\n', encoding="utf-8")
    tampered = runtime.verify_offspring_checkpoint(
        offspring_id=str(birth["offspring_id"]),
        checkpoint_id=str(checkpoint["checkpoint_id"]),
        reason="architecture validation checkpoint tamper detect",
    )
    fork_a = runtime.fork_offspring_candidate(
        parent_offspring_id=str(birth["offspring_id"]),
        parent_checkpoint_id=str(checkpoint["checkpoint_id"]),
        mutation_reason="variant A",
        reason="architecture validation fork A",
    )
    fork_b = runtime.fork_offspring_candidate(
        parent_offspring_id=str(birth["offspring_id"]),
        parent_checkpoint_id=str(checkpoint["checkpoint_id"]),
        mutation_reason="variant B",
        reason="architecture validation fork B",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "offspring"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'offspring_checkpoint_recorded',
                'offspring_checkpoint_verified',
                'offspring_fork_drafted'
            )
            """
        )
    }
    if (
        checkpoint["status"] != "CHECKPOINT_RECORDED"
        or checkpoint["checkpoint"]["lease_replay_allowed"] is not False
        or verified["status"] != "VERIFIED"
        or verified["mismatches"]
        or tampered["status"] != "TAMPERED"
        or "budget_ledger.json" not in tampered["mismatches"]
        or fork_a["status"] != "FORK_DRAFTED"
        or fork_b["status"] != "FORK_DRAFTED"
        or fork_a["child_offspring_id"] == fork_b["child_offspring_id"]
        or fork_a["child_budget_ledger_path"] == fork_b["child_budget_ledger_path"]
        or fork_a["lineage_edge"]["from"] != birth["offspring_id"]
        or fork_a["runtime_started"] is not False
        or fork_b["second_authority_created"] is not False
        or panel is None
        or panel["status"]["checkpoint"]["receipt_count"] < 5
        or panel["status"]["checkpoint"]["lease_replay_allowed"] is not False
        or {
            "offspring_checkpoint_recorded",
            "offspring_checkpoint_verified",
            "offspring_fork_drafted",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P63",
            "BLOCKED",
            [str(checkpoint), str(verified), str(tampered), str(fork_a), str(fork_b)],
            ["offspring checkpoint/fork validation failed"],
        )
    return ArchitecturePassResult(
        "P63",
        "ADMIT_SHADOW_ONLY",
        [
            str(checkpoint["checkpoint_id"]),
            str(fork_a["fork_id"]),
            str(fork_b["fork_id"]),
            *sorted(event_types),
        ],
        [
            "Offspring checkpoint manifests detect tamper and fork two independent candidate states with lineage edges without replaying leases or starting child runtimes",
        ],
    )


def validate_phase2_offspring_mailbox_envelope(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="architecture-validation-head",
        mission="bounded read-only offspring mailbox candidate",
        budget={"time": 2, "calls": 2, "tokens": 10, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["mailbox candidate imported", "budget exhausted"],
        reason="architecture validation offspring birth contract for mailbox",
    )
    state = runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation offspring isolated state for mailbox",
    )
    artifact_path = Path(str(state["state_root"])) / "artifacts" / "summary.txt"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("offspring mailbox candidate artifact\n", encoding="utf-8")
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
        task_id="candidate-task-1",
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
        reason="architecture validation offspring mailbox draft",
    )
    accepted = runtime.receive_offspring_mailbox_envelope(
        envelope=draft["envelope"],
        reason="architecture validation offspring mailbox import",
    )
    unknown = dict(draft["envelope"])
    unknown["schema_version"] = "offspring-mailbox-v99"
    quarantined = runtime.receive_offspring_mailbox_envelope(
        envelope=unknown,
        reason="architecture validation unknown schema quarantine",
    )
    damaged = dict(draft["envelope"])
    damaged["parts"] = [{"content_type": "text/plain", "body": "tampered"}]
    damaged_quarantine = runtime.receive_offspring_mailbox_envelope(
        envelope=damaged,
        reason="architecture validation damaged digest quarantine",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "offspring"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'offspring_mailbox_envelope_drafted',
                'offspring_mailbox_envelope_received',
                'offspring_mailbox_envelope_quarantined'
            )
            """
        )
    }
    if (
        draft["status"] != "DRAFTED"
        or accepted["status"] != "CANDIDATE_RECEIVED"
        or accepted["artifact_trace"]["all_artifacts_linked_to_child_evidence"]
        is not True
        or accepted["parent_db_write_allowed"] is not False
        or accepted["goal_state_mutated"] is not False
        or accepted["skill_state_mutated"] is not False
        or accepted["completion_authority_transferred"] is not False
        or quarantined["status"] != "QUARANTINED"
        or "unsupported schema" not in quarantined["quarantine_reason"]
        or damaged_quarantine["status"] != "QUARANTINED"
        or "digest mismatch" not in damaged_quarantine["quarantine_reason"]
        or panel is None
        or panel["status"]["mailbox"]["receipt_count"] < 4
        or panel["status"]["mailbox"]["completion_authority_transferred"] is not False
        or {
            "offspring_mailbox_envelope_drafted",
            "offspring_mailbox_envelope_received",
            "offspring_mailbox_envelope_quarantined",
        }
        - event_types
    ):
        return ArchitecturePassResult(
            "P64",
            "BLOCKED",
            [str(draft), str(accepted), str(quarantined), str(damaged_quarantine)],
            ["offspring mailbox envelope validation failed"],
        )
    return ArchitecturePassResult(
        "P64",
        "ADMIT_SHADOW_ONLY",
        [
            str(draft["envelope_id"]),
            str(accepted["envelope_id"]),
            str(quarantined["quarantine_reason"]),
            str(damaged_quarantine["quarantine_reason"]),
            *sorted(event_types),
        ],
        [
            "Offspring mailbox envelope imports candidate artifacts through a versioned schema, quarantines unknown or damaged envelopes, and keeps parent authority over validation and completion",
        ],
    )


def validate_phase2_offspring_retirement_cleanup(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    birth = runtime.draft_offspring_birth_contract(
        parent_head="architecture-validation-head",
        mission="bounded read-only offspring cleanup candidate",
        budget={"time": 2, "calls": 2, "tokens": 10, "writes": 0},
        inheritance_manifest={
            "allow": ["readonly_profile"],
            "deny": ["secrets", "private_memory", "parent_database_write"],
        },
        termination_conditions=["retired", "cleanup verified"],
        reason="architecture validation offspring birth contract for cleanup",
    )
    state = runtime.initialize_offspring_isolated_state(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation offspring isolated state for cleanup",
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
        reason="architecture validation pre-retirement budget receipt",
    )
    retired = runtime.retire_offspring_candidate(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation retire for cleanup",
        outcome_summary={
            "completed_readonly_tasks": 0,
            "failures": 0,
            "capabilities_proposed": 0,
        },
    )
    cleanup = runtime.verify_offspring_retirement_cleanup(
        offspring_id=str(birth["offspring_id"]),
        reason="architecture validation retirement cleanup",
    )
    budget_blocked = False
    try:
        runtime.reserve_offspring_budget(
            offspring_id=str(birth["offspring_id"]),
            request={"time": 1},
            reason="architecture validation post-retirement budget block",
        )
    except PermissionError:
        budget_blocked = True
    evidence_bundle_path = Path(str(cleanup["evidence_bundle_path"]))
    retention_path = Path(str(cleanup["retention_manifest_path"]))
    gc_path = Path(str(cleanup["gc_verification_path"]))
    evidence_bundle = json.loads(evidence_bundle_path.read_text(encoding="utf-8"))
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "offspring"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'offspring_candidate_retired',
                'offspring_retirement_cleanup_verified'
            )
            """
        )
    }
    if (
        retired["status"] != "RETIRED_CANDIDATE"
        or cleanup["status"] != "CLEANUP_VERIFIED"
        or cleanup["task_assignment_allowed"] is not False
        or cleanup["budget_reservation_allowed"] is not False
        or cleanup["cleanup_verification"]["owner_review_required"] is not False
        or cleanup["cleanup_verification"]["secret_residual"] is not False
        or cleanup["cleanup_verification"]["lease_residual"] is not False
        or cleanup["cleanup_verification"]["mount_residual"] is not False
        or not budget_blocked
        or not evidence_bundle_path.is_file()
        or not retention_path.is_file()
        or not gc_path.is_file()
        or evidence_bundle["lineage"]["offspring_id"] != birth["offspring_id"]
        or not evidence_bundle["budget"]["ledger"]
        or not evidence_bundle["retirement"]
        or panel is None
        or panel["status"]["retirement_cleanup"]["receipt_count"] != 1
        or panel["status"]["retirement_cleanup"]["task_assignment_allowed"] is not False
        or {"offspring_candidate_retired", "offspring_retirement_cleanup_verified"}
        - event_types
    ):
        return ArchitecturePassResult(
            "P65",
            "BLOCKED",
            [str(retired), str(cleanup), str(evidence_bundle)],
            ["offspring retirement cleanup validation failed"],
        )
    return ArchitecturePassResult(
        "P65",
        "ADMIT_SHADOW_ONLY",
        [
            str(cleanup["retention_manifest"]["retention_id"]),
            str(cleanup["evidence_bundle_digest"]),
            *cleanup["cleanup_verification"]["cleanup"]["removed_targets"],
            *sorted(event_types),
        ],
        [
            "Offspring retirement cleanup preserves a verifiable evidence bundle, removes disposable resource scopes, and blocks retired candidates from receiving new budget or task authority",
        ],
    )


def validate_phase2_paired_baseline_candidate_experiment(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
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
        reason="architecture validation paired experiment",
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
        reason="architecture validation invalid paired experiment",
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='paired_candidate_experiment_recorded'
            """
        )
    }
    receipts = runtime.status()["paired_experiment_receipts"]
    if (
        valid["status"] != "CANDIDATE_VALIDATED"
        or valid["candidate_validated"] is not True
        or valid["promotion_executed"] is not False
        or valid["absorption_executed"] is not False
        or valid["report"]["completion"]["candidate_success_rate"]
        <= valid["report"]["completion"]["baseline_success_rate"]
        or valid["report"]["stability_summary"]["repeated_case_count"] != 1
        or valid["report"]["process_quality"]["failure_samples_retained"] is not True
        or invalid["status"] != "INVALID_CONDITIONS"
        or invalid["candidate_validated"] is not False
        or not invalid["report"]["process_quality"]["invalid_reasons"]
        or len(receipts) != 2
        or event_types != {"paired_candidate_experiment_recorded"}
    ):
        return ArchitecturePassResult(
            "P66",
            "BLOCKED",
            [str(valid), str(invalid)],
            ["paired baseline-candidate experiment validation failed"],
        )
    return ArchitecturePassResult(
        "P66",
        "ADMIT_SHADOW_ONLY",
        [
            str(valid["experiment_id"]),
            str(invalid["experiment_id"]),
            str(valid["report"]["preregistration_digest"]),
            "paired_candidate_experiment_recorded",
        ],
        [
            "Paired baseline-candidate experiments lock preregistration, baseline, candidate diff, environment, budget, and evaluator conditions; invalid condition drift blocks validation without promotion or absorption",
        ],
    )


def validate_phase2_holdout_epoch_immutability(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
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
        reason="architecture validation freeze holdout epoch",
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
        reason="architecture validation same epoch run",
    )
    tampered_thresholds = {"min_pass_rate": 0.5}
    invalid = runtime.run_holdout_epoch(
        epoch_id=str(frozen["epoch_id"]),
        evaluator=evaluator,
        holdout_manifest=holdout,
        thresholds=tampered_thresholds,
        candidate_results=[
            {"case_id": "h1", "passed": True},
            {"case_id": "h2", "passed": False},
        ],
        reason="architecture validation threshold tamper",
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'holdout_epoch_frozen',
                'holdout_epoch_run_recorded'
            )
            """
        )
    }
    receipts = runtime.status()["holdout_epoch_receipts"]
    if (
        frozen["status"] != "EPOCH_FROZEN"
        or frozen["holdout_write_allowed"] is not False
        or frozen["threshold_mutation_allowed"] is not False
        or passed["status"] != "HOLDOUT_PASSED"
        or passed["requires_rebaseline"] is not False
        or passed["holdout_write_allowed"] is not False
        or invalid["status"] != "INVALID_EPOCH"
        or invalid["requires_rebaseline"] is not True
        or "threshold_digest" not in invalid["mismatches"]
        or invalid["promotion_executed"] is not False
        or len(receipts) != 3
        or event_types != {"holdout_epoch_frozen", "holdout_epoch_run_recorded"}
    ):
        return ArchitecturePassResult(
            "P67",
            "BLOCKED",
            [str(frozen), str(passed), str(invalid)],
            ["holdout epoch immutability validation failed"],
        )
    return ArchitecturePassResult(
        "P67",
        "ADMIT_SHADOW_ONLY",
        [
            str(frozen["epoch_id"]),
            str(frozen["epoch"]["epoch_digest"]),
            "threshold_digest_mismatch_detected",
            *sorted(event_types),
        ],
        [
            "Holdout epoch manifests freeze evaluator, holdout, and thresholds; same-epoch reruns are reproducible while threshold drift invalidates the run and requires rebaseline",
        ],
    )


def validate_phase2_promotion_bundle_gate(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
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
        reason="architecture validation promotion bundle draft",
    )
    unapproved_blocked = False
    try:
        runtime.prepare_promotion_canary(
            bundle_id=str(bundle["bundle_id"]),
            scope=["capability.alpha"],
            reason="architecture validation unapproved canary",
        )
    except PermissionError:
        unapproved_blocked = True
    approval = runtime.bind_owner_promotion_approval(
        bundle_id=str(bundle["bundle_id"]),
        bundle_digest=str(bundle["bundle_digest"]),
        actor="Owner",
        scope=["capability.alpha"],
        reason="architecture validation owner approval binding",
    )
    over_scope_blocked = False
    try:
        runtime.prepare_promotion_canary(
            bundle_id=str(bundle["bundle_id"]),
            scope=["capability.alpha", "capability.beta"],
            reason="architecture validation oversized canary scope",
        )
    except PermissionError:
        over_scope_blocked = True
    canary = runtime.prepare_promotion_canary(
        bundle_id=str(bundle["bundle_id"]),
        scope=["capability.alpha"],
        reason="architecture validation approved canary",
    )
    rollback = runtime.verify_promotion_rollback(
        bundle_id=str(bundle["bundle_id"]),
        reason="architecture validation rollback drill",
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type IN (
                'promotion_bundle_drafted',
                'promotion_owner_approval_bound',
                'promotion_canary_prepared',
                'promotion_rollback_verified'
            )
            """
        )
    }
    receipts = runtime.status()["promotion_bundle_receipts"]
    if (
        bundle["status"] != "BUNDLE_DRAFTED"
        or bundle["canonical_state_mutated"] is not False
        or not unapproved_blocked
        or approval["status"] != "OWNER_APPROVAL_BOUND"
        or approval["scope"] != ["capability.alpha"]
        or not over_scope_blocked
        or canary["status"] != "CANARY_PREPARED"
        or canary["promotion_executed"] is not False
        or rollback["status"] != "ROLLBACK_VERIFIED"
        or rollback["missing_assets"]
        or len(receipts) != 4
        or event_types
        != {
            "promotion_bundle_drafted",
            "promotion_owner_approval_bound",
            "promotion_canary_prepared",
            "promotion_rollback_verified",
        }
    ):
        return ArchitecturePassResult(
            "P68",
            "BLOCKED",
            [str(bundle), str(approval), str(canary), str(rollback)],
            ["promotion bundle gate validation failed"],
        )
    return ArchitecturePassResult(
        "P68",
        "ADMIT_SHADOW_ONLY",
        [
            str(bundle["bundle_id"]),
            str(bundle["bundle_digest"]),
            str(approval["receipt_digest"]),
            str(rollback["receipt_digest"]),
            *sorted(event_types),
        ],
        [
            "Promotion bundles bind owner approval to bundle digest and exact scope, require rollback assets, and only prepare canaries without mutating canonical state or executing promotion",
        ],
    )


def validate_phase2_transfer_efficiency_audit(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
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
        reason="architecture validation transfer partial canary",
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
        reason="architecture validation efficiency reject",
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='transfer_efficiency_audit_recorded'
            """
        )
    }
    receipts = runtime.status()["transfer_audit_receipts"]
    if (
        partial["status"] != "PARTIAL_CANARY_ONLY"
        or partial["hidden_best_only_result_detected"] is not True
        or partial["owner_exception_required"] is not True
        or partial["zero_key_regressions"] is not True
        or len(partial["transfer_failures"]) != 1
        or partial["promotion_executed"] is not False
        or efficiency_reject["status"] != "REJECT_EFFICIENCY"
        or not efficiency_reject["efficiency_failures"]
        or efficiency_reject["owner_exception_required"] is not True
        or len(receipts) != 2
        or event_types != {"transfer_efficiency_audit_recorded"}
    ):
        return ArchitecturePassResult(
            "P69",
            "BLOCKED",
            [str(partial), str(efficiency_reject)],
            ["transfer/regression/efficiency audit validation failed"],
        )
    return ArchitecturePassResult(
        "P69",
        "ADMIT_SHADOW_ONLY",
        [
            str(partial["audit_id"]),
            str(efficiency_reject["audit_id"]),
            "partial_canary_only",
            "efficiency_reject",
            "transfer_efficiency_audit_recorded",
        ],
        [
            "Transfer audits preserve cross-domain failures, zero-regression checks, and success-per-cost thresholds so best-only or inefficient candidates cannot silently advance",
        ],
    )


def validate_phase2_final_delivery_audit(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    console_trace = [
        {
            "result_id": "task-graph-result",
            "input": "Owner task event",
            "tool": "AgenticHarness",
            "receipt": "agentic_task_node_completed",
            "approval": "not_required_read_only",
            "artifact": "graph-result.json",
            "coverage": "TESTED",
        },
        {
            "result_id": "offspring-result",
            "input": "Offspring mailbox envelope",
            "tool": "OffspringRegistry",
            "receipt": "offspring_mailbox_envelope_received",
            "approval": "candidate_only",
            "artifact": "offspring-envelope.json",
            "coverage": "TESTED",
        },
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
    claim_ledger = [
        {
            "claim_id": "repo-ui-runtime",
            "claim": "Owner Console repository runtime is coded and tested",
            "evidence_level": "TESTED",
            "claim_level": "TESTED",
        },
        {
            "claim_id": "live-deployment",
            "claim": "No live deployment is claimed before R40/external evidence",
            "evidence_level": "CODED",
            "claim_level": "CODED",
        },
    ]
    passed = runtime.record_final_delivery_audit(
        console_trace=console_trace,
        installer_recovery=installer_recovery,
        claim_ledger=claim_ledger,
        reason="architecture validation final delivery audit",
    )
    bad_claim = runtime.record_final_delivery_audit(
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
        reason="architecture validation claim ceiling block",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "capability_epoch"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='final_delivery_audit_recorded'
            """
        )
    }
    if (
        passed["status"] != "DELIVERY_AUDIT_PASSED"
        or passed["console_convergence"]["trace_failures"]
        or passed["console_convergence"]["ui_unknown"] is not False
        or passed["installer_recovery"]["reversible"] is not True
        or passed["claim_ledger"]["claim_failures"]
        or passed["live_install_modified"] is not False
        or bad_claim["status"] != "DELIVERY_AUDIT_BLOCKED"
        or not bad_claim["claim_ledger"]["claim_failures"]
        or panel is None
        or panel["status"]["final_delivery_audit"]["receipt_count"] != 2
        or panel["status"]["final_delivery_audit"]["live_install_modified"] is not False
        or event_types != {"final_delivery_audit_recorded"}
    ):
        return ArchitecturePassResult(
            "P70",
            "BLOCKED",
            [str(passed), str(bad_claim)],
            ["final delivery audit validation failed"],
        )
    return ArchitecturePassResult(
        "P70",
        "ADMIT_SHADOW_ONLY",
        [
            str(passed["audit_id"]),
            str(bad_claim["audit_id"]),
            "console_traceability_passed",
            "installer_recovery_reversible",
            "claim_ceiling_blocked",
            "final_delivery_audit_recorded",
        ],
        [
            "Final delivery audit records Owner Console traceability, reversible installer/recovery phases, and claim-ledger ceilings without modifying live installation or claiming external readiness",
        ],
    )


def validate_phase2_delivery_readiness_audit(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
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
        "Delete or archive the disposable campaign home D:\\WLS\\campaigns\\life-campaign-30.",
        "Use git switch main or git revert on the candidate branch; do not merge automatically.",
        "Leave the live installation, live config, and live database unchanged.",
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
        reason="architecture validation delivery readiness audit",
    )
    blocked = runtime.record_delivery_readiness_audit(
        branch="main",
        commit="",
        owner_commands={"R01_R05": "missing execute flag"},
        campaign_assets={**campaign_assets, "powershell_entry": False},
        rollback_steps=["No rollback"],
        boundaries={**boundaries, "deploy_executed": True},
        reason="architecture validation delivery readiness block",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "capability_epoch"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='delivery_readiness_audit_recorded'
            """
        )
    }
    if (
        passed["status"] != "DELIVERY_READY_CANDIDATE"
        or passed["failure_groups"]["branch_failures"]
        or passed["owner_commands"]["failures"]
        or passed["campaign_assets"]["missing"]
        or passed["rollback"]["failures"]
        or passed["boundaries"]["failures"]
        or blocked["status"] != "DELIVERY_READY_BLOCKED"
        or not blocked["failure_groups"]["branch_failures"]
        or not blocked["owner_commands"]["failures"]
        or panel is None
        or panel["status"]["delivery_readiness"]["receipt_count"] != 2
        or panel["status"]["delivery_readiness"]["live_install_modified"] is not False
        or event_types != {"delivery_readiness_audit_recorded"}
    ):
        return ArchitecturePassResult(
            "P74",
            "BLOCKED",
            [str(passed), str(blocked)],
            ["delivery readiness audit validation failed"],
        )
    return ArchitecturePassResult(
        "P74",
        "ADMIT_SHADOW_ONLY",
        [
            str(passed["audit_id"]),
            str(blocked["audit_id"]),
            "candidate_branch_bound",
            "owner_commands_bound",
            "rollback_path_bound",
            "live_boundaries_preserved",
            "delivery_readiness_audit_recorded",
        ],
        [
            "Delivery readiness audits bind candidate branch, exact Owner commands, required campaign assets, rollback steps, and no-live-mutation boundaries without merging, deployment, or Skill promotion",
        ],
    )


def validate_phase2_packaging_layout_audit(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
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
        "duplicate_source_metadata_absent": True,
        "legacy_installer_not_at_root": True,
        "no_direct_main_push_workflow": True,
        "no_source_project_install": True,
        "no_source_project_build": True,
        "no_source_working_directory": True,
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
        reason="architecture validation packaging layout audit",
    )
    blocked_report = {
        **report,
        "success": False,
        "canonical_package": "source/wls",
        "project_manifests": ["pyproject.toml", "source/pyproject.toml"],
        "package_roots": ["source/src/wls", "legacy/wls"],
        "checks": {**checks, "single_package_root": False},
    }
    blocked = runtime.record_packaging_layout_audit(
        report=blocked_report,
        reason="architecture validation packaging layout block",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "capability_epoch"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='packaging_layout_audit_recorded'
            """
        )
    }
    if (
        passed["status"] != "PACKAGING_LAYOUT_PASSED"
        or passed["failure_groups"]["failed_checks"]
        or passed["single_authority_preserved"] is not True
        or passed["install_executed"] is not False
        or blocked["status"] != "PACKAGING_LAYOUT_BLOCKED"
        or not blocked["failure_groups"]["failed_checks"]
        or not blocked["failure_groups"]["package_root_failures"]
        or panel is None
        or panel["status"]["packaging_layout"]["receipt_count"] != 2
        or panel["status"]["packaging_layout"]["install_executed"] is not False
        or event_types != {"packaging_layout_audit_recorded"}
    ):
        return ArchitecturePassResult(
            "P75",
            "BLOCKED",
            [str(passed), str(blocked)],
            ["packaging layout audit validation failed"],
        )
    return ArchitecturePassResult(
        "P75",
        "ADMIT_SHADOW_ONLY",
        [
            str(passed["audit_id"]),
            str(blocked["audit_id"]),
            "single_package_root_bound",
            "single_project_manifest_bound",
            "version_authority_bound",
            "packaging_layout_audit_recorded",
        ],
        [
            "Packaging layout audits bind repository source authority to one project manifest, one package root, and one version file without building, installing, deploying, or mutating the live instance",
        ],
    )


def validate_phase2_installed_tail_check_audit(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
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
    required_checks = list(checks)
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
        "required_checks": required_checks,
    }
    passed = runtime.record_installed_tail_check_audit(
        report=report,
        reason="architecture validation installed tail check audit",
    )
    blocked_report = {
        **report,
        "status": "FAIL",
        "checks": {**checks, "live_db_unchanged": False},
        "live_hashes_after": {
            "live_config_sha256": "config-hash",
            "live_db_sha256": "changed-db-hash",
        },
        "status_smoke": {"executed": True, "ok": False},
    }
    blocked = runtime.record_installed_tail_check_audit(
        report=blocked_report,
        reason="architecture validation installed tail check block",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "capability_epoch"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='installed_tail_check_audit_recorded'
            """
        )
    }
    if (
        passed["status"] != "INSTALLED_TAIL_CHECK_PASSED"
        or passed["failure_groups"]["failed_required_checks"]
        or passed["live_config_modified"] is not False
        or passed["live_database_modified"] is not False
        or blocked["status"] != "INSTALLED_TAIL_CHECK_BLOCKED"
        or not blocked["failure_groups"]["failed_required_checks"]
        or not blocked["failure_groups"]["live_hash_failures"]
        or panel is None
        or panel["status"]["installed_tail_check"]["receipt_count"] != 2
        or panel["status"]["installed_tail_check"]["deploy_executed"] is not False
        or event_types != {"installed_tail_check_audit_recorded"}
    ):
        return ArchitecturePassResult(
            "P76",
            "BLOCKED",
            [str(passed), str(blocked)],
            ["installed tail check audit validation failed"],
        )
    return ArchitecturePassResult(
        "P76",
        "ADMIT_SHADOW_ONLY",
        [
            str(passed["audit_id"]),
            str(blocked["audit_id"]),
            "installed_tail_check_bound",
            "live_hash_preservation_bound",
            "status_smoke_limit_bound",
            "installed_tail_check_audit_recorded",
        ],
        [
            "Installed tail-check audits preserve disposable compatibility and live hash evidence from reports while blocking mutation, smoke failure, deployment, merge, and Skill promotion claims",
        ],
    )


def validate_phase2_operational_preflight_audit(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    runtime.run_cycle()
    status_snapshot = runtime.status()
    integrity_report = {
        "ok": True,
        "ledger": "verified in disposable architecture validation",
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
        reason="architecture validation operational preflight audit",
    )
    blocked_status = {
        **status_snapshot,
        "paused": True,
        "pending_actions": [
            {
                "action_id": "waiting-action",
                "status": "WAITING_APPROVAL",
                "risk": "REVERSIBLE_WRITE",
            }
        ],
    }
    blocked = runtime.record_operational_preflight_audit(
        status_snapshot=blocked_status,
        integrity_report={**integrity_report, "ok": False},
        lease_probe={**lease_probe, "daemon_lock_available": False},
        reason="architecture validation operational preflight block",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "life"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='operational_preflight_audit_recorded'
            """
        )
    }
    if (
        passed["status"] != "OPERATIONAL_PREFLIGHT_PASSED"
        or passed["failure_groups"]["runtime_failures"]
        or passed["daemon_started"] is not False
        or blocked["status"] != "OPERATIONAL_PREFLIGHT_BLOCKED"
        or not blocked["failure_groups"]["runtime_failures"]
        or not blocked["failure_groups"]["integrity_failures"]
        or not blocked["failure_groups"]["lease_failures"]
        or panel is None
        or panel["status"]["operational_preflight"]["receipt_count"] != 2
        or panel["status"]["operational_preflight"]["daemon_started"] is not False
        or event_types != {"operational_preflight_audit_recorded"}
    ):
        return ArchitecturePassResult(
            "P77",
            "BLOCKED",
            [str(passed), str(blocked)],
            ["operational preflight audit validation failed"],
        )
    return ArchitecturePassResult(
        "P77",
        "ADMIT_SHADOW_ONLY",
        [
            str(passed["audit_id"]),
            str(blocked["audit_id"]),
            "runtime_status_bound",
            "integrity_report_bound",
            "lease_probe_bound",
            "operational_preflight_audit_recorded",
        ],
        [
            "Operational preflight audits bind runtime status, completed-cycle evidence, integrity status, and lease probes without starting daemons, mutating live state, or claiming long-run uptime",
        ],
    )


def validate_phase2_owner_console_readiness_view(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
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
    runtime.record_operational_preflight_audit(
        status_snapshot=status_snapshot,
        integrity_report=integrity_report,
        lease_probe=lease_probe,
        reason="architecture validation readiness view preflight",
    )
    runtime.record_installed_tail_check_audit(
        report={
            "receipt_type": "SINGLE_SOFTWARE_TAIL_CHECK",
            "status": "PASS_WITH_LIMITS",
            "install_root": "D:\\WLS\\wls-0.9.0.dev1-py313",
            "live_home": "D:\\WLS\\wls-0.9.0.dev1-py313\\home",
            "campaign_home": "D:\\WLS\\campaigns\\life-campaign-30",
            "checks": {
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
            },
            "live_hashes_before": {
                "live_config_sha256": "config-hash",
                "live_db_sha256": "db-hash",
            },
            "live_hashes_after": {
                "live_config_sha256": "config-hash",
                "live_db_sha256": "db-hash",
            },
            "status_smoke": {"executed": False, "ok": None},
            "required_checks": [
                "install_root_exists",
                "live_home_exists",
                "campaign_home_exists",
                "campaign_not_live_home",
                "campaign_not_runtime_live_home",
                "campaign_not_inside_live_home",
                "campaign_not_inside_runtime_live_home",
                "campaign_config_exists",
                "campaign_config_points_to_campaign_home",
                "live_config_present",
                "live_db_present",
                "live_config_unchanged",
                "live_db_unchanged",
            ],
        },
        reason="architecture validation readiness view tail check",
    )
    runtime.record_packaging_layout_audit(
        report={
            "success": True,
            "canonical_project_root": ".",
            "canonical_package": "source/src/wls",
            "canonical_version_file": "source/src/wls/_version.py",
            "project_manifests": ["pyproject.toml"],
            "package_roots": ["source/src/wls"],
            "checks": {"single_project_manifest": True, "single_package_root": True},
        },
        reason="architecture validation readiness view packaging",
    )
    runtime.record_delivery_readiness_audit(
        branch="living-agent-os-capabilities-001",
        commit="abcdef1234567890",
        owner_commands={
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
        },
        campaign_assets={
            "campaign_spec": True,
            "python_runner": True,
            "powershell_entry": True,
            "campaign_tests": True,
            "campaign_architecture_doc": True,
        },
        rollback_steps=[
            "Delete disposable campaign home D:\\WLS\\campaigns\\life-campaign-30.",
            "Use git revert on the candidate branch if rejected.",
            "Keep live installation, live config, and live database unchanged.",
        ],
        boundaries={
            "live_install_modified": False,
            "live_config_modified": False,
            "live_database_modified": False,
            "merge_executed": False,
            "deploy_executed": False,
            "skill_promoted": False,
        },
        reason="architecture validation readiness view delivery",
    )
    product = OwnerConsoleProductProjection().project(runtime.status())
    app_js = (Path(__file__).parent / "ui_static" / "app.js").read_text(
        encoding="utf-8"
    )
    readiness = product["delivery_readiness"]
    if (
        readiness["overall_status"] != "CANDIDATE_READY"
        or readiness["missing_or_blocked"]
        or [item["pass_id"] for item in readiness["items"]]
        != ["P77", "P76", "P75", "P74"]
        or product["writes_canonical_state"] is not False
        or product["direct_tool_execution"] is not False
        or "Delivery Readiness" not in app_js
        or "readinessCards" not in app_js
    ):
        return ArchitecturePassResult(
            "P78",
            "BLOCKED",
            [str(readiness)],
            ["owner console readiness view validation failed"],
        )
    return ArchitecturePassResult(
        "P78",
        "ADMIT_SHADOW_ONLY",
        [
            readiness["overall_status"],
            "owner_console_delivery_readiness_projected",
            "static_ui_delivery_readiness_rendered",
            "read_only_projection_preserved",
        ],
        [
            "Owner Console exposes a read-only delivery readiness summary over P74-P77 receipts and renders it in the product panel without direct tool execution or canonical writes",
        ],
    )


def validate_phase2_delivery_handoff_package(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    readiness_summary = {
        "overall_status": "CANDIDATE_READY",
        "missing_or_blocked": [],
        "items": [
            {"pass_id": "P77", "status": "OPERATIONAL_PREFLIGHT_PASSED"},
            {"pass_id": "P76", "status": "INSTALLED_TAIL_CHECK_PASSED"},
            {"pass_id": "P75", "status": "PACKAGING_LAYOUT_PASSED"},
            {"pass_id": "P74", "status": "DELIVERY_READY_CANDIDATE"},
        ],
    }
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
    boundaries = {
        "live_install_modified": False,
        "live_config_modified": False,
        "live_database_modified": False,
        "merge_executed": False,
        "deploy_executed": False,
        "skill_promoted": False,
    }
    passed = runtime.record_delivery_handoff_package(
        readiness_summary=readiness_summary,
        candidate={"branch": "living-agent-os-capabilities-001", "commit": "abcdef"},
        test_results=[
            {"name": "architecture_validation_p74_p78", "status": "PASS"},
            {"name": "ui_projection_and_server", "status": "PASS"},
        ],
        owner_commands=owner_commands,
        rollback_steps=[
            "Delete disposable campaign home D:\\WLS\\campaigns\\life-campaign-30.",
            "Use git revert on the candidate branch if rejected.",
            "Keep live installation, live config, and live database unchanged.",
        ],
        boundaries=boundaries,
        reason="architecture validation delivery handoff package",
    )
    blocked = runtime.record_delivery_handoff_package(
        readiness_summary={"overall_status": "NEEDS_EVIDENCE", "missing_or_blocked": ["P76"]},
        candidate={"branch": "main", "commit": ""},
        test_results=[{"name": "ui_projection_and_server", "status": "FAIL"}],
        owner_commands={"R01_R05": "missing execute"},
        rollback_steps=["No rollback"],
        boundaries={**boundaries, "deploy_executed": True},
        reason="architecture validation delivery handoff block",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "capability_epoch"
        ),
        None,
    )
    script = (Path(__file__).parents[2] / "scripts" / "build_delivery_handoff.py")
    script_text = script.read_text(encoding="utf-8")
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='delivery_handoff_package_recorded'
            """
        )
    }
    if (
        passed["status"] != "DELIVERY_HANDOFF_READY"
        or passed["failure_groups"]["candidate_failures"]
        or passed["failure_groups"]["test_failures"]
        or blocked["status"] != "DELIVERY_HANDOFF_BLOCKED"
        or not blocked["failure_groups"]["readiness_failures"]
        or not blocked["failure_groups"]["boundary_failures"]
        or panel is None
        or panel["status"]["delivery_handoff"]["receipt_count"] != 2
        or "run_life_campaign_30.ps1" not in script_text
        or "WLS_DELIVERY_HANDOFF_PACKAGE" not in script_text
        or event_types != {"delivery_handoff_package_recorded"}
    ):
        return ArchitecturePassResult(
            "P79",
            "BLOCKED",
            [str(passed), str(blocked)],
            ["delivery handoff package validation failed"],
        )
    return ArchitecturePassResult(
        "P79",
        "ADMIT_SHADOW_ONLY",
        [
            str(passed["handoff_id"]),
            str(blocked["handoff_id"]),
            "owner_commands_exportable",
            "rollback_steps_bound",
            "delivery_handoff_package_recorded",
        ],
        [
            "Delivery handoff packages bind readiness, candidate branch, tests, Owner commands, rollback, and no-live-mutation boundaries without executing campaigns, merging, deploying, or promoting Skills",
        ],
    )


def validate_phase2_release_state_audit(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    receipt_counts = {
        "final_delivery_audit": 1,
        "delivery_readiness": 1,
        "packaging_layout": 1,
        "installed_tail_check": 1,
        "operational_preflight": 1,
        "delivery_handoff": 1,
    }
    asset_checks = {
        "campaign_spec": True,
        "campaign_runner": True,
        "powershell_entry": True,
        "owner_console_static": True,
        "delivery_handoff_script": True,
        "architecture_doc": True,
    }
    test_results = [
        {"name": "architecture_validation_p74_p79", "status": "PASS"},
        {"name": "ui_projection_and_server", "status": "PASS"},
        {"name": "campaign_packaging_contracts", "status": "PASS"},
    ]
    boundaries = {
        "live_install_modified": False,
        "live_config_modified": False,
        "live_database_modified": False,
        "merge_executed": False,
        "deploy_executed": False,
        "skill_promoted": False,
        "persistent_daemon_started": False,
    }
    passed = runtime.record_release_state_audit(
        receipt_counts=receipt_counts,
        asset_checks=asset_checks,
        test_results=test_results,
        boundaries=boundaries,
        reason="architecture validation release state audit",
    )
    blocked = runtime.record_release_state_audit(
        receipt_counts={**receipt_counts, "installed_tail_check": 0},
        asset_checks={**asset_checks, "owner_console_static": False},
        test_results=[{"name": "campaign_packaging_contracts", "status": "FAIL"}],
        boundaries={**boundaries, "deploy_executed": True},
        reason="architecture validation release state block",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "capability_epoch"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='release_state_audit_recorded'
            """
        )
    }
    if (
        passed["status"] != "RELEASE_STATE_CANDIDATE_READY"
        or passed["failure_groups"]["receipt_failures"]
        or passed["failure_groups"]["asset_failures"]
        or blocked["status"] != "RELEASE_STATE_BLOCKED"
        or blocked["failure_groups"]["receipt_failures"] != ["installed_tail_check"]
        or blocked["failure_groups"]["asset_failures"] != ["owner_console_static"]
        or blocked["failure_groups"]["test_failures"] != ["campaign_packaging_contracts"]
        or blocked["failure_groups"]["boundary_failures"] != ["deploy_executed"]
        or panel is None
        or panel["status"]["release_state_audit"]["receipt_count"] != 2
        or panel["status"]["release_state_audit"]["deploy_executed"] is not False
        or event_types != {"release_state_audit_recorded"}
    ):
        return ArchitecturePassResult(
            "P80",
            "BLOCKED",
            [str(passed), str(blocked)],
            ["release state audit validation failed"],
        )
    return ArchitecturePassResult(
        "P80",
        "ADMIT_SHADOW_ONLY",
        [
            str(passed["audit_id"]),
            str(blocked["audit_id"]),
            "release_state_candidate_ready",
            "missing_evidence_blocks_release",
            "release_state_audit_recorded",
        ],
        [
            "Release state audits summarize current repository readiness evidence, assets, tests, and no-live-mutation boundaries while blocking incomplete handoff states",
        ],
    )


def validate_phase2_release_handoff_summary(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    readiness_summary = {
        "overall_status": "CANDIDATE_READY",
        "missing_or_blocked": [],
        "items": [
            {"pass_id": "P77", "status": "OPERATIONAL_PREFLIGHT_PASSED"},
            {"pass_id": "P76", "status": "INSTALLED_TAIL_CHECK_PASSED"},
            {"pass_id": "P75", "status": "PACKAGING_LAYOUT_PASSED"},
            {"pass_id": "P74", "status": "DELIVERY_READY_CANDIDATE"},
        ],
    }
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
    boundaries = {
        "live_install_modified": False,
        "live_config_modified": False,
        "live_database_modified": False,
        "merge_executed": False,
        "deploy_executed": False,
        "skill_promoted": False,
        "persistent_daemon_started": False,
    }
    runtime.record_delivery_handoff_package(
        readiness_summary=readiness_summary,
        candidate={"branch": "living-agent-os-capabilities-001", "commit": "abcdef"},
        test_results=[
            {"name": "architecture_validation_p74_p80", "status": "PASS"},
            {"name": "ui_projection_and_server", "status": "PASS"},
        ],
        owner_commands=owner_commands,
        rollback_steps=[
            "Delete disposable campaign home D:\\WLS\\campaigns\\life-campaign-30.",
            "Use git revert on the candidate branch if rejected.",
            "Keep live installation, live config, and live database unchanged.",
        ],
        boundaries={
            key: value
            for key, value in boundaries.items()
            if key != "persistent_daemon_started"
        },
        reason="architecture validation release handoff summary handoff",
    )
    runtime.record_release_state_audit(
        receipt_counts={
            "final_delivery_audit": 1,
            "delivery_readiness": 1,
            "packaging_layout": 1,
            "installed_tail_check": 1,
            "operational_preflight": 1,
            "delivery_handoff": 1,
        },
        asset_checks={
            "campaign_spec": True,
            "campaign_runner": True,
            "powershell_entry": True,
            "owner_console_static": True,
            "delivery_handoff_script": True,
            "architecture_doc": True,
        },
        test_results=[
            {"name": "architecture_validation_p74_p80", "status": "PASS"},
            {"name": "ui_projection_and_server", "status": "PASS"},
            {"name": "campaign_packaging_contracts", "status": "PASS"},
        ],
        boundaries=boundaries,
        reason="architecture validation release handoff summary release state",
    )
    product = OwnerConsoleProductProjection().project(runtime.status())
    release_handoff = product["release_handoff"]
    app_js = (Path(__file__).parent / "ui_static" / "app.js").read_text(
        encoding="utf-8"
    )
    handoff_script = (
        Path(__file__).parents[2] / "scripts" / "build_delivery_handoff.py"
    ).read_text(encoding="utf-8")
    if (
        release_handoff["overall_status"] != "CANDIDATE_READY"
        or release_handoff["missing_or_blocked"]
        or [item["pass_id"] for item in release_handoff["items"]] != ["P79", "P80"]
        or product["writes_canonical_state"] is not False
        or product["direct_tool_execution"] is not False
        or "Release Handoff" not in app_js
        or "releaseHandoffCards" not in app_js
        or "release_state_summary" not in handoff_script
        or "release_state_audit" not in handoff_script
    ):
        return ArchitecturePassResult(
            "P81",
            "BLOCKED",
            [str(release_handoff)],
            ["release handoff summary validation failed"],
        )
    return ArchitecturePassResult(
        "P81",
        "ADMIT_SHADOW_ONLY",
        [
            release_handoff["overall_status"],
            "owner_console_release_handoff_projected",
            "static_ui_release_handoff_rendered",
            "handoff_export_release_state_summary",
        ],
        [
            "Owner Console and delivery handoff exports expose P79/P80 release-state readiness as read-only candidate evidence without executing campaigns or mutating live state",
        ],
    )


def validate_phase2_owner_goal_metadata_persistence(
    home: Path,
) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    projection = UIProjection(runtime)
    project_id = projection.create_goal(
        {
            "kind": "project",
            "title": "Owner metadata project",
            "rationale": "validate owner console metadata",
            "risk": "READ",
        }
    )["goal_id"]
    task_id = projection.create_goal(
        {
            "kind": "task",
            "project_id": project_id,
            "title": "Owner metadata task",
            "rationale": "validate task metadata",
            "risk": "READ",
        }
    )["goal_id"]
    project = projection.project_detail(project_id)
    task_row = runtime.goals.get(task_id)
    if (
        project["origin"] != "owner"
        or project["task_spec"].get("kind") != "project"
        or task_row is None
        or task_row.origin != "owner"
        or task_row.rationale != "validate task metadata"
        or task_row.task_spec.get("created_via") != "wls-ui"
        or task_row.risk.value != "READ"
    ):
        return ArchitecturePassResult(
            "P82",
            "BLOCKED",
            [str(project), str(task_row.to_dict() if task_row else None)],
            ["owner goal metadata persistence validation failed"],
        )
    return ArchitecturePassResult(
        "P82",
        "ADMIT_SHADOW_ONLY",
        [
            "owner_goal_metadata_persisted",
            "ui_task_spec_bound_to_canonical_goal",
            "goal_risk_round_tripped",
        ],
        [
            "Owner Console goal creation stores origin, rationale, task_spec, and risk in the canonical GoalStore without creating a UI-side authority",
        ],
    )


def validate_phase2_ui_hardening_audit(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    defect_checks = {f"D{index:02d}": True for index in range(1, 19)}
    boundary_checks = {
        "loopback_only": True,
        "no_auth_cookie": True,
        "no_persistent_ui_token": True,
        "no_ui_completion_authority": True,
        "no_second_goal_store": True,
        "cycle_request_lock": True,
        "sanitized_500": True,
    }
    passed = runtime.record_ui_hardening_audit(
        defect_checks=defect_checks,
        boundary_checks=boundary_checks,
        test_results=[
            {"name": "ui_projection_and_server", "status": "PASS"},
            {"name": "goal_metadata_persistence", "status": "PASS"},
        ],
        real_browser_e2e=False,
        reason="architecture validation UI hardening audit",
    )
    blocked = runtime.record_ui_hardening_audit(
        defect_checks={**defect_checks, "D09": False},
        boundary_checks={**boundary_checks, "no_auth_cookie": False},
        test_results=[{"name": "ui_server_security", "status": "FAIL"}],
        real_browser_e2e=False,
        reason="architecture validation UI hardening block",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "capability_epoch"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='ui_hardening_audit_recorded'
            """
        )
    }
    if (
        passed["status"] != "UI_HARDENING_CANDIDATE_READY"
        or passed["failure_groups"]["owner_host_gates"] != ["real_browser_e2e"]
        or blocked["status"] != "UI_HARDENING_BLOCKED"
        or blocked["failure_groups"]["defect_failures"] != ["D09"]
        or blocked["failure_groups"]["boundary_failures"] != ["no_auth_cookie"]
        or panel is None
        or panel["status"]["ui_hardening_audit"]["receipt_count"] != 2
        or panel["status"]["ui_hardening_audit"]["persistent_ui_token"] is not False
        or panel["status"]["ui_hardening_audit"]["ui_completion_authority"] is not False
        or event_types != {"ui_hardening_audit_recorded"}
    ):
        return ArchitecturePassResult(
            "P83",
            "BLOCKED",
            [str(passed), str(blocked)],
            ["UI hardening audit validation failed"],
        )
    return ArchitecturePassResult(
        "P83",
        "ADMIT_SHADOW_ONLY",
        [
            str(passed["audit_id"]),
            "ui_hardening_candidate_ready",
            "real_browser_e2e_remains_owner_host_gate",
            "ui_hardening_audit_recorded",
        ],
        [
            "UI hardening audits bind D01-D18 coverage, local security boundaries, tests, and real-browser gate status without creating UI authority or claiming Owner-host browser proof",
        ],
    )


def validate_phase2_offspring_ecology_audit(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home / "runtime"))
    ready = runtime.audit_offspring_ecology(
        population=[
            {
                "offspring_id": "offspring-alpha",
                "status": "CANDIDATE",
                "niche": "repo-reliability",
                "cost": 4,
                "score": 2,
                "novelty": 0.3,
                "evidence_ids": ["ev-alpha"],
            },
            {
                "offspring_id": "offspring-beta",
                "status": "RETIRED",
                "niche": "context-policy",
                "cost": 2,
                "score": 1,
                "novelty": 0.6,
                "evidence_ids": ["ev-beta"],
            },
        ],
        selection_policy={
            "max_population": 3,
            "max_depth": 2,
            "selection_axes": ["score", "novelty", "cost"],
        },
        reason="architecture validation offspring ecology audit",
    )
    blocked = runtime.audit_offspring_ecology(
        population=[
            {
                "offspring_id": "offspring-failed",
                "status": "FAILED",
                "niche": "repo-reliability",
                "cost": 1,
                "score": 0,
                "evidence_ids": ["ev-failed"],
            }
        ],
        selection_policy={"max_population": 1, "max_depth": 1},
        reason="architecture validation offspring ecology block",
    )
    panel = next(
        (
            item
            for item in OwnerConsoleProductProjection().project(runtime.status())[
                "panels"
            ]
            if item.get("panel_id") == "offspring"
        ),
        None,
    )
    event_types = {
        str(row["event_type"])
        for row in runtime.db.query_all(
            """
            SELECT event_type FROM evidence
            WHERE event_type='offspring_ecology_audit_recorded'
            """
        )
    }
    if (
        ready["status"] != "ECOLOGY_REVIEW_READY"
        or ready["productive_count"] != 2
        or ready["second_authority_created"] is not False
        or blocked["status"] != "ECOLOGY_REVIEW_BLOCKED"
        or "all_candidates_failed" not in blocked["blocked_reasons"]
        or panel is None
        or panel["status"]["ecology"]["receipt_count"] != 2
        or panel["status"]["ecology"]["absorption_executed"] is not False
        or panel["status"]["ecology"]["promotion_executed"] is not False
        or event_types != {"offspring_ecology_audit_recorded"}
    ):
        return ArchitecturePassResult(
            "P84",
            "BLOCKED",
            [str(ready), str(blocked)],
            ["offspring ecology audit validation failed"],
        )
    return ArchitecturePassResult(
        "P84",
        "ADMIT_SHADOW_ONLY",
        [
            str(ready["audit_id"]),
            "offspring_ecology_review_ready",
            "failed_population_blocks_ecology",
            "offspring_ecology_audit_recorded",
        ],
        [
            "Offspring ecology audits compare candidate population productivity, niche diversity, budget cost, and failure status without archive search execution, absorption, promotion, or second authority",
        ],
    )


def _insert_waiting_write_action(
    runtime: LivingSystem, path: Path, body: str
) -> ActionSpec:
    plan = Plan(rationale="architecture validation approval gate", actions=[])
    action = ActionSpec(
        tool="write_file",
        arguments={"path": str(path), "content": body},
        purpose="Validate exact approval and receipt for a reversible sandbox write",
        expected_result="Sandbox file written only after approval",
        risk=RiskLevel.REVERSIBLE_WRITE,
        status=ActionStatus.WAITING_APPROVAL,
    )
    with runtime.db.transaction() as connection:
        connection.execute(
            "INSERT INTO plans(plan_id,cycle_id,plan_json,status,created_at) VALUES (?,?,?,?,?)",
            (
                plan.plan_id,
                "architecture-validation",
                json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True),
                "WAITING_APPROVAL",
                plan.created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO actions(
                action_id,plan_id,tool,arguments_json,purpose,expected_result,risk,
                acceptance_json,idempotency_key,status,side_effect_class
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                action.action_id,
                plan.plan_id,
                action.tool,
                json.dumps(action.arguments, ensure_ascii=False, sort_keys=True),
                action.purpose,
                action.expected_result,
                action.risk.value,
                json.dumps(action.acceptance, ensure_ascii=False),
                action.idempotency_key,
                action.status.value,
                "reversible",
            ),
        )
        runtime.ledger.append(
            "architecture_validation_action_prepared",
            {"action_id": action.action_id, "tool": action.tool, "created_at": utc_now()},
            connection,
        )
    return action


class _BrowserFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
            return
        body = b"<html><body>WLS browser fixture</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return
