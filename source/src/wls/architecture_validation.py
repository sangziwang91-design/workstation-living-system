from __future__ import annotations

from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import json
import threading

from .a2a_adapter import A2AAdapter, ArtifactEnvelope, TaskContract, payload_digest
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
from .schemas import ActionSpec, ActionStatus, Plan, RiskLevel, utc_now
from .ui_projection import OwnerConsoleProductProjection
from .wechat_adapter import WeChatW0W1Adapter
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
