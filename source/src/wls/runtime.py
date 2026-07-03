from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import importlib
import json
import os
import signal
import time

from ._version import __version__
from .approval import ApprovalManager
from .autonomy import AutonomySystem
from .attention import AttentionSystem
from .capabilities import baseline_registry
from .channel_gateway import ChannelGateway, ChannelMessage
from .cognition import CognitiveEngine
from .config import RuntimeConfig, load_or_create_config
from .db import Database
from .drives import DriveSystem
from .evaluator import Evaluator
from .evidence import EvidenceLedger
from .growth_cycle import GrowthCycleManager
from .learning import LearningSystem
from .memory_attribution import MemoryAttributionStore
from .lease import ProcessLease
from .planner import Planner
from .policy import PolicyEngine
from .relationships import RelationshipMemory
from .a2a_adapter import A2AAdapter, ArtifactEnvelope, TaskContract
from .mcp_adapter import McpCandidate, McpTrustGate
from .read_only_organs import ReadOnlyTaskReceipt, ReadOnlyTaskRequest
from .schemas import (
    ActionSpec,
    ActionStatus,
    Event,
    EvidenceKind,
    Goal,
    MemoryItem,
    Observation,
    Plan,
    RiskLevel,
    VerificationStatus,
    new_id,
    utc_now,
)
from .self_model import SelfModel
from .sensors import build_sensor
from .scheduler import EventScheduler, ScheduledEvent
from .skills import SkillLibrary
from .sleep import SleepConsolidator
from .stores import EventStore, GoalStore, MemoryStore
from .temporal_world import TemporalCausalWorld
from .tools import ToolRegistry
from .wechat_adapter import WeChatW0W1Adapter
from .world import WorldModel


