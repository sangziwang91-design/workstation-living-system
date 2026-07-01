from __future__ import annotations

from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import json
import threading

from .browser_adapter import BrowserReadOnlyAdapter, BrowserReadOnlyRequest
from .capabilities import baseline_registry
from .channel_gateway import ChannelMessage
from .computer_adapter import ComputerUseAdapter, ComputerUseContract
from .config import default_config
from .coding_adapter import CodingTaskContract
from .runtime import LivingSystem
from .scheduler import ScheduledEvent
from .schemas import ActionSpec, ActionStatus, Plan, RiskLevel, utc_now


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
