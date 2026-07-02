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
from .read_only_organs import ReadOnlyTaskRequest
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
        "goals",
        "actions_approval",
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