class LivingSystem:
    """Persistent observe-model-attend-plan-act-learn-consolidate runtime."""

    def __init__(self, config: RuntimeConfig):
        config.validate()
        config.ensure_directories()
        self.config = config
        self.db = Database(config.db_path)
        self.ledger = EvidenceLedger(self.db, config.secret_path)
        self.events = EventStore(self.db, self.ledger)
        self.goals = GoalStore(self.db, self.ledger)
        self.memories = MemoryStore(self.db, self.ledger)
        self.world = WorldModel(self.db, self.ledger)
        self.temporal_world = TemporalCausalWorld(self.db, self.ledger)
        self.drives = DriveSystem(self.db, self.ledger)
        self.policy = PolicyEngine(config)
        self.approvals = ApprovalManager(
            self.db, self.ledger, config.secret_path.with_name("approval.key")
        )
        self.tools = ToolRegistry(self.policy)
        self.evaluator = Evaluator()
        self.self_model = SelfModel(self.db, self.ledger)
        self.relationships = RelationshipMemory(self.db, self.ledger)
        self.skills = SkillLibrary(self.db, self.ledger)
        self.learning = LearningSystem(self.db, self.ledger, self.memories, self.skills)
        self.autonomy = AutonomySystem(
            self.db, self.ledger, self.goals, self.world, config
        )
        self.sleep = SleepConsolidator(
            self.db,
            self.ledger,
            self.memories,
            self.world,
            self.self_model,
            self.skills,
            self.learning,
        )
        self.attention = AttentionSystem(config.workspace_capacity)
        self.cognition = CognitiveEngine(
            self.db, self.ledger, self.temporal_world, config
        )
        self.memory_attribution = MemoryAttributionStore(
            self.db, self.ledger, self.memories.causal
        )
        self.planner = Planner(config, self.cognition, self.ledger)
        self.growth = GrowthCycleManager(self)
        self.capabilities = baseline_registry()
        self.capabilities.assert_no_duplicate_authority()
        self.channel_gateway = ChannelGateway()
        self.event_scheduler = EventScheduler()
        self.mcp_trust = McpTrustGate()
        self.a2a_adapter = A2AAdapter()
        self._load_plugins()
        self.lease = ProcessLease(config.home_path / "state" / "runtime.lock")
        self.worker_id = f"wls-{os.getpid()}-{new_id('worker')[-8:]}"
        self._stop = False
        self._initialize_runtime()

    def _load_plugins(self) -> None:
        for module_name in self.config.plugin_modules:
            module = importlib.import_module(module_name)
            register = getattr(module, "register_wls", None)
            if not callable(register):
                raise ValueError(
                    f"plugin {module_name} must expose register_wls(runtime)"
                )
            register(self)
            self.ledger.append("plugin_loaded", {"module": module_name})

    @classmethod
    def from_config_path(
        cls, path: str | Path, home: str | Path | None = None
    ) -> "LivingSystem":
        return cls(load_or_create_config(path, home))

    def _initialize_runtime(self) -> None:
        evidence_id = self.ledger.append(
            "runtime_initialized",
            {
                "version": __version__,
                "home": str(self.config.home_path),
                "read_only": self.config.read_only,
            },
        )
        self.self_model.initialize_identity(self.config.identity_name, evidence_id)
        self.db.set_runtime("version", __version__)
        self.db.set_runtime("read_only", self.config.read_only)
        self._recover_actions()
        self.events.recover_stale_reservations(
            (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        )

    def _recover_actions(self) -> dict[str, int]:
        rows = self.db.query_all(
            "SELECT action_id,side_effect_class FROM actions WHERE status='RUNNING'"
        )
        safe = 0
        unknown = 0
        with self.db.transaction() as connection:
            for row in rows:
                if row["side_effect_class"] == "none":
                    connection.execute(
                        "UPDATE actions SET status='PLANNED',started_at=NULL,error=? WHERE action_id=?",
                        (
                            "recovered after interrupted read-only action",
                            row["action_id"],
                        ),
                    )
                    safe += 1
                else:
                    connection.execute(
                        "UPDATE actions SET status='UNKNOWN_SIDE_EFFECT',finished_at=?,error=? WHERE action_id=?",
                        (
                            utc_now(),
                            "process ended during possible side effect; manual resolution required",
                            row["action_id"],
                        ),
                    )
                    unknown += 1
            if rows:
                self.ledger.append(
                    "action_recovery",
                    {"safe_reset": safe, "unknown_side_effect": unknown},
                    connection,
                )
        return {"safe_reset": safe, "unknown_side_effect": unknown}

    def ingest_event(self, event: Event) -> tuple[str, bool]:
        return self.events.add_event(event)

    def add_goal(self, goal: Goal) -> str:
        return self.goals.add(goal)

    def ingest_channel_message(self, message: ChannelMessage) -> tuple[str, bool]:
        return self.channel_gateway.submit(message, self.events)

    def emit_scheduled_event(self, item: ScheduledEvent) -> tuple[str, bool]:
        return self.event_scheduler.submit_due(item, self.events)

    def intake_scheduled_event(self, item: ScheduledEvent) -> dict[str, Any]:
        event = self.event_scheduler.emit_due(item)
        event_id, inserted = self.events.add_event(event)
        receipt = {
            "schedule_id": item.schedule_id,
            "event_id": event_id,
            "inserted": inserted,
            "event_type": item.event_type,
            "source": item.source,
            "due_at": item.due_at,
            "emitted_at": item.emitted_at,
            "status": "QUEUED_EVENT_ONLY",
            "allowed_next_authority": "EventStore",
            "creates_goal": False,
            "creates_action": False,
            "direct_tool_execution": False,
            "writes_canonical_state": False,
            "claim_ceiling": "scheduled event queued only; no standing goal or action created",
        }
        current = self.scheduled_event_receipts(limit=100)
        updated = [
            receipt,
            *[
                row
                for row in current
                if row.get("schedule_id") != item.schedule_id
                or row.get("due_at") != item.due_at
            ],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("scheduled_event_receipts", updated, connection)
            self.ledger.append(
                "scheduled_event_queued",
                {
                    "schedule_id": item.schedule_id,
                    "event_id": event_id,
                    "inserted": inserted,
                    "event_type": item.event_type,
                    "due_at": item.due_at,
                },
                connection,
            )
        return receipt

    def intake_read_only_task(
        self, request: ReadOnlyTaskRequest
    ) -> dict[str, Any]:
        receipt = request.submit(self.events)
        preview = self._record_read_only_plan_preview(request, receipt)
        return {"receipt": receipt.to_dict(), "preview": preview}

    def read_only_plan_previews(self, limit: int = 20) -> list[dict[str, Any]]:
        previews = self.db.get_runtime("read_only_plan_previews", [])
        if not isinstance(previews, list):
            return []
        return previews[: max(0, int(limit))]

    def scheduled_event_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("scheduled_event_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def external_handoff_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("external_handoff_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def approval_channel_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("approval_channel_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def provider_route_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("provider_route_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def record_provider_route_receipt(self, *, reason: str) -> dict[str, Any]:
        route = self.planner.route_summary()
        evidence = route.get("evidence", {})
        provider = (
            self._json_safe(evidence.get("provider", {}))
            if isinstance(evidence, dict)
            else {}
        )
        request = (
            self._json_safe(evidence.get("request", {}))
            if isinstance(evidence, dict)
            else {}
        )
        receipt = {
            "receipt_type": "PROVIDER_ROUTE",
            "status": "RECORDED_ROUTE",
            "provider_id": route.get("provider_id"),
            "planner_provider": self.planner.provider_type,
            "rationale": route.get("rationale"),
            "fallback_chain": route.get("fallback_chain", []),
            "provider": provider,
            "request": request,
            "reason": reason,
            "planner_authority": "Planner",
            "creates_plan": False,
            "creates_action": False,
            "direct_model_call": False,
            "direct_tool_execution": False,
            "claim_ceiling": "route evidence receipt only; no model call or plan generated",
            "created_at": utc_now(),
        }
        current = self.provider_route_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("provider_route_receipts", updated, connection)
            self.ledger.append("provider_route_recorded", receipt, connection)
        return receipt

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): LivingSystem._json_safe(item) for key, item in value.items()}
        if isinstance(value, set):
            return sorted(LivingSystem._json_safe(item) for item in value)
        if isinstance(value, (list, tuple)):
            return [LivingSystem._json_safe(item) for item in value]
        return value

    def draft_wechat_approval_request(self, action_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT action_id,plan_id,tool,purpose,risk,status FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        draft = WeChatW0W1Adapter("W2").approval_request_notification(dict(row))
        receipt = {
            "receipt_type": "WECHAT_APPROVAL_REQUEST",
            **draft,
            "creates_approval": False,
            "approval_authority": "ApprovalManager",
            "claim_ceiling": "draft notification only; no approval issued or action executed",
        }
        self._record_approval_channel_receipt("wechat_approval_request_drafted", receipt)
        return receipt

    def intake_wechat_approval_decision(
        self,
        message: ChannelMessage,
        *,
        action_id: str,
        decision: str,
        reason: str,
    ) -> dict[str, Any]:
        event = WeChatW0W1Adapter("W2").approval_decision_event(
            message, action_id=action_id, decision=decision, reason=reason
        )
        event_id, inserted = self.events.add_event(event)
        receipt = {
            "receipt_type": "WECHAT_APPROVAL_DECISION_EVENT",
            "status": "QUEUED_EVENT_ONLY",
            "event_id": event_id,
            "inserted": inserted,
            "action_id": action_id,
            "decision": decision.upper(),
            "message_id": message.message_id,
            "creates_approval": False,
            "executes_action": False,
            "direct_tool_execution": False,
            "allowed_next_authority": "ApprovalManager",
            "claim_ceiling": "approval decision event only; ApprovalManager must issue exact approval",
            "created_at": utc_now(),
        }
        self._record_approval_channel_receipt("wechat_approval_decision_queued", receipt)
        return receipt

    def admit_mcp_candidate(self, candidate: McpCandidate, *, reason: str) -> dict[str, Any]:
        result = self.mcp_trust.admit(candidate)
        receipt = {
            "receipt_type": "MCP_CANDIDATE",
            "status": result["status"],
            "server_id": candidate.server_id,
            "identity_digest": candidate.identity_digest,
            "transport": candidate.transport,
            "side_effect_class": candidate.side_effect_class,
            "review_status": candidate.review_status,
            "reason": reason,
            "creates_goal": False,
            "creates_action": False,
            "writes_canonical_memory": False,
            "direct_tool_execution": False,
            "candidate_only": True,
            "claim_ceiling": "reviewed MCP candidate only; no tool execution or authority transfer",
            "created_at": utc_now(),
        }
        self._record_external_handoff_receipt("mcp_candidate_admitted", receipt)
        return receipt

    def receive_a2a_artifact(
        self,
        contract: TaskContract,
        envelope: ArtifactEnvelope,
        *,
        reason: str,
    ) -> dict[str, Any]:
        result = self.a2a_adapter.receive(contract, envelope)
        receipt = {
            "receipt_type": "A2A_ARTIFACT",
            "status": result["status"],
            "task_id": contract.task_id,
            "artifact_type": envelope.artifact_type,
            "payload_hash": envelope.hashes.get("payload_sha256"),
            "allowed_outputs": list(contract.allowed_outputs),
            "expires_at": contract.expires_at,
            "reason": reason,
            "creates_goal": False,
            "creates_action": False,
            "writes_canonical_memory": False,
            "direct_tool_execution": False,
            "candidate_only": True,
            "claim_ceiling": "external worker artifact candidate only; no canonical truth claim",
            "created_at": utc_now(),
        }
        self._record_external_handoff_receipt("a2a_artifact_received", receipt)
        return receipt

    def _record_external_handoff_receipt(
        self, event_type: str, receipt: dict[str, Any]
    ) -> None:
        current = self.external_handoff_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("external_handoff_receipts", updated, connection)
            self.ledger.append(event_type, receipt, connection)

    def _record_approval_channel_receipt(
        self, event_type: str, receipt: dict[str, Any]
    ) -> None:
        current = self.approval_channel_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("approval_channel_receipts", updated, connection)
            self.ledger.append(event_type, receipt, connection)

    def read_only_execution_preflights(self, limit: int = 20) -> list[dict[str, Any]]:
        preflights = self.db.get_runtime("read_only_execution_preflights", [])
        if not isinstance(preflights, list):
            return []
        return preflights[: max(0, int(limit))]

    def read_only_execution_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("read_only_execution_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def read_only_result_projections(self, limit: int = 20) -> list[dict[str, Any]]:
        projections = self.db.get_runtime("read_only_result_projections", [])
        if not isinstance(projections, list):
            return []
        return projections[: max(0, int(limit))]

    def read_only_projection_reviews(self, limit: int = 20) -> list[dict[str, Any]]:
        reviews = self.db.get_runtime("read_only_projection_reviews", [])
        if not isinstance(reviews, list):
            return []
        return reviews[: max(0, int(limit))]

    def _record_read_only_plan_preview(
        self, request: ReadOnlyTaskRequest, receipt: ReadOnlyTaskReceipt
    ) -> dict[str, Any]:
        candidate = request.to_plan_candidate()
        preview = {
            "request_id": request.request_id,
            "organ_id": request.organ_id,
            "event_id": receipt.event_id,
            "status": "PREVIEW_ONLY",
            "created_at": utc_now(),
            "candidate": candidate,
            "writes_canonical_state": False,
            "direct_tool_execution": False,
            "creates_plan_row": False,
            "creates_action_row": False,
            "claim_ceiling": "Owner Console preview only; Planner has not admitted a plan",
        }
        current = self.read_only_plan_previews(limit=100)
        updated = [
            preview,
            *[item for item in current if item.get("request_id") != request.request_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("read_only_plan_previews", updated, connection)
            self.ledger.append(
                "read_only_plan_preview_recorded",
                {
                    "request_id": request.request_id,
                    "organ_id": request.organ_id,
                    "event_id": receipt.event_id,
                    "status": preview["status"],
                },
                connection,
            )
        return preview

    def admit_read_only_plan_preview(
        self, request_id: str, *, reason: str = "owner-approved preview admission"
    ) -> dict[str, Any]:
        previews = self.read_only_plan_previews(limit=100)
        preview = next(
            (item for item in previews if item.get("request_id") == request_id),
            None,
        )
        if preview is None:
            raise KeyError(f"unknown read-only plan preview: {request_id}")
        if preview.get("status") not in {"PREVIEW_ONLY", "ADMITTED_AS_PLAN"}:
            raise ValueError(f"preview cannot be admitted from {preview.get('status')}")
        if preview.get("admitted_plan_id"):
            return {
                "status": "ALREADY_ADMITTED",
                "plan_id": preview["admitted_plan_id"],
                "preview": preview,
            }
        candidate = preview.get("candidate", {})
        actions, rejected_hints = self._read_only_candidate_actions(candidate)
        if not actions:
            raise ValueError("read-only preview has no admissible registered actions")
        plan = Plan(
            rationale=(
                f"Planner-admitted read-only preview {request_id}: "
                f"{candidate.get('rationale', '')}"
            ).strip(),
            actions=actions,
            unknowns=[
                "origin=read_only_plan_preview",
                f"request_id={request_id}",
                *[f"rejected_tool_hint={hint}" for hint in rejected_hints],
            ],
        )
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO plans(plan_id,cycle_id,plan_json,status,created_at) VALUES (?,?,?,?,?)",
                (
                    plan.plan_id,
                    f"preview:{request_id}",
                    json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True),
                    "PLANNED",
                    plan.created_at,
                ),
            )
            for action in plan.actions:
                side_effect_class = self.tools.get(action.tool).side_effect_class
                if action.risk != RiskLevel.READ or side_effect_class != "none":
                    raise PermissionError("only read-only side-effect-free actions can be admitted")
                connection.execute(
                    """
                    INSERT INTO actions(
                        action_id,plan_id,goal_id,skill_id,tool,arguments_json,purpose,expected_result,
                        risk,acceptance_json,idempotency_key,status,side_effect_class
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        action.action_id,
                        plan.plan_id,
                        action.goal_id,
                        action.skill_id,
                        action.tool,
                        json.dumps(
                            action.arguments, ensure_ascii=False, sort_keys=True
                        ),
                        action.purpose,
                        action.expected_result,
                        action.risk.value,
                        json.dumps(action.acceptance, ensure_ascii=False),
                        action.idempotency_key,
                        action.status.value,
                        side_effect_class,
                    ),
                )
            admitted_preview = {
                **preview,
                "status": "ADMITTED_AS_PLAN",
                "admitted_at": utc_now(),
                "admitted_plan_id": plan.plan_id,
                "admitted_action_ids": [action.action_id for action in plan.actions],
                "rejected_tool_hints": rejected_hints,
                "admission_reason": reason,
                "creates_plan_row": True,
                "creates_action_row": True,
                "direct_tool_execution": False,
                "claim_ceiling": "Planner admitted read-only plan; actions are not executed by admission",
            }
            updated = [
                admitted_preview,
                *[item for item in previews if item.get("request_id") != request_id],
            ][:100]
            self.db.set_runtime("read_only_plan_previews", updated, connection)
            self.ledger.append(
                "read_only_plan_preview_admitted",
                {
                    "request_id": request_id,
                    "plan_id": plan.plan_id,
                    "action_ids": [action.action_id for action in plan.actions],
                    "rejected_tool_hints": rejected_hints,
                    "reason": reason,
                },
                connection,
            )
        return {
            "status": "ADMITTED_AS_PLAN",
            "plan_id": plan.plan_id,
            "action_ids": [action.action_id for action in plan.actions],
            "rejected_tool_hints": rejected_hints,
            "preview": admitted_preview,
        }

    def preflight_read_only_plan(self, plan_id: str) -> dict[str, Any]:
        plan_row = self.db.query_one("SELECT status FROM plans WHERE plan_id=?", (plan_id,))
        if plan_row is None:
            raise KeyError(f"unknown plan: {plan_id}")
        action_rows = self.db.query_all(
            "SELECT * FROM actions WHERE plan_id=? ORDER BY rowid", (plan_id,)
        )
        checks: list[dict[str, Any]] = []
        ok = True
        for row in action_rows:
            action = self._action_from_row(row)
            validation_error = None
            try:
                self.policy.validate_arguments(action)
            except Exception as exc:
                validation_error = f"{type(exc).__name__}: {exc}"
            decision = self.policy.decide(action, approval_valid=False)
            tool = self.tools.get(action.tool)
            action_ok = (
                validation_error is None
                and row["status"] == ActionStatus.PLANNED.value
                and action.risk == RiskLevel.READ
                and tool.side_effect_class == "none"
                and decision.allowed
                and not decision.requires_approval
            )
            ok = ok and action_ok
            checks.append(
                {
                    "action_id": action.action_id,
                    "tool": action.tool,
                    "status": row["status"],
                    "risk": action.risk.value,
                    "side_effect_class": tool.side_effect_class,
                    "arguments_valid": validation_error is None,
                    "validation_error": validation_error,
                    "policy_allowed": decision.allowed,
                    "requires_approval": decision.requires_approval,
                    "policy_reason": decision.reason,
                    "preflight_ok": action_ok,
                }
            )
        preflight = {
            "plan_id": plan_id,
            "status": "READY_FOR_EXECUTION" if ok and checks else "BLOCKED",
            "checked_at": utc_now(),
            "actions": checks,
            "direct_tool_execution": False,
            "writes_canonical_state": False,
            "claim_ceiling": "preflight only; no actions executed",
        }
        current = self.read_only_execution_preflights(limit=100)
        updated = [
            preflight,
            *[item for item in current if item.get("plan_id") != plan_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("read_only_execution_preflights", updated, connection)
            self.ledger.append(
                "read_only_execution_preflight_recorded",
                {
                    "plan_id": plan_id,
                    "status": preflight["status"],
                    "action_ids": [item["action_id"] for item in checks],
                },
                connection,
            )
        return preflight

    def execute_preflighted_read_only_plan(self, plan_id: str) -> dict[str, Any]:
        preflight = next(
            (
                item
                for item in self.read_only_execution_preflights(limit=100)
                if item.get("plan_id") == plan_id
            ),
            None,
        )
        if preflight is None:
            raise PermissionError("read-only execution requires a recorded preflight")
        if preflight.get("status") != "READY_FOR_EXECUTION":
            raise PermissionError(f"preflight is not ready: {preflight.get('status')}")
        action_rows = self.db.query_all(
            "SELECT * FROM actions WHERE plan_id=? ORDER BY rowid", (plan_id,)
        )
        allowed_ids = {
            str(item["action_id"])
            for item in preflight.get("actions", [])
            if item.get("preflight_ok")
        }
        outcomes: list[dict[str, Any]] = []
        for row in action_rows:
            action_id = str(row["action_id"])
            if action_id not in allowed_ids:
                raise PermissionError(f"action missing ready preflight: {action_id}")
            if row["status"] != ActionStatus.PLANNED.value:
                raise PermissionError(f"action is not PLANNED: {action_id}")
            action = self._action_from_row(row)
            tool = self.tools.get(action.tool)
            if action.risk != RiskLevel.READ or tool.side_effect_class != "none":
                raise PermissionError("only READ/none actions may execute through this path")
            outcomes.append(self._execute_action(action))
        self._refresh_plan_status(plan_id)
        receipt = {
            "plan_id": plan_id,
            "status": "EXECUTED_READ_ONLY",
            "executed_at": utc_now(),
            "outcomes": outcomes,
            "all_succeeded": all(bool(item.get("success")) for item in outcomes),
            "direct_tool_execution": True,
            "writes_canonical_state": False,
            "claim_ceiling": "read-only preflighted execution receipts only",
        }
        current = self.read_only_execution_receipts(limit=100)
        updated = [
            receipt,
            *[item for item in current if item.get("plan_id") != plan_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("read_only_execution_receipts", updated, connection)
            self.ledger.append(
                "read_only_plan_executed",
                {
                    "plan_id": plan_id,
                    "status": receipt["status"],
                    "action_ids": [item.get("action_id") for item in outcomes],
                    "all_succeeded": receipt["all_succeeded"],
                },
                connection,
            )
        return receipt

    def project_read_only_execution_receipt(self, plan_id: str) -> dict[str, Any]:
        receipt = next(
            (
                item
                for item in self.read_only_execution_receipts(limit=100)
                if item.get("plan_id") == plan_id
            ),
            None,
        )
        if receipt is None:
            raise KeyError(f"unknown read-only execution receipt: {plan_id}")
        if not receipt.get("all_succeeded"):
            raise ValueError("only successful read-only receipts can be projected")
        source_ids = [
            str(item.get("action_id"))
            for item in receipt.get("outcomes", [])
            if item.get("action_id")
        ]
        if not source_ids:
            raise ValueError("receipt has no action sources")
        summary = {
            "plan_id": plan_id,
            "outcome_count": len(receipt.get("outcomes", [])),
            "claim_ceiling": "candidate projection from read-only tool receipt",
        }
        memory = MemoryItem(
            memory_type="read_only_execution_candidate",
            content={
                "summary": summary,
                "receipt": receipt,
                "candidate_only": True,
                "does_not_complete_goal": True,
                "does_not_promote_skill": True,
            },
            importance=0.35,
            confidence=0.55,
            source_ids=source_ids,
            tags=["candidate_projection", "read_only_execution"],
        )
        memory_id = self.memories.add(memory)
        observation = Observation(
            source="read_only_execution_projection",
            kind="candidate_projection",
            subject=f"plan:{plan_id}",
            predicate="read_only_execution_receipt_projected",
            value={
                "memory_id": memory_id,
                "outcome_count": len(receipt.get("outcomes", [])),
                "candidate_only": True,
            },
            confidence=0.55,
            evidence_kind=EvidenceKind.INFERENCE,
            verification=VerificationStatus.INFERENCE,
            metadata={"plan_id": plan_id, "source_action_ids": source_ids},
        )
        world_result = self.world.assimilate(observation)
        projection = {
            "plan_id": plan_id,
            "status": "PROJECTED_CANDIDATE",
            "projected_at": utc_now(),
            "memory_id": memory_id,
            "fact_id": world_result["fact_id"],
            "observation_id": observation.observation_id,
            "source_action_ids": source_ids,
            "candidate_only": True,
            "writes_canonical_memory": True,
            "writes_world_projection": True,
            "claim_ceiling": "candidate memory/world projection, not final fact or skill",
        }
        current = self.read_only_result_projections(limit=100)
        updated = [
            projection,
            *[item for item in current if item.get("plan_id") != plan_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("read_only_result_projections", updated, connection)
            self.ledger.append(
                "read_only_execution_result_projected",
                {
                    "plan_id": plan_id,
                    "memory_id": memory_id,
                    "fact_id": world_result["fact_id"],
                    "observation_id": observation.observation_id,
                },
                connection,
            )
        return projection

    def review_read_only_result_projection(
        self, plan_id: str, decision: str, *, reason: str
    ) -> dict[str, Any]:
        if decision not in {"ACCEPT_CANDIDATE", "ROLLBACK_CANDIDATE"}:
            raise ValueError("invalid projection review decision")
        projection = next(
            (
                item
                for item in self.read_only_result_projections(limit=100)
                if item.get("plan_id") == plan_id
            ),
            None,
        )
        if projection is None:
            raise KeyError(f"unknown read-only result projection: {plan_id}")
        memory_id = str(projection["memory_id"])
        fact_id = str(projection["fact_id"])
        rolled_back = False
        if decision == "ROLLBACK_CANDIDATE":
            self.memories.deactivate(memory_id)
            self.db.execute("UPDATE world_facts SET active=0 WHERE fact_id=?", (fact_id,))
            rolled_back = True
        reviewed = {
            **projection,
            "review_status": decision,
            "reviewed_at": utc_now(),
            "review_reason": reason,
            "rolled_back": rolled_back,
            "candidate_only": True,
            "skill_promotion_executed": False,
            "goal_completion_executed": False,
        }
        projections = self.read_only_result_projections(limit=100)
        updated_projections = [
            reviewed,
            *[item for item in projections if item.get("plan_id") != plan_id],
        ][:100]
        reviews = self.read_only_projection_reviews(limit=100)
        review_record = {
            "plan_id": plan_id,
            "decision": decision,
            "reason": reason,
            "memory_id": memory_id,
            "fact_id": fact_id,
            "rolled_back": rolled_back,
            "created_at": utc_now(),
        }
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "read_only_result_projections", updated_projections, connection
            )
            self.db.set_runtime(
                "read_only_projection_reviews",
                [review_record, *reviews][:100],
                connection,
            )
            self.ledger.append(
                "read_only_result_projection_reviewed",
                review_record,
                connection,
            )
        return review_record

    def _read_only_candidate_actions(
        self, candidate: dict[str, Any]
    ) -> tuple[list[ActionSpec], list[str]]:
        actions: list[ActionSpec] = []
        rejected_hints: list[str] = []
        for item in candidate.get("candidate_actions", []):
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool", ""))
            try:
                definition = self.tools.get(tool)
            except KeyError:
                rejected_hints.append(tool)
                continue
            if definition.side_effect_class != "none" or item.get("risk") != "READ":
                rejected_hints.append(tool)
                continue
            try:
                arguments = self._read_only_action_arguments(
                    tool, dict(item.get("arguments", {}))
                )
            except ValueError:
                rejected_hints.append(tool)
                continue
            acceptance = self._read_only_acceptance(tool)
            actions.append(
                ActionSpec(
                    tool=tool,
                    arguments=arguments,
                    purpose=str(item.get("purpose", "Admit read-only preview action")),
                    expected_result=str(
                        item.get("expected_result", "bounded read-only receipt")
                    ),
                    risk=RiskLevel.READ,
                    acceptance=acceptance,
                )
            )
        return actions, rejected_hints

    def _read_only_action_arguments(self, tool: str, candidate_arguments: dict[str, Any]) -> dict[str, Any]:
        inputs = candidate_arguments.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
        if tool in {"read_file", "list_directory", "inspect_asset"}:
            input_key = (
                "dir_path"
                if tool == "list_directory"
                else ("asset_path" if tool == "inspect_asset" else "file_path")
            )
            path = inputs.get(input_key, inputs.get("path"))
            if not isinstance(path, str) or not path.strip():
                raise ValueError("read-only path tools require path input")
            path_obj = Path(path).expanduser().resolve(strict=False)
            if tool == "read_file" and not path_obj.is_file():
                raise ValueError("read_file requires file input")
            if tool == "list_directory" and path_obj.exists() and not path_obj.is_dir():
                raise ValueError("list_directory requires directory input")
            if tool == "inspect_asset" and not path_obj.is_file():
                raise ValueError("inspect_asset requires file input")
            arguments: dict[str, Any] = {"path": path}
            if tool in {"read_file", "inspect_asset"}:
                arguments["max_bytes"] = int(inputs.get("max_bytes", 524288))
            if tool == "list_directory":
                arguments["limit"] = int(inputs.get("limit", 200))
            return arguments
        if tool == "noop":
            return {
                "reason": str(
                    inputs.get(
                        "reason",
                        "Read-only organ candidate retained as deliberate no-op",
                    )
                )
            }
        if tool == "http_get":
            url = inputs.get("url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError("http_get requires url input")
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme not in {"http", "https"} or not host:
                raise ValueError("http_get requires an http(s) URL with hostname")
            return {
                "url": url,
                "host": host,
                "timeout": float(inputs.get("timeout", 5.0)),
                "max_bytes": int(inputs.get("max_bytes", 1048576)),
            }
        if tool == "inspect_coding_candidate":
            worktree = inputs.get("worktree_path", inputs.get("path"))
            if not isinstance(worktree, str) or not worktree.strip():
                raise ValueError("inspect_coding_candidate requires worktree_path input")
            changed_files = inputs.get("changed_files", [])
            tests = inputs.get("tests", [])
            rollback = inputs.get("rollback", [])
            if not isinstance(changed_files, list) or not all(
                isinstance(item, str) for item in changed_files
            ):
                raise ValueError("changed_files must be a string list")
            if not isinstance(tests, list) or not all(
                isinstance(item, str) for item in tests
            ):
                raise ValueError("tests must be a string list")
            if not isinstance(rollback, list) or not all(
                isinstance(item, str) for item in rollback
            ):
                raise ValueError("rollback must be a string list")
            return {
                "path": worktree,
                "task_id": str(inputs.get("task_id", candidate_arguments.get("request_id", ""))),
                "base_sha": str(inputs.get("base_sha", "")),
                "changed_files": changed_files,
                "tests": tests,
                "rollback": rollback,
            }
        raise ValueError(f"unsupported read-only tool mapping: {tool}")

    @staticmethod
    def _read_only_acceptance(tool: str) -> list[str]:
        if tool == "list_directory":
            return ["output contains items"]
        if tool == "read_file":
            return ["output contains path", "output contains text or binary marker"]
        if tool == "noop":
            return ["output ok is true"]
        if tool == "http_get":
            return ["output contains status", "output contains body"]
        if tool == "inspect_asset":
            return ["output contains sha256", "output contains mime_type"]
        if tool == "inspect_coding_candidate":
            return ["output contains changed_files", "output contains rollback"]
        return []

    def run_cycle(self) -> dict[str, Any]:
        if self.db.get_runtime("kill_switch", False):
            return {
                "status": "KILLED",
                "reason": self.db.get_runtime("kill_reason", ""),
            }
        if self.db.get_runtime("paused", False):
            return {
                "status": "PAUSED",
                "reason": self.db.get_runtime("pause_reason", ""),
            }
        with self.lease:
            return self._run_cycle_locked()

    def _run_cycle_locked(self) -> dict[str, Any]:
        cycle_id = new_id("cycle")
        started_at = utc_now()
        self.db.execute(
            "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
            (cycle_id, started_at, "RUNNING"),
        )
        prediction_errors: list[dict[str, Any]] = []
        sensor_summary: list[dict[str, Any]] = []
        reserved: list[Event] = []
        selected_event_ids: set[str] = set()
        plan_persisted = False
        try:
            recovery_outcomes = self._resume_durable_actions()
            sensor_summary, prediction_errors = self._poll_due_sensors()
            reserved = self.events.reserve(
                self.worker_id, self.config.max_events_per_cycle
            )
            autonomous_goal_ids = self.autonomy.consider()
            active_goals = self.goals.active(limit=20)
            query_text = self._query_text(reserved, active_goals)
            memory_mode = str(
                self.config.provider.get("memory_mode", "enabled")
            ).lower()
            memory_query_context = self.memories.build_query_context(
                reserved, active_goals
            )
            memory_retrieval = self.memories.retrieve_causal(
                query_text,
                self.config.memory_retrieval_limit,
                context=memory_query_context,
                enabled=memory_mode != "disabled",
                frozen=memory_mode == "frozen",
            )
            retrieved_memories = memory_retrieval["selected"]
            resource_snapshot = self._resource_snapshot()
            meaningful_input = bool(
                reserved
                or active_goals
                or prediction_errors
                or recovery_outcomes
                or autonomous_goal_ids
            )
            if meaningful_input:
                drives, affect, appraisal = self.drives.appraise(
                    reserved,
                    outcomes=recovery_outcomes,
                    resource_snapshot=resource_snapshot,
                )
            else:
                drives, affect = self.drives.load()
                appraisal = {"idle": True}
            workspace = self.attention.select(
                reserved, active_goals, retrieved_memories, drives, affect
            )
            selected_event_ids = self.attention.event_ids(workspace)
            self.events.release_unselected(self.worker_id, selected_event_ids)
            selected_events = [
                event for event in reserved if event.event_id in selected_event_ids
            ]
            world_facts = (
                self.world.query(query_text, limit=30)
                if query_text
                else self.world.active_facts(limit=30)
            )
            contradictions = self.world.unresolved_contradictions(limit=10)
            budget = self.drives.behavior_budget(
                drives, affect, self.config.max_actions_per_cycle
            )
            context = {
                "cycle_id": cycle_id,
                "workspace": [item.to_dict() for item in workspace],
                "goals": [goal.to_dict() for goal in active_goals],
                "world_facts": world_facts,
                "memories": retrieved_memories,
                "memory_retrieval": memory_retrieval,
                "matching_skills": self.skills.match(query_text),
                "self_model": self.self_model.snapshot(),
                "relationships": self.relationships.snapshot(),
                "drives": drives.to_dict(),
                "affect": affect.to_dict(),
                "budget": budget,
                "unknowns": [
                    f"contradiction:{item['subject']}:{item['predicate']}"
                    for item in contradictions
                ],
                "available_tools": sorted(self.tools._tools),
                "paths": {
                    "home": str(self.config.home_path),
                    "sandbox": str(self.config.sandbox_path),
                    "outbox": str(self.config.outbox_path),
                },
            }
            if meaningful_input:
                plan = self.planner.plan(context)
                self.memory_attribution.record(
                    cycle_id, plan.memory_ids, memory_retrieval
                )
                plan.actions = plan.actions[: int(budget["max_actions"])]
                self._persist_plan_and_ack_events(
                    cycle_id, plan, [event.event_id for event in selected_events]
                )
                self.cognition.attach_plan(cycle_id, plan)
                plan_persisted = True
                outcomes = [*recovery_outcomes, *self._execute_plan(plan)]
                cognition_result = self.cognition.resolve_cycle(cycle_id, plan, outcomes)
                memory_resolution = self.memory_attribution.resolve(
                    cycle_id,
                    outcomes,
                    cognition_result,
                    frozen=memory_mode == "frozen",
                )
                if cognition_result is not None and memory_resolution is not None:
                    cognition_result["memory_attribution"] = memory_resolution
                plan_status = self._plan_status(plan.plan_id)
                episode_id = self.learning.record_episode(
                    cycle_id=cycle_id,
                    event_ids=[event.event_id for event in selected_events],
                    plan_id=plan.plan_id,
                    outcomes=outcomes,
                    prediction_errors=prediction_errors,
                    workspace=[item.to_dict() for item in workspace],
                )
                drives, affect, post_appraisal = self.drives.appraise(
                    selected_events,
                    outcomes=outcomes,
                    resource_snapshot=resource_snapshot,
                )
            else:
                plan = Plan(
                    rationale="Idle cycle: no external change, active goal, recovery, or prediction error.",
                    actions=[],
                )
                outcomes = []
                cognition_result = None
                plan_status = "IDLE"
                episode_id = None
                post_appraisal = {"idle": True}
            idle_cycles = int(self.db.get_runtime("idle_cycles", 0))
            if selected_events or plan.actions:
                idle_cycles = 0
            else:
                idle_cycles += 1
            self.db.set_runtime("idle_cycles", idle_cycles)
            sleep_result = None
            if idle_cycles >= self.config.sleep_after_idle_cycles:
                self.drives.decay(rate=0.05)
                sleep_result = self.sleep.run()
                self.db.set_runtime("idle_cycles", 0)
            metrics = {
                "sensors": sensor_summary,
                "autonomous_goal_ids": autonomous_goal_ids,
                "reserved_events": len(reserved),
                "selected_events": len(selected_events),
                "workspace_items": len(workspace),
                "memory_retrieval": {
                    "mode": memory_retrieval["mode"],
                    "selected_memory_ids": [
                        item["memory_id"] for item in memory_retrieval["selected"]
                    ],
                    "suppressed_memory_ids": [
                        item["memory_id"] for item in memory_retrieval["suppressed"]
                    ],
                },
                "actions": len(plan.actions),
                "outcomes": outcomes,
                "episode_id": episode_id,
                "plan_status": plan_status,
                "cognition": cognition_result,
                "appraisal": appraisal,
                "post_appraisal": post_appraisal,
                "sleep": sleep_result,
            }
            self.db.execute(
                "UPDATE cycles SET finished_at=?,status=?,workspace_json=?,metrics_json=? WHERE cycle_id=?",
                (
                    utc_now(),
                    "SUCCEEDED",
                    json.dumps(
                        [item.to_dict() for item in workspace],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                    cycle_id,
                ),
            )
            self.ledger.append(
                "cycle_completed", {"cycle_id": cycle_id, "metrics": metrics}
            )
            cycle_count = int(self.db.get_runtime("cycle_count", 0)) + 1
            self.db.set_runtime("cycle_count", cycle_count)
            if cycle_count % self.config.full_integrity_check_every == 0:
                self.verify_integrity(full=True)
            return {"status": "SUCCEEDED", "cycle_id": cycle_id, **metrics}
        except Exception as exc:
            if not plan_persisted:
                for event in reserved:
                    if event.event_id in selected_event_ids:
                        self.events.fail(
                            event.event_id, f"cycle failed: {type(exc).__name__}: {exc}"
                        )
            self.db.execute(
                "UPDATE cycles SET finished_at=?,status=?,error=? WHERE cycle_id=?",
                (utc_now(), "FAILED", f"{type(exc).__name__}: {exc}"[:4000], cycle_id),
            )
            self.ledger.append(
                "cycle_failed",
                {"cycle_id": cycle_id, "error": f"{type(exc).__name__}: {exc}"[:2000]},
            )
            raise

    def _resume_durable_actions(self) -> list[dict[str, Any]]:
        """Resume actions that were durably planned before an interrupted cycle.

        RUNNING actions are reconciled during startup. This method handles the
        remaining safe states: PLANNED actions are re-evaluated by policy and
        APPROVED actions consume their exact persisted approval. Waiting and
        unknown-side-effect actions always remain human-gated.
        """
        if self.config.max_actions_per_cycle <= 0:
            return []
        rows = self.db.query_all(
            """
            SELECT a.* FROM actions a
            JOIN plans p ON p.plan_id=a.plan_id
            WHERE a.status IN ('PLANNED','APPROVED')
              AND p.status NOT IN ('COMPLETED','FAILED')
            ORDER BY a.rowid ASC LIMIT ?
            """,
            (self.config.max_actions_per_cycle,),
        )
        outcomes: list[dict[str, Any]] = []
        touched_plans: set[str] = set()
        for row in rows:
            action = self._action_from_row(row)
            approval_id = str(row["approval_id"]) if row["approval_id"] else None
            outcome = self._execute_action(action, approval_id=approval_id)
            outcome["recovered"] = True
            outcomes.append(outcome)
            touched_plans.add(str(row["plan_id"]))
        for plan_id in touched_plans:
            self._refresh_plan_status(plan_id)
        return outcomes

    def _poll_due_sensors(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        summaries: list[dict[str, Any]] = []
        prediction_errors: list[dict[str, Any]] = []
        now = datetime.now(UTC)
        for sensor_config in self.config.sensors:
            if not sensor_config.enabled:
                continue
            state_row = self.db.query_one(
                "SELECT * FROM sensor_state WHERE sensor_name=?", (sensor_config.name,)
            )
            previous_state = json.loads(state_row["state_json"]) if state_row else {}
            last_polled = (
                datetime.fromisoformat(state_row["last_polled_at"])
                if state_row and state_row["last_polled_at"]
                else None
            )
            if (
                last_polled
                and (now - last_polled).total_seconds() < sensor_config.interval_seconds
            ):
                continue
            sensor = build_sensor(
                sensor_config.sensor_type,
                sensor_config.name,
                sensor_config.settings,
                self.config.home_path,
            )
            try:
                observations, next_state = sensor.poll(previous_state)
                for observation in observations:
                    self.events.add_observation(observation)
                    result = self.world.assimilate(observation)
                    prediction_errors.extend(result["prediction_errors"])
                    if observation.kind in {"service_health", "resource", "state"}:
                        due_at = (
                            datetime.now(UTC)
                            + timedelta(seconds=sensor_config.interval_seconds)
                        ).isoformat()
                        self.world.add_prediction(
                            observation.subject,
                            observation.predicate,
                            observation.value,
                            max(0.4, observation.confidence * 0.8),
                            source=f"persistence:{sensor_config.name}",
                            due_at=due_at,
                        )
                    sensor.ack(observation)
                with self.db.transaction() as connection:
                    connection.execute(
                        """
                        INSERT INTO sensor_state(sensor_name,state_json,last_polled_at,last_success_at,last_error)
                        VALUES (?,?,?,?,NULL)
                        ON CONFLICT(sensor_name) DO UPDATE SET state_json=excluded.state_json,
                            last_polled_at=excluded.last_polled_at,last_success_at=excluded.last_success_at,last_error=NULL
                        """,
                        (
                            sensor_config.name,
                            json.dumps(next_state, ensure_ascii=False, sort_keys=True),
                            utc_now(),
                            utc_now(),
                        ),
                    )
                summaries.append(
                    {
                        "sensor": sensor_config.name,
                        "observations": len(observations),
                        "status": "ok",
                    }
                )
            except Exception as exc:
                error_observation = self._sensor_error_observation(
                    sensor_config.name, exc
                )
                self.events.add_observation(error_observation)
                self.world.assimilate(error_observation)
                with self.db.transaction() as connection:
                    connection.execute(
                        """
                        INSERT INTO sensor_state(sensor_name,state_json,last_polled_at,last_success_at,last_error)
                        VALUES (?,?,?,NULL,?)
                        ON CONFLICT(sensor_name) DO UPDATE SET last_polled_at=excluded.last_polled_at,last_error=excluded.last_error
                        """,
                        (
                            sensor_config.name,
                            json.dumps(previous_state, ensure_ascii=False),
                            utc_now(),
                            f"{type(exc).__name__}: {exc}"[:4000],
                        ),
                    )
                summaries.append(
                    {
                        "sensor": sensor_config.name,
                        "observations": 1,
                        "status": "error",
                        "error": str(exc),
                    }
                )
        return summaries, prediction_errors

    def _sensor_error_observation(self, sensor_name: str, exc: Exception):
        from .schemas import Observation

        return Observation(
            source=sensor_name,
            kind="sensor_error",
            subject=sensor_name,
            predicate="poll_status",
            value="failed",
            metadata={
                "error": f"{type(exc).__name__}: {exc}"[:2000],
                "dedupe_key": f"sensor_error:{sensor_name}:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}:{type(exc).__name__}:{str(exc)[:200]}",
            },
        )

    def _persist_plan_and_ack_events(
        self, cycle_id: str, plan: Plan, event_ids: list[str]
    ) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO plans(plan_id,cycle_id,plan_json,status,created_at) VALUES (?,?,?,?,?)",
                (
                    plan.plan_id,
                    cycle_id,
                    json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True),
                    "PLANNED",
                    plan.created_at,
                ),
            )
            for action in plan.actions:
                side_effect_class = self.tools.get(action.tool).side_effect_class
                connection.execute(
                    """
                    INSERT INTO actions(
                        action_id,plan_id,goal_id,skill_id,tool,arguments_json,purpose,expected_result,
                        risk,acceptance_json,idempotency_key,status,side_effect_class
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        action.action_id,
                        plan.plan_id,
                        action.goal_id,
                        action.skill_id,
                        action.tool,
                        json.dumps(
                            action.arguments, ensure_ascii=False, sort_keys=True
                        ),
                        action.purpose,
                        action.expected_result,
                        action.risk.value,
                        json.dumps(action.acceptance, ensure_ascii=False),
                        action.idempotency_key,
                        action.status.value,
                        side_effect_class,
                    ),
                )
            self.events.mark_processed(event_ids, cycle_id, connection)
            self.ledger.append(
                "plan_persisted",
                {
                    "cycle_id": cycle_id,
                    "plan": plan.to_dict(),
                    "acknowledged_event_ids": event_ids,
                },
                connection,
            )

    def _execute_plan(self, plan: Plan) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        skill_results: dict[str, list[bool]] = {}
        for action in plan.actions:
            outcome = self._execute_action(action)
            outcomes.append(outcome)
            if action.skill_id:
                skill_results.setdefault(action.skill_id, []).append(
                    bool(outcome.get("success"))
                )
            if action.goal_id and outcome.get("success"):
                goal_row = self.db.query_one(
                    "SELECT progress FROM goals WHERE goal_id=?", (action.goal_id,)
                )
                if goal_row is not None:
                    self.goals.update_progress(
                        action.goal_id, min(1.0, float(goal_row["progress"]) + 0.25)
                    )
        for skill_id, results in skill_results.items():
            self.skills.record_use(skill_id, all(results))
        self._refresh_plan_status(plan.plan_id)
        return outcomes

    def _execute_action(
        self, action: ActionSpec, approval_id: str | None = None
    ) -> dict[str, Any]:
        prior = self.db.query_one(
            "SELECT result_json,action_id FROM actions WHERE idempotency_key=? AND status='SUCCEEDED' LIMIT 1",
            (action.idempotency_key,),
        )
        if prior and prior["action_id"] != action.action_id:
            reused = json.loads(prior["result_json"])
            self.db.execute(
                "UPDATE actions SET status='SUCCEEDED',finished_at=?,result_json=? WHERE action_id=?",
                (
                    utc_now(),
                    json.dumps(reused, ensure_ascii=False, sort_keys=True),
                    action.action_id,
                ),
            )
            return {
                "action_id": action.action_id,
                "success": True,
                "reused": True,
                "evaluation": reused.get("evaluation", {}),
            }
        try:
            self.policy.validate_arguments(action)
        except Exception as exc:
            reason = f"argument validation failed: {type(exc).__name__}: {exc}"
            self.db.execute(
                "UPDATE actions SET status='REJECTED',finished_at=?,error=? WHERE action_id=?",
                (utc_now(), reason[:4000], action.action_id),
            )
            self.ledger.append(
                "action_rejected", {"action_id": action.action_id, "reason": reason}
            )
            return {
                "action_id": action.action_id,
                "success": False,
                "status": "REJECTED",
                "reason": reason,
            }
        approval_valid = False
        if approval_id:
            approval_valid = self.approvals.validate_and_consume(action, approval_id)
        decision = self.policy.decide(action, approval_valid=approval_valid)
        if not decision.allowed:
            status = (
                ActionStatus.WAITING_APPROVAL
                if decision.requires_approval
                else ActionStatus.REJECTED
            )
            self.db.execute(
                "UPDATE actions SET status=?,error=? WHERE action_id=?",
                (status.value, decision.reason, action.action_id),
            )
            self.ledger.append(
                "action_blocked",
                {
                    "action_id": action.action_id,
                    "status": status.value,
                    "reason": decision.reason,
                },
            )
            return {
                "action_id": action.action_id,
                "success": False,
                "status": status.value,
                "reason": decision.reason,
            }
        with self.db.transaction() as connection:
            updated = connection.execute(
                "UPDATE actions SET status='RUNNING',started_at=?,error=NULL WHERE action_id=? AND status IN ('PLANNED','APPROVED','WAITING_APPROVAL')",
                (utc_now(), action.action_id),
            ).rowcount
            if not updated:
                row = connection.execute(
                    "SELECT status FROM actions WHERE action_id=?", (action.action_id,)
                ).fetchone()
                return {
                    "action_id": action.action_id,
                    "success": False,
                    "status": row["status"] if row else "MISSING",
                }
            self.ledger.append(
                "action_started",
                {"action_id": action.action_id, "tool": action.tool},
                connection,
            )
        result = self.tools.execute(action)
        evaluation = self.evaluator.evaluate(action, result)
        final_success = bool(evaluation["accepted"])
        final_status = ActionStatus.SUCCEEDED if final_success else ActionStatus.FAILED
        payload = {"result": result.to_dict(), "evaluation": evaluation}
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE actions SET status=?,finished_at=?,result_json=?,error=? WHERE action_id=?
                """,
                (
                    final_status.value,
                    result.finished_at,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    result.error
                    if result.error
                    else (None if final_success else "acceptance criteria failed"),
                    action.action_id,
                ),
            )
            evidence_id = self.ledger.append(
                "action_completed",
                {
                    "action_id": action.action_id,
                    "status": final_status.value,
                    "payload": payload,
                },
                connection,
            )
        self.self_model.record_action_outcome(action, result, evidence_id)
        return {
            "action_id": action.action_id,
            "success": final_success,
            "status": final_status.value,
            "evaluation": evaluation,
            "output": result.output,
            "error": result.error,
        }

    def resume_action(self, action_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM actions WHERE action_id=?", (action_id,))
        if row is None:
            raise KeyError(action_id)
        if row["status"] not in {
            ActionStatus.APPROVED.value,
            ActionStatus.WAITING_APPROVAL.value,
        }:
            raise ValueError(f"action cannot be resumed from {row['status']}")
        approval_id = row["approval_id"]
        if not approval_id:
            raise PermissionError("no approval attached")
        action = self._action_from_row(row)
        result = self._execute_action(action, approval_id=approval_id)
        self._refresh_plan_status(str(row["plan_id"]))
        return result

    def resolve_unknown_action(
        self, action_id: str, resolution: str, evidence: dict[str, Any]
    ) -> None:
        if resolution not in {"SUCCEEDED", "FAILED", "CANCELLED", "RETRY_SAFE"}:
            raise ValueError("invalid resolution")
        row = self.db.query_one("SELECT * FROM actions WHERE action_id=?", (action_id,))
        if row is None or row["status"] != ActionStatus.UNKNOWN_SIDE_EFFECT.value:
            raise ValueError("action is not awaiting unknown-side-effect resolution")
        if resolution == "RETRY_SAFE":
            status = ActionStatus.PLANNED.value
            finished_at = None
        else:
            status = resolution
            finished_at = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE actions SET status=?,finished_at=?,error=? WHERE action_id=?",
                (
                    status,
                    finished_at,
                    json.dumps(evidence, ensure_ascii=False),
                    action_id,
                ),
            )
            self.ledger.append(
                "unknown_action_resolved",
                {
                    "action_id": action_id,
                    "resolution": resolution,
                    "evidence": evidence,
                },
                connection,
            )
        self._refresh_plan_status(str(row["plan_id"]))

    def pause(self, reason: str) -> str:
        self.db.set_runtime("paused", True)
        self.db.set_runtime("pause_reason", reason)
        return self.ledger.append("runtime_paused", {"reason": reason})

    def resume(self, evidence: str) -> str:
        self.db.set_runtime("paused", False)
        self.db.set_runtime("pause_reason", "")
        return self.ledger.append("runtime_resumed", {"evidence": evidence})

    def kill(self, reason: str) -> str:
        self.db.set_runtime("kill_switch", True)
        self.db.set_runtime("kill_reason", reason)
        return self.ledger.append("kill_switch_activated", {"reason": reason})

    def reset_kill(self, evidence: str) -> str:
        self.db.set_runtime("kill_switch", False)
        self.db.set_runtime("kill_reason", "")
        return self.ledger.append("kill_switch_reset", {"evidence": evidence})

    def run_daemon(self, max_cycles: int | None = None) -> None:
        self._stop = False
        previous_handlers = {}
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[signal_name] = signal.signal(
                    signal_name, lambda *_: setattr(self, "_stop", True)
                )
            except (ValueError, OSError):
                pass
        cycles = 0
        daemon_lease = ProcessLease(self.config.home_path / "state" / "daemon.lock")
        try:
            with daemon_lease:
                while not self._stop and (max_cycles is None or cycles < max_cycles):
                    started = time.monotonic()
                    try:
                        self.run_cycle()
                    except RuntimeError as exc:
                        if "lease" not in str(exc).lower():
                            raise
                    cycles += 1
                    remaining = self.config.cycle_seconds - (time.monotonic() - started)
                    if remaining > 0:
                        end = time.monotonic() + remaining
                        while not self._stop and time.monotonic() < end:
                            time.sleep(min(0.5, end - time.monotonic()))
        finally:
            for name, handler in previous_handlers.items():
                try:
                    signal.signal(name, handler)
                except (ValueError, OSError):
                    pass

    def status(self) -> dict[str, Any]:
        latest_cycle = self.db.query_one(
            "SELECT * FROM cycles ORDER BY started_at DESC LIMIT 1"
        )
        pending_actions = self.db.query_all(
            "SELECT action_id,plan_id,tool,purpose,risk,status,approval_id FROM actions WHERE status IN ('WAITING_APPROVAL','APPROVED','UNKNOWN_SIDE_EFFECT') ORDER BY rowid DESC"
        )
        drives, affect = self.drives.load()
        return {
            "version": __version__,
            "home": str(self.config.home_path),
            "read_only": self.config.read_only,
            "paused": bool(self.db.get_runtime("paused", False)),
            "killed": bool(self.db.get_runtime("kill_switch", False)),
            "cycle_count": int(self.db.get_runtime("cycle_count", 0)),
            "event_counts": self.events.counts(),
            "latest_cycle": dict(latest_cycle) if latest_cycle else None,
            "pending_actions": [dict(row) for row in pending_actions],
            "drives": drives.to_dict(),
            "affect": affect.to_dict(),
            "self_model": self.self_model.snapshot(),
            "active_goals": [goal.to_dict() for goal in self.goals.active()],
            "active_skills": self.skills.active(),
            "growth_cycles": self.growth.summary(limit=20),
            "planner_provider": self.planner.provider_type,
            "planner_route": self.planner.route_summary(),
            "cognition": self.cognition.summary(limit=500),
            "temporal_world": self.temporal_world.summary(),
            "causal_memory": self.memories.memory_summary(),
            "memory_attribution": self.memory_attribution.summary(),
            "capabilities": self.capabilities.summary(),
            "read_only_plan_previews": self.read_only_plan_previews(),
            "scheduled_event_receipts": self.scheduled_event_receipts(),
            "external_handoff_receipts": self.external_handoff_receipts(),
            "approval_channel_receipts": self.approval_channel_receipts(),
            "provider_route_receipts": self.provider_route_receipts(),
            "read_only_execution_preflights": self.read_only_execution_preflights(),
            "read_only_execution_receipts": self.read_only_execution_receipts(),
            "read_only_result_projections": self.read_only_result_projections(),
            "read_only_projection_reviews": self.read_only_projection_reviews(),
            "next_focus": self.db.get_runtime("next_focus", []),
        }

    def verify_integrity(self, full: bool = True) -> dict[str, Any]:
        ledger_ok, ledger_details = self.ledger.verify()
        db_ok, db_details = self.db.integrity_check() if full else (True, "skipped")
        cognition_ok, cognition_details = self.cognition.integrity()
        memory_ok, memory_details = self.memories.memory_integrity()
        attribution_ok, attribution_details = self.memory_attribution.integrity()
        result = {
            "ok": (
                ledger_ok
                and db_ok
                and cognition_ok
                and memory_ok
                and attribution_ok
            ),
            "ledger": ledger_details,
            "database": db_details,
            "cognition": cognition_details,
            "causal_memory": memory_details,
            "memory_attribution": attribution_details,
        }
        if not result["ok"]:
            self.kill(f"integrity failure: {result}")
        return result

    def _resource_snapshot(self) -> dict[str, Any]:
        facts = self.world.active_facts(subject="host", limit=20)
        return {fact["predicate"]: fact["value"] for fact in facts}

    @staticmethod
    def _query_text(events: list[Event], goals: list[Goal]) -> str:
        parts = [
            json.dumps(event.payload, ensure_ascii=False, sort_keys=True)
            for event in events
        ]
        parts.extend(f"{goal.title} {goal.description}" for goal in goals)
        return " ".join(parts)[:30000]

    def _refresh_plan_status(self, plan_id: str) -> str:
        rows = self.db.query_all(
            "SELECT status FROM actions WHERE plan_id=?", (plan_id,)
        )
        statuses = {str(row["status"]) for row in rows}
        if not statuses or statuses <= {ActionStatus.SUCCEEDED.value}:
            status = "COMPLETED"
        elif statuses & {
            ActionStatus.WAITING_APPROVAL.value,
            ActionStatus.APPROVED.value,
        }:
            status = "WAITING_APPROVAL"
        elif statuses & {ActionStatus.UNKNOWN_SIDE_EFFECT.value}:
            status = "NEEDS_RECONCILIATION"
        elif statuses & {ActionStatus.FAILED.value, ActionStatus.REJECTED.value}:
            status = "FAILED"
        else:
            status = "RUNNING"
        self.db.execute(
            "UPDATE plans SET status=?,completed_at=? WHERE plan_id=?",
            (status, utc_now() if status in {"COMPLETED", "FAILED"} else None, plan_id),
        )
        return status

    def _plan_status(self, plan_id: str) -> str:
        row = self.db.query_one("SELECT status FROM plans WHERE plan_id=?", (plan_id,))
        return str(row["status"]) if row else "MISSING"

    @staticmethod
    def _action_from_row(row) -> ActionSpec:
        return ActionSpec(
            action_id=row["action_id"],
            tool=row["tool"],
            arguments=json.loads(row["arguments_json"]),
            purpose=row["purpose"],
            expected_result=row["expected_result"],
            risk=RiskLevel(row["risk"]),
            goal_id=row["goal_id"],
            skill_id=row["skill_id"],
            idempotency_key=row["idempotency_key"],
            acceptance=json.loads(row["acceptance_json"]),
            status=ActionStatus(row["status"]),
        )
