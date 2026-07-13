from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import hashlib
import importlib
import json
import mimetypes
import os
import signal
import shutil
import sqlite3
import tempfile
import time

from ._version import __version__
from .approval import ApprovalManager
from .agentic_harness import AgenticHarness
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
from .garbage_audit import GarbageAuditor
from .growth_cycle import GrowthCycleManager
from .learning import LearningSystem
from .memory_attribution import MemoryAttributionStore
from .lease import ProcessLease
from .longitudinal import LongitudinalEvaluator, LongitudinalProtocol, MeasurementPoint
from .offspring import OffspringRegistry
from .performance import PerformanceMeasurement, evaluate_performance_budget
from .planner import Planner
from .policy import PolicyEngine
from .relationships import RelationshipMemory
from .a2a_adapter import A2AAdapter, ArtifactEnvelope, TaskContract
from .mcp_adapter import McpCandidate, McpTrustGate
from .read_only_organs import ReadOnlyTaskReceipt, ReadOnlyTaskRequest
from .schemas import (
    ActionSpec,
    ActionStatus,
    CandidateStatus,
    Event,
    EvidenceKind,
    Goal,
    MemoryItem,
    Observation,
    Plan,
    RiskLevel,
    VerificationStatus,
    WorkspaceItem,
    digest_json,
    new_id,
    utc_now,
)
from .self_model import SelfModel
from .sensors import build_sensor
from .scheduler import EventScheduler, ScheduledEvent
from .skills import SkillLibrary
from .sandbox_adapter import SandboxAdapter, SandboxContract
from .sleep import SleepConsolidator
from .stores import EventStore, GoalStore, MemoryStore
from .temporal_world import TemporalCausalWorld
from .tools import ToolRegistry
from .wechat_adapter import WeChatW0W1Adapter
from .world import WorldModel


class LivingSystem:
    """Persistent observe-model-attend-plan-act-learn-consolidate runtime."""

    MAX_PERSISTED_WORKSPACE_PAYLOAD_CHARS = 1200

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
        self.agentic = AgenticHarness(self.db, self.ledger)
        self.offspring = OffspringRegistry(config, self.db, self.ledger)
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
        self._recover_cycles()
        self._recover_actions()
        self.events.recover_stale_reservations(
            (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        )

    def _recover_cycles(self) -> dict[str, int]:
        rows = self.db.query_all(
            "SELECT cycle_id FROM cycles WHERE status='RUNNING' ORDER BY started_at"
        )
        if not rows:
            return {"interrupted": 0}
        recovered_at = utc_now()
        cycle_ids = [str(row["cycle_id"]) for row in rows]
        with self.db.transaction() as connection:
            for cycle_id in cycle_ids:
                connection.execute(
                    """
                    UPDATE cycles
                    SET status='INTERRUPTED',finished_at=?,error=?
                    WHERE cycle_id=? AND status='RUNNING'
                    """,
                    (
                        recovered_at,
                        "runtime initialized after prior process ended mid-cycle",
                        cycle_id,
                    ),
                )
            self.ledger.append(
                "cycle_recovery",
                {
                    "interrupted": len(cycle_ids),
                    "cycle_ids": cycle_ids[:50],
                    "truncated": len(cycle_ids) > 50,
                },
                connection,
            )
        return {"interrupted": len(cycle_ids)}

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

    def voice_transcript_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("voice_transcript_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def notification_draft_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("notification_draft_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def screen_snapshot_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("screen_snapshot_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def browser_form_draft_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("browser_form_draft_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def download_quarantine_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("download_quarantine_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def document_ingress_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("document_ingress_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def provider_route_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("provider_route_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def skill_candidate_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("skill_candidate_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def skill_sandbox_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("skill_sandbox_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def sandbox_adapter_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("sandbox_adapter_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def offspring_birth_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.offspring.latest_receipts(limit=limit)

    def offspring_state_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.offspring.state_receipts(limit=limit)

    def offspring_retirement_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.offspring.retirement_receipts(limit=limit)

    def offspring_retirement_cleanup_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.offspring.retirement_cleanup_receipts(limit=limit)

    def offspring_budget_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.offspring.budget_receipts(limit=limit)

    def offspring_checkpoint_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.offspring.checkpoint_receipts(limit=limit)

    def offspring_mailbox_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.offspring.mailbox_receipts(limit=limit)

    def offspring_ecology_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.offspring.ecology_receipts(limit=limit)

    def agentic_task_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT graph_id,intent_id,status,updated_at
            FROM agentic_task_graphs
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(0, int(limit)),),
        )
        return [dict(row) for row in rows]

    def agentic_context_manifest_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.agentic.context_manifest.latest_receipts(limit=limit)

    def agentic_worker_profile_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.agentic.worker_registry.latest_receipts(limit=limit)

    def agentic_worker_lifecycle_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.worker_registry.lifecycle_receipts(limit=limit)

    def agentic_node_action_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT binding_id,graph_id,node_id,lease_id,plan_id,action_id,status,created_at
            FROM agentic_node_action_bindings
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(0, int(limit)),),
        )
        return [dict(row) for row in rows]

    def agentic_failure_attribution_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT attribution_id,graph_id,node_id,lease_id,failure_class,error_signature,created_at
            FROM agentic_failure_attributions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(0, int(limit)),),
        )
        return [dict(row) for row in rows]

    def agentic_acceptance_trace_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.acceptance_trace_receipts(limit=limit)

    def agentic_mailbox_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.agentic.mailbox_receipts(limit=limit)

    def agentic_repair_candidate_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.repair_candidate_receipts(limit=limit)

    def agentic_budget_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.agentic.budget_receipts(limit=limit)

    def agentic_checkpoint_resume_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.checkpoint_resume_receipts(limit=limit)

    def agentic_context_packet_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.context_packet_receipts(limit=limit)

    def agentic_context_epoch_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.context_epoch_receipts(limit=limit)

    def agentic_worker_lease_recovery_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.worker_lease_recovery_receipts(limit=limit)

    def agentic_worker_arbitration_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.worker_arbitration_receipts(limit=limit)

    def agentic_worker_trust_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.worker_registry.trust_receipts(limit=limit)

    def agentic_retry_gate_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.agentic.retry_gate_receipts(limit=limit)

    def agentic_replan_candidate_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.replan_candidate_receipts(limit=limit)

    def agentic_process_audit_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self.agentic.process_audit_receipts(limit=limit)

    def agentic_harness_epoch_audit_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("agentic_harness_epoch_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def agentic_benchmark_scorecard_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("agentic_benchmark_scorecard_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def single_software_convergence_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("single_software_convergence_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def bind_agentic_node_to_action(
        self,
        graph_id: str,
        node_id: str,
        *,
        lease_id: str,
        reason: str = "agentic harness policy-bound node admission",
    ) -> dict[str, Any]:
        graph = self.agentic.load_graph(graph_id)
        if node_id not in graph.nodes:
            raise KeyError(f"unknown task node: {node_id}")
        node = graph.nodes[node_id]
        if node.lease_id != lease_id:
            raise PermissionError("node action binding requires the active node lease")
        existing = self.db.query_one(
            """
            SELECT binding_id,plan_id,action_id,status,created_at
            FROM agentic_node_action_bindings
            WHERE graph_id=? AND node_id=? AND lease_id=?
            """,
            (graph_id, node_id, lease_id),
        )
        if existing is not None:
            return {
                "status": "ALREADY_BOUND",
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
                "binding_id": existing["binding_id"],
                "plan_id": existing["plan_id"],
                "action_id": existing["action_id"],
                "action_status": existing["status"],
                "created_at": existing["created_at"],
                "direct_tool_execution": False,
            }
        lease_row = self.db.query_one(
            """
            SELECT worker_id,status FROM agentic_node_leases
            WHERE graph_id=? AND node_id=? AND lease_id=?
            """,
            (graph_id, node_id, lease_id),
        )
        if lease_row is None or lease_row["status"] != "ACTIVE":
            raise PermissionError("active node lease is required")
        action = ActionSpec(
            tool="noop",
            arguments={
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
                "worker_id": lease_row["worker_id"],
                "mode": "agentic_node_shadow_binding",
                "node_acceptance": list(node.acceptance),
            },
            purpose=f"Bind agentic node '{node.title}' to canonical Action",
            expected_result="policy decision and evidence receipt are recorded",
            risk=node.risk,
            acceptance=["output ok is true"],
            idempotency_key=digest_json(
                {
                    "kind": "agentic_node_action_binding",
                    "graph_id": graph_id,
                    "node_id": node_id,
                    "lease_id": lease_id,
                }
            ),
        )
        decision = self.policy.decide(action, approval_valid=False)
        if decision.allowed:
            action.status = ActionStatus.PLANNED
            binding_status = "PLANNED"
        elif decision.requires_approval:
            action.status = ActionStatus.WAITING_APPROVAL
            binding_status = "WAITING_APPROVAL"
        else:
            action.status = ActionStatus.REJECTED
            binding_status = "REJECTED"
        plan = Plan(
            rationale=(
                "Policy-bound shadow plan for leased agentic task node; "
                "created by LivingSystem without executing the node tool"
            ),
            actions=[action],
            unknowns=[
                f"graph_id={graph_id}",
                f"node_id={node_id}",
                f"lease_id={lease_id}",
                f"worker_id={lease_row['worker_id']}",
                f"reason={reason}",
            ],
        )
        policy_payload = {
            "allowed": decision.allowed,
            "requires_approval": decision.requires_approval,
            "reason": decision.reason,
            "risk": self.policy.classify(action).value,
            "direct_tool_execution": False,
            "claim_ceiling": (
                "canonical Plan/Action binding only; no tool execution, "
                "node completion, or approval consumption"
            ),
        }
        binding = {
            "binding_id": new_id("binding"),
            "graph_id": graph_id,
            "node_id": node_id,
            "lease_id": lease_id,
            "plan_id": plan.plan_id,
            "action_id": action.action_id,
            "action_status": action.status.value,
            "policy_decision": policy_payload,
            "created_at": utc_now(),
            "direct_tool_execution": False,
        }
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO plans(plan_id,cycle_id,plan_json,status,created_at) VALUES (?,?,?,?,?)",
                (
                    plan.plan_id,
                    f"agentic:{graph_id}:{node_id}",
                    json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True),
                    "PLANNED",
                    plan.created_at,
                ),
            )
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
                    json.dumps(action.arguments, ensure_ascii=False, sort_keys=True),
                    action.purpose,
                    action.expected_result,
                    action.risk.value,
                    json.dumps(action.acceptance, ensure_ascii=False),
                    action.idempotency_key,
                    action.status.value,
                    self.tools.get(action.tool).side_effect_class,
                ),
            )
            connection.execute(
                """
                INSERT INTO agentic_node_action_bindings(
                    binding_id,graph_id,node_id,lease_id,plan_id,action_id,
                    policy_decision_json,status,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    binding["binding_id"],
                    graph_id,
                    node_id,
                    lease_id,
                    plan.plan_id,
                    action.action_id,
                    json.dumps(policy_payload, ensure_ascii=False, sort_keys=True),
                    binding_status,
                    binding["created_at"],
                ),
            )
            self.ledger.append("agentic_node_action_bound", binding, connection)
        return binding

    def execute_bound_agentic_node_action(self, binding_id: str) -> dict[str, Any]:
        binding_row = self.db.query_one(
            "SELECT * FROM agentic_node_action_bindings WHERE binding_id=?",
            (binding_id,),
        )
        if binding_row is None:
            raise KeyError(f"unknown agentic node action binding: {binding_id}")
        if binding_row["status"] != "PLANNED":
            raise PermissionError(
                f"agentic node action binding is not executable: {binding_row['status']}"
            )
        action_row = self.db.query_one(
            "SELECT * FROM actions WHERE action_id=?", (binding_row["action_id"],)
        )
        if action_row is None:
            raise KeyError(f"missing bound action: {binding_row['action_id']}")
        action = self._action_from_row(action_row)
        side_effect_class = self.tools.get(action.tool).side_effect_class
        decision = self.policy.decide(action, approval_valid=False)
        if action.risk is not RiskLevel.READ or side_effect_class != "none":
            raise PermissionError("only READ/no-side-effect bound node actions execute")
        if action.tool != "noop":
            raise PermissionError("only shadow noop bound node actions execute in V1")
        if not decision.allowed:
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    UPDATE agentic_node_action_bindings
                    SET status=?
                    WHERE binding_id=?
                    """,
                    (
                        "WAITING_APPROVAL"
                        if decision.requires_approval
                        else "REJECTED",
                        binding_id,
                    ),
                )
                self.ledger.append(
                    "agentic_node_action_execution_blocked",
                    {
                        "binding_id": binding_id,
                        "action_id": action.action_id,
                        "reason": decision.reason,
                    },
                    connection,
                )
            raise PermissionError(decision.reason)
        result = self._execute_action(action)
        if result.get("success") is True:
            completion = self.agentic.complete_node(
                str(binding_row["graph_id"]),
                str(binding_row["node_id"]),
                lease_id=str(binding_row["lease_id"]),
                result={
                    "action_id": action.action_id,
                    "binding_id": binding_id,
                    "execution_status": result.get("status"),
                    "output": result.get("output", {}),
                },
            )
            status = "SUCCEEDED"
        else:
            completion = self.agentic.fail_node(
                str(binding_row["graph_id"]),
                str(binding_row["node_id"]),
                lease_id=str(binding_row["lease_id"]),
                error=str(result.get("error") or result.get("reason") or "action failed"),
            )
            status = "FAILED"
        receipt = {
            "binding_id": binding_id,
            "graph_id": binding_row["graph_id"],
            "node_id": binding_row["node_id"],
            "lease_id": binding_row["lease_id"],
            "plan_id": binding_row["plan_id"],
            "action_id": action.action_id,
            "status": status,
            "action_result": result,
            "node_update": completion,
            "direct_tool_execution": True,
            "execution_scope": "READ/no-side-effect shadow node action",
            "claim_ceiling": (
                "preflighted READ/no-side-effect bound node action only; no external "
                "worker execution, write action, approval consumption, or live deployment"
            ),
        }
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE agentic_node_action_bindings
                SET status=?
                WHERE binding_id=?
                """,
                (status, binding_id),
            )
            self.ledger.append("agentic_node_action_executed", receipt, connection)
        return receipt

    def record_agentic_harness_epoch_audit(self, *, reason: str) -> dict[str, Any]:
        status = self.status()
        graph_count = len(self.agentic_task_receipts(limit=100))
        manifest_count = len(self.agentic_context_manifest_receipts(limit=100))
        worker_count = len(self.agentic_worker_profile_receipts(limit=100))
        binding_count = len(self.agentic_node_action_receipts(limit=100))
        failure_count = len(self.agentic_failure_attribution_receipts(limit=100))
        acceptance_trace_count = len(
            self.agentic_acceptance_trace_receipts(limit=100)
        )
        context_packet_count = len(self.agentic_context_packet_receipts(limit=100))
        context_epoch_count = len(self.agentic_context_epoch_receipts(limit=100))
        process_audit_count = len(self.agentic_process_audit_receipts(limit=100))
        mailbox_count = len(self.agentic_mailbox_receipts(limit=100))
        repair_candidate_count = len(
            self.agentic_repair_candidate_receipts(limit=100)
        )
        unsafe_executed = self.db.query_one(
            """
            SELECT COUNT(*) AS n
            FROM agentic_node_action_bindings b
            JOIN actions a ON a.action_id=b.action_id
            WHERE b.status='SUCCEEDED'
              AND (a.risk!='READ' OR a.side_effect_class!='none')
            """
        )
        high_risk_bindings = self.db.query_one(
            """
            SELECT COUNT(*) AS n
            FROM agentic_node_action_bindings b
            JOIN actions a ON a.action_id=b.action_id
            WHERE a.risk IN ('HIGH','IRREVERSIBLE')
            """
        )
        high_risk_succeeded = self.db.query_one(
            """
            SELECT COUNT(*) AS n
            FROM agentic_node_action_bindings b
            JOIN actions a ON a.action_id=b.action_id
            WHERE b.status='SUCCEEDED'
              AND a.risk IN ('HIGH','IRREVERSIBLE')
            """
        )
        event_types = {
            str(row["event_type"])
            for row in self.db.query_all(
                """
                SELECT DISTINCT event_type FROM evidence
                WHERE event_type LIKE 'agentic_%'
                ORDER BY event_type
                """
            )
        }
        invariants = {
            "single_runtime_authority": status.get("version") is not None
            and graph_count >= 1
            and manifest_count >= 1
            and worker_count >= 1,
            "no_external_worker_execution": True,
            "no_unsafe_bound_action_executed": int(unsafe_executed["n"] or 0) == 0
            if unsafe_executed
            else False,
            "owner_gate_preserved_for_high_risk": (
                int(high_risk_succeeded["n"] or 0) == 0
                and int(high_risk_bindings["n"] or 0) >= 0
            )
            if high_risk_succeeded and high_risk_bindings
            else False,
            "evidence_retained": {
                "agentic_task_graph_compiled",
                "agentic_task_node_leased",
            }.issubset(event_types),
            "acceptance_trace_retained": (
                acceptance_trace_count > 0
                and "agentic_task_node_acceptance_evaluated" in event_types
            ),
            "mailbox_handoff_retained": (
                mailbox_count >= 2
                and {
                    "agentic_task_envelope_exported",
                    "agentic_result_envelope_imported",
                }.issubset(event_types)
            ),
            "failed_nodes_have_repair_candidates": (
                failure_count == 0
                or (
                    repair_candidate_count > 0
                    and "agentic_repair_candidate_proposed" in event_types
                )
            ),
        }
        receipt = {
            "receipt_type": "AGENTIC_HARNESS_EPOCH_AUDIT",
            "status": "PASS" if all(invariants.values()) else "FAIL",
            "reason": reason,
            "receipt_counts": {
                "task_graph": graph_count,
                "context_manifest": manifest_count,
                "worker_profile": worker_count,
                "node_action_binding": binding_count,
                "failure_attribution": failure_count,
                "acceptance_trace": acceptance_trace_count,
                "context_packet": context_packet_count,
                "context_epoch": context_epoch_count,
                "process_audit": process_audit_count,
                "file_mailbox": mailbox_count,
                "repair_candidate": repair_candidate_count,
            },
            "invariants": invariants,
            "evidence_event_types": sorted(event_types),
            "allowed_conclusion": "AGENTIC_HARNESS_REPOSITORY_RUNTIME_ONLY",
            "claim_ceiling": (
                "agentic harness epoch audit only; no live deployment, external "
                "worker execution, production readiness, or unified release claim"
            ),
            "created_at": utc_now(),
        }
        current = self.agentic_harness_epoch_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "agentic_harness_epoch_audit_receipts", updated, connection
            )
            self.ledger.append("agentic_harness_epoch_audited", receipt, connection)
        return receipt

    def record_agentic_benchmark_scorecard(self, *, reason: str) -> dict[str, Any]:
        acceptance_traces = self.agentic_acceptance_trace_receipts(limit=100)
        failures = self.agentic_failure_attribution_receipts(limit=100)
        repair_candidates = self.agentic_repair_candidate_receipts(limit=100)
        budget_receipts = self.agentic_budget_receipts(limit=100)
        cases = len(acceptance_traces)
        succeeded = sum(
            1
            for item in acceptance_traces
            if item.get("acceptance_report", {}).get("passed") is True
        )
        acceptance_total = sum(
            int(item.get("acceptance_report", {}).get("check_count", 0))
            for item in acceptance_traces
        )
        acceptance_passed = 0
        evidence_required = 0
        evidence_present = 0
        for item in acceptance_traces:
            report = item.get("acceptance_report", {})
            checks = report.get("checks", []) if isinstance(report, dict) else []
            if not isinstance(checks, list):
                continue
            for check in checks:
                if not isinstance(check, dict):
                    continue
                if check.get("passed") is True:
                    acceptance_passed += 1
                if check.get("type") == "evidence_min":
                    evidence_required += 1
                    if check.get("passed") is True:
                        evidence_present += 1
        reserved_budgets = [
            item for item in budget_receipts if item.get("status") == "RESERVED"
        ]
        cost = sum(
            float(item.get("request", {}).get("cost_usd", 0.0))
            for item in reserved_budgets
            if isinstance(item.get("request"), dict)
        )
        latency_seconds = sum(
            float(item.get("request", {}).get("seconds", 0.0))
            for item in reserved_budgets
            if isinstance(item.get("request"), dict)
        )
        hidden_failures = max(0, len(failures) - len(repair_candidates))
        summary = {
            "cases": cases,
            "task_success_rate": succeeded / cases if cases else 0.0,
            "acceptance_coverage": acceptance_passed / acceptance_total
            if acceptance_total
            else 0.0,
            "evidence_coverage": evidence_present / evidence_required
            if evidence_required
            else 0.0,
            "hidden_failures": hidden_failures,
            "owner_correction_minutes": 0.0,
            "cost": cost,
            "latency_seconds": latency_seconds,
        }
        receipt = {
            "receipt_type": "AGENTIC_BENCHMARK_SCORECARD",
            "status": "RECORDED",
            "reason": reason,
            "summary": summary,
            "source_receipt_counts": {
                "acceptance_trace": len(acceptance_traces),
                "failure_attribution": len(failures),
                "repair_candidate": len(repair_candidates),
                "budget": len(budget_receipts),
            },
            "hidden_failure_policy": (
                "failure attributions without repair-candidate receipts count as hidden failures"
            ),
            "direct_execution": False,
            "claim_ceiling": (
                "benchmark scorecard over existing WLS receipts only; no external "
                "benchmark suite, live task quality, provider performance, or product "
                "readiness is proven"
            ),
            "created_at": utc_now(),
        }
        current = self.agentic_benchmark_scorecard_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "agentic_benchmark_scorecard_receipts", updated, connection
            )
            self.ledger.append("agentic_benchmark_scorecard_recorded", receipt, connection)
        return receipt

    def record_single_software_convergence_audit(
        self,
        *,
        reason: str,
        source_branch: str | None = None,
        required_live_tail_tests: list[str] | None = None,
    ) -> dict[str, Any]:
        required_tail = required_live_tail_tests or [
            "30-round campaign replay against disposable campaign home",
            "installed package compatibility smoke against disposable WLS home",
            "live configuration/database non-mutation check",
            "Owner Console/WeChat read-only projection smoke",
            "package build/install/uninstall rollback drill",
        ]
        capability_summary = self.capabilities.summary()
        receipt_counts = {
            "capability_epoch_audit": len(self.capability_epoch_audit_receipts(100)),
            "agentic_harness_epoch_audit": len(
                self.agentic_harness_epoch_audit_receipts(100)
            ),
            "agentic_task": len(self.agentic_task_receipts(100)),
            "agentic_worker_lifecycle": len(
                self.agentic_worker_lifecycle_receipts(100)
            ),
            "agentic_node_action": len(self.agentic_node_action_receipts(100)),
            "agentic_failure_attribution": len(
                self.agentic_failure_attribution_receipts(100)
            ),
            "agentic_acceptance_trace": len(
                self.agentic_acceptance_trace_receipts(100)
            ),
            "agentic_context_packet": len(self.agentic_context_packet_receipts(100)),
            "agentic_context_epoch": len(self.agentic_context_epoch_receipts(100)),
            "agentic_process_audit": len(self.agentic_process_audit_receipts(100)),
            "agentic_file_mailbox": len(self.agentic_mailbox_receipts(100)),
            "agentic_repair_candidate": len(
                self.agentic_repair_candidate_receipts(100)
            ),
            "agentic_budget": len(self.agentic_budget_receipts(100)),
            "agentic_checkpoint_resume": len(
                self.agentic_checkpoint_resume_receipts(100)
            ),
            "agentic_worker_lease_recovery": len(
                self.agentic_worker_lease_recovery_receipts(100)
            ),
            "agentic_worker_arbitration": len(
                self.agentic_worker_arbitration_receipts(100)
            ),
            "agentic_worker_trust": len(self.agentic_worker_trust_receipts(100)),
            "agentic_retry_gate": len(self.agentic_retry_gate_receipts(100)),
            "agentic_replan_candidate": len(
                self.agentic_replan_candidate_receipts(100)
            ),
            "agentic_benchmark_scorecard": len(
                self.agentic_benchmark_scorecard_receipts(100)
            ),
            "read_only_execution": len(self.read_only_execution_receipts(100)),
            "skill_candidate": len(self.skill_candidate_receipts(100)),
            "skill_sandbox": len(self.skill_sandbox_receipts(100)),
            "sandbox_adapter": len(self.sandbox_adapter_receipts(100)),
            "offspring_birth": len(self.offspring_birth_receipts(100)),
            "offspring_state": len(self.offspring_state_receipts(100)),
            "offspring_retirement": len(self.offspring_retirement_receipts(100)),
            "offspring_retirement_cleanup": len(
                self.offspring_retirement_cleanup_receipts(100)
            ),
            "offspring_budget": len(self.offspring_budget_receipts(100)),
            "offspring_checkpoint": len(self.offspring_checkpoint_receipts(100)),
            "offspring_mailbox": len(self.offspring_mailbox_receipts(100)),
            "offspring_ecology": len(self.offspring_ecology_receipts(100)),
            "packaging_layout": len(self.packaging_layout_receipts(100)),
            "delivery_readiness": len(self.delivery_readiness_receipts(100)),
            "installed_tail_check": len(self.installed_tail_check_receipts(100)),
            "operational_preflight": len(self.operational_preflight_receipts(100)),
            "delivery_handoff": len(self.delivery_handoff_receipts(100)),
            "release_state_audit": len(self.release_state_audit_receipts(100)),
            "ui_hardening_audit": len(self.ui_hardening_audit_receipts(100)),
            "ui_package_absorption": len(self.ui_package_absorption_receipts(100)),
            "final_route_absorption": len(self.final_route_absorption_receipts(100)),
            "source_artifact_inventory": len(self.source_artifact_inventory_receipts(100)),
            "delivery_self_check": len(self.delivery_self_check_receipts(100)),
            "delivery_gap_audit": len(self.delivery_gap_audit_receipts(100)),
        }
        branch_only_scaffolding = [
            {
                "item": "candidate branch / worktree",
                "status": "MUST_CONVERGE_BEFORE_FINAL",
                "reason": "final state is one WLS software, not a permanent branch stack",
            },
            {
                "item": "repository-only agentic harness receipts",
                "status": "NEEDS_PACKAGE_AND_DISPOSABLE_RUNTIME_PROOF",
                "reason": "repo tests do not prove installed software behavior",
            },
        ]
        integrated_software_organs = [
            item.get("capability_id")
            for item in capability_summary.get("capabilities", [])
            if isinstance(item, dict)
            and not item.get("declares_authority")
            and item.get("mode") in {"WORKBENCH", "SHADOW", "ACTIVE"}
        ]
        invariants = {
            "single_living_system_authority": not any(
                item.get("declares_authority")
                for item in capability_summary.get("capabilities", [])
                if isinstance(item, dict)
            ),
            "branch_is_not_final_state": True,
            "live_tail_tests_explicit": bool(required_tail),
            "no_live_deployment_claim": True,
            "candidate_only_until_packaged": True,
        }
        receipt = {
            "receipt_type": "SINGLE_SOFTWARE_CONVERGENCE_AUDIT",
            "status": "CONVERGENCE_INCOMPLETE",
            "reason": reason,
            "source_branch": source_branch,
            "target_state": "ONE_WLS_SOFTWARE",
            "integrated_software_organs": sorted(
                str(item) for item in integrated_software_organs if item
            ),
            "branch_only_scaffolding": branch_only_scaffolding,
            "required_live_tail_tests": required_tail,
            "receipt_counts": receipt_counts,
            "invariants": invariants,
            "blocking_gaps": [
                "candidate branch not yet packaged into unified WLS release",
                "live/disposable installed-instance tail tests not yet completed",
                "30-round baseline plus package 2.0 plus common agent organs not yet proven as one software",
            ],
            "allowed_conclusion": "CONVERGENCE_MAP_RECORDED_NOT_FINAL_SOFTWARE",
            "claim_ceiling": (
                "single-software convergence audit only; records remaining gaps "
                "and tail tests, not final packaging, deployment, or complete product state"
            ),
            "created_at": utc_now(),
        }
        current = self.single_software_convergence_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "single_software_convergence_receipts", updated, connection
            )
            self.ledger.append(
                "single_software_convergence_audited", receipt, connection
            )
        return receipt

    def draft_offspring_birth_contract(
        self,
        *,
        parent_head: str,
        mission: str,
        budget: dict[str, Any],
        inheritance_manifest: dict[str, Any],
        termination_conditions: list[str],
        parent_id: str = "WLS-PRIME",
        reason: str,
    ) -> dict[str, Any]:
        return self.offspring.draft_birth_contract(
            parent_head=parent_head,
            mission=mission,
            budget=budget,
            inheritance_manifest=inheritance_manifest,
            termination_conditions=termination_conditions,
            parent_id=parent_id,
            reason=reason,
        )

    def initialize_offspring_isolated_state(
        self, *, offspring_id: str, reason: str
    ) -> dict[str, Any]:
        return self.offspring.initialize_isolated_state(
            offspring_id=offspring_id, reason=reason
        )

    def retire_offspring_candidate(
        self,
        *,
        offspring_id: str,
        reason: str,
        outcome_summary: dict[str, Any],
        absorption_requested: bool = False,
    ) -> dict[str, Any]:
        return self.offspring.retire_candidate(
            offspring_id=offspring_id,
            reason=reason,
            outcome_summary=outcome_summary,
            absorption_requested=absorption_requested,
        )

    def verify_offspring_retirement_cleanup(
        self,
        *,
        offspring_id: str,
        reason: str,
        retention_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.offspring.verify_retirement_cleanup(
            offspring_id=offspring_id,
            reason=reason,
            retention_policy=retention_policy,
        )

    def reserve_offspring_budget(
        self,
        *,
        offspring_id: str,
        request: dict[str, int | float],
        reason: str,
        worker_id: str | None = None,
        node_id: str | None = None,
    ) -> dict[str, Any]:
        return self.offspring.reserve_budget(
            offspring_id=offspring_id,
            request=request,
            reason=reason,
            worker_id=worker_id,
            node_id=node_id,
        )

    def review_offspring_no_gain_stop(
        self,
        *,
        offspring_id: str,
        evidence_delta: int,
        improvement_delta: float,
        consecutive_no_evidence_rounds: int,
        consecutive_no_improvement_rounds: int,
        reason: str,
    ) -> dict[str, Any]:
        return self.offspring.review_no_gain_stop(
            offspring_id=offspring_id,
            evidence_delta=evidence_delta,
            improvement_delta=improvement_delta,
            consecutive_no_evidence_rounds=consecutive_no_evidence_rounds,
            consecutive_no_improvement_rounds=consecutive_no_improvement_rounds,
            reason=reason,
        )

    def record_offspring_checkpoint(
        self,
        *,
        offspring_id: str,
        reason: str,
        artifact_manifest: dict[str, Any] | None = None,
        parent_checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        return self.offspring.record_checkpoint(
            offspring_id=offspring_id,
            reason=reason,
            artifact_manifest=artifact_manifest,
            parent_checkpoint_id=parent_checkpoint_id,
        )

    def verify_offspring_checkpoint(
        self, *, offspring_id: str, checkpoint_id: str, reason: str
    ) -> dict[str, Any]:
        return self.offspring.verify_checkpoint(
            offspring_id=offspring_id, checkpoint_id=checkpoint_id, reason=reason
        )

    def fork_offspring_candidate(
        self,
        *,
        parent_offspring_id: str,
        parent_checkpoint_id: str,
        mutation_reason: str,
        reason: str,
    ) -> dict[str, Any]:
        return self.offspring.fork_candidate(
            parent_offspring_id=parent_offspring_id,
            parent_checkpoint_id=parent_checkpoint_id,
            mutation_reason=mutation_reason,
            reason=reason,
        )

    def draft_offspring_mailbox_envelope(
        self,
        *,
        offspring_id: str,
        task_id: str,
        attempt_id: str,
        kind: str,
        parts: list[dict[str, Any]],
        artifact_refs: list[dict[str, Any]],
        child_evidence: list[dict[str, Any]],
        sender: str,
        recipient: str,
        reason: str,
        schema_version: str = "offspring-mailbox-v1",
    ) -> dict[str, Any]:
        return self.offspring.draft_mailbox_envelope(
            offspring_id=offspring_id,
            task_id=task_id,
            attempt_id=attempt_id,
            kind=kind,
            parts=parts,
            artifact_refs=artifact_refs,
            child_evidence=child_evidence,
            sender=sender,
            recipient=recipient,
            reason=reason,
            schema_version=schema_version,
        )

    def receive_offspring_mailbox_envelope(
        self,
        *,
        envelope: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        return self.offspring.receive_mailbox_envelope(
            envelope=envelope,
            reason=reason,
        )

    def audit_offspring_ecology(
        self,
        *,
        population: list[dict[str, Any]],
        selection_policy: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        return self.offspring.audit_ecology(
            population=population,
            selection_policy=selection_policy,
            reason=reason,
        )

    def learning_epoch_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("learning_epoch_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def capability_epoch_audit_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("capability_epoch_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def paired_experiment_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("paired_experiment_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def holdout_epoch_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("holdout_epoch_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def promotion_bundle_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("promotion_bundle_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def transfer_audit_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("transfer_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def final_delivery_audit_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("final_delivery_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def commercial_readiness_audit_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("commercial_readiness_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return [dict(item) for item in receipts[:limit] if isinstance(item, dict)]

    def packaging_layout_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("packaging_layout_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def record_packaging_layout_audit(
        self,
        *,
        report: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("packaging layout audit reason is required")
        checks = report.get("checks", {})
        if not isinstance(checks, dict):
            checks = {}
        failed_checks = [
            str(key)
            for key, value in sorted(checks.items())
            if value is not True
        ]
        required_fields = {
            "canonical_project_root": ".",
            "canonical_package": "source/src/wls",
            "canonical_version_file": "source/src/wls/_version.py",
        }
        field_failures = [
            key for key, expected in required_fields.items() if report.get(key) != expected
        ]
        package_roots = report.get("package_roots", [])
        if not isinstance(package_roots, list):
            package_roots = []
        package_root_failures = (
            []
            if package_roots == ["source/src/wls"]
            else ["package_roots must equal ['source/src/wls']"]
        )
        project_manifests = report.get("project_manifests", [])
        if not isinstance(project_manifests, list):
            project_manifests = []
        manifest_failures = (
            []
            if project_manifests == ["pyproject.toml"]
            else ["project_manifests must equal ['pyproject.toml']"]
        )
        success_claim_failure = (
            []
            if report.get("success") is True
            else ["packaging report did not declare success"]
        )
        failure_groups = {
            "failed_checks": failed_checks,
            "field_failures": field_failures,
            "package_root_failures": package_root_failures,
            "manifest_failures": manifest_failures,
            "success_claim_failure": success_claim_failure,
        }
        passed = not any(failure_groups.values())
        receipt = {
            "receipt_type": "PACKAGING_LAYOUT_AUDIT",
            "status": "PACKAGING_LAYOUT_PASSED" if passed else "PACKAGING_LAYOUT_BLOCKED",
            "audit_id": new_id("packaging_layout_audit"),
            "reason": reason,
            "report": report,
            "failure_groups": failure_groups,
            "canonical_project_root": report.get("canonical_project_root"),
            "canonical_package": report.get("canonical_package"),
            "canonical_version_file": report.get("canonical_version_file"),
            "project_manifests": project_manifests,
            "package_roots": package_roots,
            "single_authority_preserved": passed,
            "install_executed": False,
            "package_build_executed": False,
            "live_install_modified": False,
            "claim_ceiling": (
                "repository packaging layout receipt only; it verifies source-tree "
                "layout and version authority from a report but does not build, "
                "install, deploy, or prove wheel/runtime compatibility"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.packaging_layout_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("packaging_layout_receipts", updated, connection)
            self.ledger.append("packaging_layout_audit_recorded", receipt, connection)
        return receipt

    def delivery_readiness_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("delivery_readiness_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def delivery_handoff_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("delivery_handoff_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def release_state_audit_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("release_state_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def ui_hardening_audit_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("ui_hardening_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def ui_package_absorption_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("ui_package_absorption_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def final_route_absorption_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("final_route_absorption_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def source_artifact_inventory_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("source_artifact_inventory_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def delivery_self_check_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("delivery_self_check_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def delivery_gap_audit_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("delivery_gap_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def installed_tail_check_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("installed_tail_check_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def operational_preflight_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("operational_preflight_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def performance_budget_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("performance_budget_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def retention_audit_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("retention_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return [dict(item) for item in receipts[:limit] if isinstance(item, dict)]

    def record_retention_audit(
        self,
        *,
        reason: str,
        max_items: int = 100,
        max_json_bytes: int = 1_000_000,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("retention audit reason is required")
        if max_items < 1:
            raise ValueError("max_items must be positive")
        if max_json_bytes < 1:
            raise ValueError("max_json_bytes must be positive")
        rows = self.db.query_all(
            """
            SELECT key,value_json,updated_at
            FROM runtime_state
            WHERE key LIKE ?
            ORDER BY key
            """,
            ("%_receipts",),
        )
        items: list[dict[str, Any]] = []
        oversized_keys: list[str] = []
        over_count_keys: list[str] = []
        invalid_keys: list[str] = []
        total_receipts = 0
        total_json_bytes = 0
        for row in rows:
            key = str(row["key"])
            raw = str(row["value_json"])
            size_bytes = len(raw.encode("utf-8"))
            total_json_bytes += size_bytes
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                invalid_keys.append(key)
                items.append(
                    {
                        "key": key,
                        "status": "INVALID_JSON",
                        "count": 0,
                        "json_bytes": size_bytes,
                        "updated_at": row["updated_at"],
                    }
                )
                continue
            if not isinstance(value, list):
                invalid_keys.append(key)
                items.append(
                    {
                        "key": key,
                        "status": "NOT_A_LIST",
                        "count": 0,
                        "json_bytes": size_bytes,
                        "updated_at": row["updated_at"],
                    }
                )
                continue
            count = len(value)
            total_receipts += count
            over_count = count > max_items
            oversized = size_bytes > max_json_bytes
            if over_count:
                over_count_keys.append(key)
            if oversized:
                oversized_keys.append(key)
            items.append(
                {
                    "key": key,
                    "status": "OVER_LIMIT"
                    if over_count or oversized
                    else "WITHIN_LIMIT",
                    "count": count,
                    "max_items": max_items,
                    "json_bytes": size_bytes,
                    "max_json_bytes": max_json_bytes,
                    "updated_at": row["updated_at"],
                    "over_item_limit": over_count,
                    "over_size_limit": oversized,
                }
            )
        status = "RETENTION_AUDIT_PASSED"
        if invalid_keys:
            status = "RETENTION_AUDIT_INVALID_RUNTIME_STATE"
        elif over_count_keys or oversized_keys:
            status = "RETENTION_AUDIT_REVIEW_REQUIRED"
        receipt = {
            "receipt_type": "RETENTION_AUDIT",
            "audit_id": new_id("retention_audit"),
            "status": status,
            "reason": reason,
            "key_count": len(items),
            "total_receipts": total_receipts,
            "total_json_bytes": total_json_bytes,
            "max_items": max_items,
            "max_json_bytes": max_json_bytes,
            "over_count_keys": over_count_keys,
            "oversized_keys": oversized_keys,
            "invalid_keys": invalid_keys,
            "items": items,
            "cleanup_executed": False,
            "owner_review_required": status != "RETENTION_AUDIT_PASSED",
            "claim_ceiling": (
                "read-only runtime receipt retention audit only; no receipts, "
                "evidence rows, or projection payloads were deleted or compacted"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.retention_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("retention_audit_receipts", updated, connection)
            self.db.set_runtime("retention_audit_last", receipt, connection)
            self.ledger.append("retention_audit_recorded", receipt, connection)
        return receipt

    def record_performance_budget_audit(
        self,
        *,
        measurements: list[PerformanceMeasurement],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("performance budget audit reason is required")
        evaluation = evaluate_performance_budget(measurements)
        receipt = {
            "receipt_type": "PERFORMANCE_BUDGET_AUDIT",
            "audit_id": new_id("performance_budget_audit"),
            "status": "PERFORMANCE_BUDGET_PASSED"
            if evaluation["passed"]
            else "PERFORMANCE_BUDGET_FAILED",
            "reason": reason,
            "evaluation": evaluation,
            "measurement_count": len(measurements),
            "live_install_modified": False,
            "cleanup_executed": False,
            "daemon_started": False,
            "claim_ceiling": (
                "performance budget receipt only; it measures bounded local "
                "operations and does not prove long-run uptime or business task quality"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.performance_budget_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("performance_budget_receipts", updated, connection)
            self.db.set_runtime("performance_budget_last", receipt, connection)
            self.ledger.append("performance_budget_audit_recorded", receipt, connection)
        return receipt

    def longitudinal_protocol_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("longitudinal_protocol_receipts", [])
        if not isinstance(receipts, list):
            return []
        return [dict(item) for item in receipts[:limit] if isinstance(item, dict)]

    def longitudinal_measurement_receipts(
        self, protocol_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("longitudinal_measurement_receipts", [])
        if not isinstance(receipts, list):
            return []
        items = [dict(item) for item in receipts if isinstance(item, dict)]
        if protocol_id:
            items = [item for item in items if item.get("protocol_id") == protocol_id]
        return items[: max(0, int(limit))]

    def longitudinal_report_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("longitudinal_report_receipts", [])
        if not isinstance(receipts, list):
            return []
        return [dict(item) for item in receipts[:limit] if isinstance(item, dict)]

    def start_longitudinal_protocol(
        self,
        *,
        host_id: str,
        baseline_commit: str,
        duration_days: int = 30,
        reason: str,
    ) -> dict[str, Any]:
        if not host_id.strip():
            raise ValueError("host_id is required")
        if not baseline_commit.strip():
            raise ValueError("baseline_commit is required")
        if not reason.strip():
            raise ValueError("longitudinal protocol reason is required")
        if len(host_id) > 160:
            raise ValueError("host_id is too long")
        if len(baseline_commit) > 160:
            raise ValueError("baseline_commit is too long")
        evaluator = LongitudinalEvaluator()
        config_payload = json.loads(
            json.dumps(asdict(self.config), ensure_ascii=False, default=str)
        )
        protocol = evaluator.start_protocol(
            host_id.strip(),
            baseline_commit.strip(),
            config_payload,
            duration_days=max(1, min(3650, int(duration_days))),
        )
        receipt = {
            "receipt_type": "LONGITUDINAL_PROTOCOL_STARTED",
            **protocol.to_dict(),
            "reason": reason,
            "daemon_started": False,
            "cleanup_executed": False,
            "claim_ceiling": (
                "protocol receipt only; no business value claim is made until "
                "owner-task measurements and reports are recorded"
            ),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.longitudinal_protocol_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("longitudinal_protocol_receipts", updated, connection)
            self.db.set_runtime("longitudinal_protocol_last", receipt, connection)
            self.ledger.append("longitudinal_protocol_started", receipt, connection)
        return receipt

    def record_longitudinal_measurement(
        self,
        *,
        protocol_id: str,
        task_class: str,
        success: bool,
        corrections: int = 0,
        cost: float = 0.0,
        latency_seconds: float = 0.0,
        memory_benefit: bool = False,
        skill_reuse: bool = False,
        task_reference: str = "",
        owner_review: str = "",
        reason: str,
    ) -> dict[str, Any]:
        if not protocol_id.strip():
            raise ValueError("protocol_id is required")
        if not task_class.strip():
            raise ValueError("task_class is required")
        if not reason.strip():
            raise ValueError("longitudinal measurement reason is required")
        protocol = self._longitudinal_protocol_from_receipt(protocol_id)
        evaluator = LongitudinalEvaluator()
        point = evaluator.record_measurement(
            protocol,
            task_class.strip(),
            bool(success),
            corrections=max(0, int(corrections)),
            cost=max(0.0, float(cost)),
            latency=max(0.0, float(latency_seconds)),
            memory_benefit=bool(memory_benefit),
            skill_reuse=bool(skill_reuse),
        )
        bounded_task_reference = self._bounded_text(task_reference, 500)
        bounded_owner_review = self._bounded_text(owner_review, 1000)
        owner_evidence_complete = bool(
            bounded_task_reference and bounded_owner_review
        )
        receipt = {
            "receipt_type": "LONGITUDINAL_MEASUREMENT_RECORDED",
            **point.to_dict(),
            "task_reference": bounded_task_reference,
            "owner_review": bounded_owner_review,
            "owner_evidence_complete": owner_evidence_complete,
            "evidence_quality": "QUALIFIED_OWNER_TASK"
            if owner_evidence_complete
            else "PARTIAL_OWNER_TASK",
            "reason": reason,
            "daemon_started": False,
            "cleanup_executed": False,
            "claim_ceiling": (
                "single owner-task measurement only; trends require repeated "
                "measurements and a compiled report"
            ),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.longitudinal_measurement_receipts(limit=500)
        updated = [receipt, *current][:500]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "longitudinal_measurement_receipts", updated, connection
            )
            self.db.set_runtime("longitudinal_measurement_last", receipt, connection)
            self.ledger.append("longitudinal_measurement_recorded", receipt, connection)
        return receipt

    def compile_longitudinal_report(
        self,
        *,
        protocol_id: str,
        baseline_success_rate: float = 1.0,
        max_regression: float = 0.1,
        reason: str,
    ) -> dict[str, Any]:
        if not protocol_id.strip():
            raise ValueError("protocol_id is required")
        if not reason.strip():
            raise ValueError("longitudinal report reason is required")
        protocol = self._longitudinal_protocol_from_receipt(protocol_id)
        measurement_receipts = self.longitudinal_measurement_receipts(
            protocol_id=protocol_id, limit=500
        )
        measurements = [
            self._longitudinal_measurement_from_receipt(item)
            for item in measurement_receipts
        ]
        qualified_measurement_count = sum(
            1 for item in measurement_receipts if self._owner_task_evidence_complete(item)
        )
        unqualified_measurement_count = max(
            0, len(measurement_receipts) - qualified_measurement_count
        )
        evaluator = LongitudinalEvaluator()
        report = evaluator.compile_report(
            protocol,
            measurements,
            baseline_success_rate=max(0.0, min(1.0, float(baseline_success_rate))),
            max_regression=max(0.0, min(1.0, float(max_regression))),
            claim_ceiling=(
                "claims limited to the recorded owner-task workload for this "
                "protocol; not a general autonomy or commercial-product proof"
            ),
        )
        receipt = {
            "receipt_type": "LONGITUDINAL_REPORT_COMPILED",
            **report.to_dict(),
            "reason": reason,
            "baseline_success_rate": max(0.0, min(1.0, float(baseline_success_rate))),
            "max_regression": max(0.0, min(1.0, float(max_regression))),
            "qualified_measurement_count": qualified_measurement_count,
            "unqualified_measurement_count": unqualified_measurement_count,
            "status": "LONGITUDINAL_REPORT_PASSED"
            if not report.regressions and measurements and qualified_measurement_count > 0
            else "LONGITUDINAL_REPORT_NEEDS_MORE_EVIDENCE"
            if not measurements or qualified_measurement_count == 0
            else "LONGITUDINAL_REPORT_REGRESSION_FOUND",
            "daemon_started": False,
            "cleanup_executed": False,
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.longitudinal_report_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("longitudinal_report_receipts", updated, connection)
            self.db.set_runtime("longitudinal_report_last", receipt, connection)
            self.ledger.append("longitudinal_report_compiled", receipt, connection)
        return receipt

    def _longitudinal_protocol_from_receipt(
        self, protocol_id: str
    ) -> LongitudinalProtocol:
        for item in self.longitudinal_protocol_receipts(limit=100):
            if item.get("protocol_id") == protocol_id:
                return LongitudinalProtocol(
                    protocol_id=str(item["protocol_id"]),
                    host_id=str(item["host_id"]),
                    baseline_commit=str(item["baseline_commit"]),
                    config_digest=str(item["config_digest"]),
                    started_at=str(item["started_at"]),
                    duration_days=int(item.get("duration_days", 30)),
                    measurement_interval_hours=int(
                        item.get("measurement_interval_hours", 24)
                    ),
                    frozen_baseline=bool(item.get("frozen_baseline", True)),
                )
        raise KeyError(f"longitudinal protocol not found: {protocol_id}")

    @staticmethod
    def _longitudinal_measurement_from_receipt(
        item: dict[str, Any]
    ) -> MeasurementPoint:
        return MeasurementPoint(
            point_id=str(item["point_id"]),
            protocol_id=str(item["protocol_id"]),
            task_class=str(item["task_class"]),
            success=LivingSystem._truthy(item.get("success", False)),
            correction_count=int(item.get("correction_count", 0)),
            cost=float(item.get("cost", 0.0)),
            latency_seconds=float(item.get("latency_seconds", 0.0)),
            memory_benefit=LivingSystem._truthy(item.get("memory_benefit", False)),
            skill_reuse=LivingSystem._truthy(item.get("skill_reuse", False)),
            recorded_at=str(item.get("recorded_at", utc_now())),
        )

    @staticmethod
    def _bounded_text(value: str, limit: int) -> str:
        text = str(value or "").strip()
        return text[:limit]

    @staticmethod
    def _owner_task_evidence_complete(item: dict[str, Any]) -> bool:
        if "owner_evidence_complete" in item:
            return LivingSystem._truthy(item.get("owner_evidence_complete"))
        return bool(
            str(item.get("task_reference", "")).strip()
            and str(item.get("owner_review", "")).strip()
        )

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "y", "passed"}

    def bounded_soak_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("bounded_soak_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def run_bounded_soak(
        self,
        *,
        cycles: int = 1,
        reason: str,
        max_cycle_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("bounded soak reason is required")
        if cycles < 0 or cycles > 20:
            raise ValueError("bounded soak cycles must be within 0..20")
        cycle_budget = (
            float(max_cycle_seconds)
            if max_cycle_seconds is not None
            else float(self.config.daemon_max_cycle_seconds)
        )
        started_at = utc_now()
        before_health = self.health_snapshot()
        before_db_bytes = int(before_health["database"]["bytes"])
        before_evidence = int(
            self.db.query_one("SELECT COUNT(*) AS n FROM evidence")["n"]  # type: ignore[index]
        )
        before_cycle_count = int(self.db.get_runtime("cycle_count", 0))
        cycle_results: list[dict[str, Any]] = []
        failures: list[str] = []
        for _ in range(cycles):
            started = time.monotonic()
            try:
                result = self.run_cycle()
                elapsed = time.monotonic() - started
                status = str(result.get("status", "UNKNOWN"))
                if status != "SUCCEEDED":
                    failures.append(f"cycle_status_{status.lower()}")
                if elapsed > cycle_budget:
                    failures.append("cycle_time_budget_exceeded")
                phase_timings = result.get("phase_timings", [])
                cycle_results.append(
                    {
                        "cycle_id": result.get("cycle_id"),
                        "status": status,
                        "elapsed_seconds": round(elapsed, 4),
                        "budget_seconds": cycle_budget,
                        "sensor_summaries": result.get("sensors", [])
                        if isinstance(result.get("sensors", []), list)
                        else [],
                        "planning_timings": result.get("planning_timings", [])
                        if isinstance(result.get("planning_timings", []), list)
                        else [],
                        "event_goal_timings": result.get("event_goal_timings", [])
                        if isinstance(result.get("event_goal_timings", []), list)
                        else [],
                        "cognition_learning_timings": result.get(
                            "cognition_learning_timings", []
                        )
                        if isinstance(
                            result.get("cognition_learning_timings", []), list
                        )
                        else [],
                        "phase_timings": phase_timings
                        if isinstance(phase_timings, list)
                        else [],
                        "slowest_phases": self._slowest_cycle_phases(phase_timings),
                    }
                )
            except Exception as exc:
                elapsed = time.monotonic() - started
                failures.append(type(exc).__name__)
                cycle_results.append(
                    {
                        "cycle_id": None,
                        "status": "FAILED",
                        "elapsed_seconds": round(elapsed, 4),
                        "budget_seconds": cycle_budget,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                break
        after_health = self.health_snapshot()
        after_db_bytes = int(after_health["database"]["bytes"])
        after_evidence = int(
            self.db.query_one("SELECT COUNT(*) AS n FROM evidence")["n"]  # type: ignore[index]
        )
        after_cycle_count = int(self.db.get_runtime("cycle_count", 0))
        if after_health["status"] == "BLOCKED":
            failures.append("post_soak_health_blocked")
        if after_db_bytes > int(self.config.daemon_max_database_bytes):
            failures.append("database_size_over_daemon_limit")
        receipt = {
            "receipt_type": "BOUNDED_SOAK_AUDIT",
            "audit_id": new_id("bounded_soak_audit"),
            "status": "BOUNDED_SOAK_PASSED" if not failures else "BOUNDED_SOAK_FAILED",
            "reason": reason,
            "requested_cycles": cycles,
            "completed_cycles": sum(
                1 for item in cycle_results if item.get("status") == "SUCCEEDED"
            ),
            "cycle_results": cycle_results,
            "before": {
                "health_status": before_health["status"],
                "db_bytes": before_db_bytes,
                "evidence_records": before_evidence,
                "cycle_count": before_cycle_count,
            },
            "after": {
                "health_status": after_health["status"],
                "db_bytes": after_db_bytes,
                "evidence_records": after_evidence,
                "cycle_count": after_cycle_count,
            },
            "growth": {
                "db_bytes": after_db_bytes - before_db_bytes,
                "evidence_records": after_evidence - before_evidence,
                "cycle_count": after_cycle_count - before_cycle_count,
            },
            "failures": failures,
            "daemon_started": False,
            "cleanup_executed": False,
            "claim_ceiling": (
                "bounded soak receipt only; it runs a limited number of canonical "
                "cycles and does not prove long-run uptime or business task quality"
            ),
            "started_at": started_at,
            "finished_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.bounded_soak_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("bounded_soak_receipts", updated, connection)
            self.db.set_runtime("bounded_soak_last", receipt, connection)
            self.ledger.append("bounded_soak_audit_recorded", receipt, connection)
        return receipt

    def upgrade_drill_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("upgrade_drill_receipts", [])
        if not isinstance(receipts, list):
            return []
        return [dict(item) for item in receipts[:limit] if isinstance(item, dict)]

    def run_upgrade_drill(
        self,
        *,
        wheel_path: str | Path,
        reason: str = "owner requested upgrade rollback drill",
        disposable_clone: bool = False,
    ) -> dict[str, Any]:
        wheel = Path(wheel_path).expanduser().resolve()
        if not wheel.is_file():
            raise FileNotFoundError(f"wheel not found: {wheel}")
        if wheel.suffix.lower() != ".whl":
            raise ValueError("upgrade drill requires a .whl file")
        drill_id = new_id("upgrade_drill")
        started_at = utc_now()
        backup_dir = self.config.home_path / "backups" / drill_id
        backup_dir.mkdir(parents=True, exist_ok=False)
        backup_db = backup_dir / "wls.db"
        restore_check_db = backup_dir / "restore_check.db"
        before_health = self.health_snapshot()
        backup_started = time.monotonic()
        source = self.db.connect()
        destination = sqlite3.connect(backup_db)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        backup_seconds = round(time.monotonic() - backup_started, 4)
        shutil.copy2(backup_db, restore_check_db)
        restore_connection = sqlite3.connect(restore_check_db)
        try:
            integrity = str(
                restore_connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
            foreign_rows = restore_connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            restore_ok = integrity.lower() == "ok" and not foreign_rows
        finally:
            restore_connection.close()
        after_health = self.health_snapshot()
        clone_rollback = (
            self._run_disposable_clone_rollback_drill(
                drill_id=drill_id,
                wheel=wheel,
                backup_db=backup_db,
            )
            if disposable_clone
            else None
        )
        clone_ok = (
            True
            if clone_rollback is None
            else clone_rollback.get("passed") is True
        )
        receipt = {
            "receipt_type": "UPGRADE_ROLLBACK_DRILL",
            "drill_id": drill_id,
            "status": "UPGRADE_DRILL_PASSED"
            if restore_ok and clone_ok
            else "UPGRADE_DRILL_FAILED",
            "reason": reason,
            "wheel": {
                "path": str(wheel),
                "bytes": wheel.stat().st_size,
                "sha256": self._file_sha256(wheel),
            },
            "backup": {
                "path": str(backup_db),
                "bytes": backup_db.stat().st_size,
                "sha256": self._file_sha256(backup_db),
                "seconds": backup_seconds,
            },
            "restore_check": {
                "path": str(restore_check_db),
                "integrity_check": integrity,
                "foreign_key_violations": len(foreign_rows),
                "passed": restore_ok,
            },
            "before": {
                "health_status": before_health["status"],
                "db_bytes": before_health["database"]["bytes"],
                "cycle_count": before_health["cycle_count"],
            },
            "after": {
                "health_status": after_health["status"],
                "db_bytes": after_health["database"]["bytes"],
                "cycle_count": after_health["cycle_count"],
            },
            "live_install_modified": False,
            "live_database_restored": False,
            "disposable_clone_executed": bool(disposable_clone),
            "disposable_clone_rollback": clone_rollback,
            "cleanup_executed": False,
            "claim_ceiling": (
                "backup, restore-check, and optional disposable clone rollback drill "
                "only; it proves the current DB can be backed up, opened as a "
                "restored copy, and rolled back inside a temporary clone when "
                "requested, not that a live package rollback was executed"
            ),
            "started_at": started_at,
            "finished_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.upgrade_drill_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("upgrade_drill_receipts", updated, connection)
            self.db.set_runtime("upgrade_drill_last", receipt, connection)
            self.ledger.append("upgrade_rollback_drill_recorded", receipt, connection)
        return receipt

    def _run_disposable_clone_rollback_drill(
        self,
        *,
        drill_id: str,
        wheel: Path,
        backup_db: Path,
    ) -> dict[str, Any]:
        clone_home_text = ""
        with tempfile.TemporaryDirectory(prefix=f"wls-upgrade-clone-{drill_id}-") as temp:
            clone_home = Path(temp) / "home"
            clone_home_text = str(clone_home)
            clone_config = replace(self.config, home=str(clone_home), read_only=True)
            clone_config.ensure_directories()
            shutil.copy2(backup_db, clone_config.db_path)
            backup_sha256 = self._file_sha256(backup_db)
            clone_before_sha256 = self._file_sha256(clone_config.db_path)
            clone_db = Database(clone_config.db_path)
            try:
                before_health = self.build_health_snapshot(clone_config, clone_db)
                marker = {
                    "drill_id": drill_id,
                    "wheel_path": str(wheel),
                    "wheel_sha256": self._file_sha256(wheel),
                    "simulated_at": utc_now(),
                    "scope": "disposable_clone_only",
                }
                clone_db.set_runtime("disposable_upgrade_marker", marker)
                marker_after_upgrade = clone_db.get_runtime(
                    "disposable_upgrade_marker", None
                )
                after_upgrade_health = self.build_health_snapshot(clone_config, clone_db)
            finally:
                clone_db.close_all()
            clone_upgraded_sha256 = self._file_sha256(clone_config.db_path)
            shutil.copy2(backup_db, clone_config.db_path)
            clone_rollback_sha256 = self._file_sha256(clone_config.db_path)
            rollback_db = Database(clone_config.db_path)
            try:
                after_rollback_health = self.build_health_snapshot(
                    clone_config, rollback_db
                )
                marker_after_rollback = rollback_db.get_runtime(
                    "disposable_upgrade_marker", None
                )
                integrity_ok, integrity_detail = rollback_db.integrity_check()
            finally:
                rollback_db.close_all()
            marker_removed = marker_after_rollback is None
            marker_written = isinstance(marker_after_upgrade, dict)
            rollback_matches_backup = clone_rollback_sha256 == backup_sha256
            passed = bool(
                integrity_ok
                and before_health["status"] != "BLOCKED"
                and after_upgrade_health["status"] != "BLOCKED"
                and after_rollback_health["status"] != "BLOCKED"
                and clone_before_sha256 == backup_sha256
                and marker_written
                and rollback_matches_backup
                and marker_removed
            )
            result = {
                "passed": passed,
                "clone_home": clone_home_text,
                "clone_home_removed": False,
                "backup_sha256": backup_sha256,
                "clone_before_sha256": clone_before_sha256,
                "clone_upgraded_sha256": clone_upgraded_sha256,
                "clone_rollback_sha256": clone_rollback_sha256,
                "rollback_matches_backup": rollback_matches_backup,
                "marker_written_before_rollback": marker_written,
                "marker_removed_after_rollback": marker_removed,
                "integrity_check": integrity_detail,
                "integrity_passed": integrity_ok,
                "before_health_status": before_health["status"],
                "after_upgrade_health_status": after_upgrade_health["status"],
                "after_rollback_health_status": after_rollback_health["status"],
                "live_install_modified": False,
                "live_database_restored": False,
                "claim_ceiling": (
                    "disposable clone rollback drill only; simulated upgrade marker "
                    "is written to a temporary clone and removed by restoring the "
                    "clone DB from backup"
                ),
            }
        result["clone_home_removed"] = not Path(clone_home_text).exists()
        return result

    @staticmethod
    def _slowest_cycle_phases(phases: object, limit: int = 5) -> list[dict[str, Any]]:
        if not isinstance(phases, list):
            return []
        normalized: list[dict[str, Any]] = []
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            try:
                elapsed = float(phase.get("elapsed_seconds", 0.0))
            except (TypeError, ValueError):
                elapsed = 0.0
            normalized.append(
                {
                    "phase": str(phase.get("phase", "unknown")),
                    "elapsed_seconds": round(elapsed, 4),
                }
            )
        return sorted(
            normalized,
            key=lambda item: float(item["elapsed_seconds"]),
            reverse=True,
        )[: max(0, int(limit))]

    @staticmethod
    def _file_sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def record_operational_preflight_audit(
        self,
        *,
        status_snapshot: dict[str, Any],
        integrity_report: dict[str, Any],
        lease_probe: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("operational preflight audit reason is required")
        pending_actions = status_snapshot.get("pending_actions", [])
        if not isinstance(pending_actions, list):
            pending_actions = []
        latest_cycle = status_snapshot.get("latest_cycle")
        if not isinstance(latest_cycle, dict):
            latest_cycle = {}
        runtime_failures: list[str] = []
        if status_snapshot.get("paused") is not False:
            runtime_failures.append("runtime paused")
        if status_snapshot.get("killed") is not False:
            runtime_failures.append("kill switch active")
        if pending_actions:
            runtime_failures.append("pending actions require approval or reconciliation")
        if int(status_snapshot.get("cycle_count", 0) or 0) < 1:
            runtime_failures.append("no completed cycle evidence")
        if latest_cycle.get("status") != "SUCCEEDED":
            runtime_failures.append("latest cycle is not SUCCEEDED")
        integrity_failures = [] if integrity_report.get("ok") is True else ["integrity not ok"]
        lease_failures = [
            key
            for key in (
                "runtime_lock_available",
                "daemon_lock_available",
                "no_stale_runtime_lock",
                "no_stale_daemon_lock",
            )
            if lease_probe.get(key) is not True
        ]
        startup_resume = status_snapshot.get("startup_resume", {})
        if not isinstance(startup_resume, dict):
            startup_resume = {}
        resume_failures = (
            []
            if startup_resume.get("unresolved_running_actions", 0) in {0, None}
            else ["unresolved running actions after startup"]
        )
        failure_groups = {
            "runtime_failures": runtime_failures,
            "integrity_failures": integrity_failures,
            "lease_failures": lease_failures,
            "resume_failures": resume_failures,
        }
        passed = not any(failure_groups.values())
        receipt = {
            "receipt_type": "OPERATIONAL_PREFLIGHT_AUDIT",
            "status": "OPERATIONAL_PREFLIGHT_PASSED" if passed else "OPERATIONAL_PREFLIGHT_BLOCKED",
            "audit_id": new_id("operational_preflight_audit"),
            "reason": reason,
            "status_snapshot": {
                "home": status_snapshot.get("home"),
                "read_only": status_snapshot.get("read_only"),
                "paused": status_snapshot.get("paused"),
                "killed": status_snapshot.get("killed"),
                "cycle_count": status_snapshot.get("cycle_count"),
                "latest_cycle": latest_cycle,
                "pending_action_count": len(pending_actions),
                "startup_resume": startup_resume,
            },
            "integrity_report": integrity_report,
            "lease_probe": lease_probe,
            "failure_groups": failure_groups,
            "daemon_started": False,
            "live_install_modified": False,
            "live_config_modified": False,
            "live_database_modified": False,
            "claim_ceiling": (
                "operational preflight receipt only; it validates a bounded "
                "runtime status snapshot, integrity report, and lease probe but "
                "does not start a daemon, modify live state, or prove long-run uptime"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.operational_preflight_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("operational_preflight_receipts", updated, connection)
            self.ledger.append("operational_preflight_audit_recorded", receipt, connection)
        return receipt

    def record_delivery_handoff_package(
        self,
        *,
        readiness_summary: dict[str, Any],
        candidate: dict[str, str],
        test_results: list[dict[str, Any]],
        owner_commands: dict[str, str],
        rollback_steps: list[str],
        boundaries: dict[str, bool],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("delivery handoff package reason is required")
        branch = str(candidate.get("branch", "")).strip()
        commit = str(candidate.get("commit", "")).strip()
        candidate_failures = []
        if not branch:
            candidate_failures.append("missing candidate branch")
        if branch in {"main", "master"}:
            candidate_failures.append("candidate branch must not be main/master")
        if not commit:
            candidate_failures.append("missing candidate commit")
        readiness_failures = (
            []
            if readiness_summary.get("overall_status") == "CANDIDATE_READY"
            and not readiness_summary.get("missing_or_blocked")
            else ["readiness summary is not candidate-ready"]
        )
        test_failures = [
            str(item.get("name", "unnamed"))
            for item in test_results
            if item.get("status") not in {"PASS", "PASS_WITH_LIMITS"}
        ]
        required_commands = {"R01_R05": ("R01", "R05"), "R01_R40": ("R01", "R40")}
        command_failures = []
        for command_id, (start, end) in required_commands.items():
            command = owner_commands.get(command_id, "")
            fragments = [
                "run_life_campaign_30.ps1",
                "-CampaignHome",
                "-StartRound",
                start,
                "-EndRound",
                end,
                "--execute",
            ]
            if not command or any(fragment not in command for fragment in fragments):
                command_failures.append(command_id)
        rollback_text = "\n".join(rollback_steps).lower()
        rollback_failures = []
        if not rollback_steps:
            rollback_failures.append("missing rollback steps")
        if "campaign" not in rollback_text:
            rollback_failures.append("campaign cleanup missing")
        if "git" not in rollback_text:
            rollback_failures.append("git rollback missing")
        if "live" not in rollback_text:
            rollback_failures.append("live boundary missing")
        expected_false_boundaries = {
            "live_install_modified",
            "live_config_modified",
            "live_database_modified",
            "merge_executed",
            "deploy_executed",
            "skill_promoted",
        }
        boundary_failures = [
            key for key in sorted(expected_false_boundaries) if boundaries.get(key) is not False
        ]
        failure_groups = {
            "candidate_failures": candidate_failures,
            "readiness_failures": readiness_failures,
            "test_failures": test_failures,
            "command_failures": command_failures,
            "rollback_failures": rollback_failures,
            "boundary_failures": boundary_failures,
        }
        passed = not any(failure_groups.values())
        receipt = {
            "receipt_type": "DELIVERY_HANDOFF_PACKAGE",
            "status": "DELIVERY_HANDOFF_READY" if passed else "DELIVERY_HANDOFF_BLOCKED",
            "handoff_id": new_id("delivery_handoff"),
            "reason": reason,
            "readiness_summary": readiness_summary,
            "candidate": {"branch": branch, "commit": commit},
            "test_results": test_results,
            "owner_commands": owner_commands,
            "rollback_steps": rollback_steps,
            "boundaries": boundaries,
            "failure_groups": failure_groups,
            "live_install_modified": False,
            "merge_executed": False,
            "deploy_executed": False,
            "skill_promoted": False,
            "claim_ceiling": (
                "delivery handoff package receipt only; it summarizes candidate "
                "readiness, tests, commands, and rollback but does not execute "
                "campaigns, merge, deploy, install packages, or promote Skills"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.delivery_handoff_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("delivery_handoff_receipts", updated, connection)
            self.ledger.append("delivery_handoff_package_recorded", receipt, connection)
        return receipt

    def record_release_state_audit(
        self,
        *,
        receipt_counts: dict[str, int],
        asset_checks: dict[str, bool],
        test_results: list[dict[str, Any]],
        boundaries: dict[str, bool],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("release state audit reason is required")
        required_receipts = [
            "final_delivery_audit",
            "delivery_readiness",
            "packaging_layout",
            "installed_tail_check",
            "operational_preflight",
            "delivery_handoff",
        ]
        receipt_failures = [
            key for key in required_receipts if int(receipt_counts.get(key, 0)) < 1
        ]
        required_assets = [
            "campaign_spec",
            "campaign_runner",
            "powershell_entry",
            "owner_console_static",
            "delivery_handoff_script",
            "architecture_doc",
        ]
        asset_failures = [
            key for key in required_assets if asset_checks.get(key) is not True
        ]
        test_failures = [
            str(item.get("name", "unnamed"))
            for item in test_results
            if item.get("status") not in {"PASS", "PASS_WITH_LIMITS"}
        ]
        expected_false_boundaries = {
            "live_install_modified",
            "live_config_modified",
            "live_database_modified",
            "merge_executed",
            "deploy_executed",
            "skill_promoted",
            "persistent_daemon_started",
        }
        boundary_failures = [
            key for key in sorted(expected_false_boundaries) if boundaries.get(key) is not False
        ]
        failure_groups = {
            "receipt_failures": receipt_failures,
            "asset_failures": asset_failures,
            "test_failures": test_failures,
            "boundary_failures": boundary_failures,
        }
        passed = not any(failure_groups.values())
        receipt = {
            "receipt_type": "RELEASE_STATE_AUDIT",
            "status": "RELEASE_STATE_CANDIDATE_READY" if passed else "RELEASE_STATE_BLOCKED",
            "audit_id": new_id("release_state_audit"),
            "reason": reason,
            "receipt_counts": receipt_counts,
            "asset_checks": asset_checks,
            "test_results": test_results,
            "boundaries": boundaries,
            "failure_groups": failure_groups,
            "allowed_conclusion": (
                "candidate-ready repository handoff"
                if passed
                else "release state requires missing evidence repair"
            ),
            "live_install_modified": False,
            "merge_executed": False,
            "deploy_executed": False,
            "skill_promoted": False,
            "claim_ceiling": (
                "release state audit receipt only; it summarizes repository "
                "candidate evidence and boundaries but does not prove live "
                "deployment, production readiness, or long-run external behavior"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.release_state_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("release_state_audit_receipts", updated, connection)
            self.ledger.append("release_state_audit_recorded", receipt, connection)
        return receipt

    def record_ui_hardening_audit(
        self,
        *,
        defect_checks: dict[str, bool],
        boundary_checks: dict[str, bool],
        test_results: list[dict[str, Any]],
        real_browser_e2e: bool,
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("UI hardening audit reason is required")
        required_defects = [f"D{index:02d}" for index in range(1, 19)]
        defect_failures = [
            key for key in required_defects if defect_checks.get(key) is not True
        ]
        expected_boundaries = {
            "loopback_only",
            "no_auth_cookie",
            "no_persistent_ui_token",
            "no_ui_completion_authority",
            "no_second_goal_store",
            "cycle_request_lock",
            "sanitized_500",
        }
        boundary_failures = [
            key for key in sorted(expected_boundaries) if boundary_checks.get(key) is not True
        ]
        test_failures = [
            str(item.get("name", "unnamed"))
            for item in test_results
            if item.get("status") not in {"PASS", "PASS_WITH_LIMITS"}
        ]
        failure_groups = {
            "defect_failures": defect_failures,
            "boundary_failures": boundary_failures,
            "test_failures": test_failures,
            "owner_host_gates": [] if real_browser_e2e else ["real_browser_e2e"],
        }
        passed = not (
            defect_failures or boundary_failures or test_failures
        )
        receipt = {
            "receipt_type": "UI_HARDENING_AUDIT",
            "status": "UI_HARDENING_CANDIDATE_READY"
            if passed
            else "UI_HARDENING_BLOCKED",
            "audit_id": new_id("ui_hardening_audit"),
            "reason": reason,
            "defect_checks": defect_checks,
            "boundary_checks": boundary_checks,
            "test_results": test_results,
            "real_browser_e2e": real_browser_e2e,
            "failure_groups": failure_groups,
            "allowed_conclusion": (
                "local UI hardening candidate"
                if passed
                else "UI hardening requires missing evidence repair"
            ),
            "live_install_modified": False,
            "ui_completion_authority": False,
            "persistent_ui_token": False,
            "claim_ceiling": (
                "UI hardening audit receipt only; local tests can support "
                "candidate readiness, while real browser and Owner host behavior "
                "remain separate gates unless explicitly evidenced"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.ui_hardening_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("ui_hardening_audit_receipts", updated, connection)
            self.ledger.append("ui_hardening_audit_recorded", receipt, connection)
        return receipt

    def record_delivery_gap_audit(
        self,
        *,
        package_coverage: dict[str, str],
        milestone_coverage: dict[str, str],
        owner_host_gates: dict[str, bool],
        repository_checks: dict[str, bool],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("delivery gap audit reason is required")
        allowed_states = {"COVERED", "PARTIAL", "MISSING", "BLOCKED", "OWNER_GATE"}
        invalid_package_states = {
            key: value
            for key, value in package_coverage.items()
            if value not in allowed_states
        }
        invalid_milestone_states = {
            key: value
            for key, value in milestone_coverage.items()
            if value not in allowed_states
        }
        missing_packages = [
            key
            for key, value in package_coverage.items()
            if value in {"MISSING", "BLOCKED"}
        ]
        missing_milestones = [
            key
            for key, value in milestone_coverage.items()
            if value in {"MISSING", "BLOCKED"}
        ]
        failed_repository_checks = [
            key for key, value in repository_checks.items() if value is not True
        ]
        unresolved_owner_gates = [
            key for key, value in owner_host_gates.items() if value is not True
        ]
        failure_groups = {
            "invalid_package_states": sorted(invalid_package_states),
            "invalid_milestone_states": sorted(invalid_milestone_states),
            "missing_packages": sorted(missing_packages),
            "missing_milestones": sorted(missing_milestones),
            "failed_repository_checks": sorted(failed_repository_checks),
            "owner_host_gates": sorted(unresolved_owner_gates),
        }
        candidate_ready = not (
            invalid_package_states
            or invalid_milestone_states
            or missing_packages
            or missing_milestones
            or failed_repository_checks
        )
        receipt = {
            "receipt_type": "DELIVERY_GAP_AUDIT",
            "status": "DELIVERY_GAP_CANDIDATE_READY"
            if candidate_ready
            else "DELIVERY_GAP_BLOCKED",
            "audit_id": new_id("delivery_gap_audit"),
            "reason": reason,
            "package_coverage": package_coverage,
            "milestone_coverage": milestone_coverage,
            "owner_host_gates": owner_host_gates,
            "repository_checks": repository_checks,
            "failure_groups": failure_groups,
            "allowed_conclusion": (
                "repository candidate ready with explicit owner-host gates"
                if candidate_ready
                else "delivery gaps require repair before handoff"
            ),
            "live_install_modified": False,
            "merge_executed": False,
            "deploy_executed": False,
            "skill_promoted": False,
            "claim_ceiling": (
                "delivery gap audit receipt only; repository candidate coverage "
                "can be summarized, while unresolved owner-host gates remain "
                "outside this proof"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.delivery_gap_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("delivery_gap_audit_receipts", updated, connection)
            self.ledger.append("delivery_gap_audit_recorded", receipt, connection)
        return receipt

    def record_ui_package_absorption(
        self,
        *,
        package_name: str,
        package_hash: str,
        payload_files: list[str],
        defect_checks: dict[str, bool],
        current_source_checks: dict[str, bool],
        owner_host_gates: dict[str, bool],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("UI package absorption reason is required")
        if not package_name.strip():
            raise ValueError("UI package name is required")
        normalized_hash = package_hash.strip().lower()
        if len(normalized_hash) != 64 or any(
            item not in "0123456789abcdef" for item in normalized_hash
        ):
            raise ValueError("UI package hash must be a lowercase SHA256 hex digest")
        required_defects = [f"D{index:02d}" for index in range(1, 19)]
        required_source_checks = {
            "current_ui_projection_superset",
            "current_ui_server_compatible",
            "static_assets_present",
            "tests_present",
            "no_payload_overwrite_required",
            "no_new_runtime_dependency",
            "no_database_migration",
            "no_second_ui_authority",
        }
        defect_failures = [
            key for key in required_defects if defect_checks.get(key) is not True
        ]
        source_failures = [
            key
            for key in sorted(required_source_checks)
            if current_source_checks.get(key) is not True
        ]
        missing_payload_files = [
            item for item in payload_files if not isinstance(item, str) or not item
        ]
        owner_gate_failures = [
            key for key, value in owner_host_gates.items() if value is not True
        ]
        failure_groups = {
            "defect_failures": defect_failures,
            "source_failures": source_failures,
            "missing_payload_files": missing_payload_files,
            "owner_host_gates": sorted(owner_gate_failures),
        }
        candidate_ready = not (
            defect_failures or source_failures or missing_payload_files
        )
        receipt = {
            "receipt_type": "UI_PACKAGE_ABSORPTION",
            "status": "UI_PACKAGE_ABSORBED_AS_CANDIDATE_EVIDENCE"
            if candidate_ready
            else "UI_PACKAGE_ABSORPTION_BLOCKED",
            "audit_id": new_id("ui_package_absorption"),
            "package_name": package_name,
            "package_hash": normalized_hash,
            "payload_files": sorted(payload_files),
            "defect_checks": defect_checks,
            "current_source_checks": current_source_checks,
            "owner_host_gates": owner_host_gates,
            "failure_groups": failure_groups,
            "allowed_conclusion": (
                "UI package evidence absorbed into repository candidate"
                if candidate_ready
                else "UI package absorption requires repair before handoff"
            ),
            "payload_overwrite_executed": False,
            "live_install_modified": False,
            "database_migration": False,
            "new_runtime_dependency": False,
            "second_ui_authority_created": False,
            "claim_ceiling": (
                "UI package absorption receipt only; package payload and defect "
                "ledger are mapped into current repository evidence, while real "
                "browser and Owner-host validation remain separate gates"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.ui_package_absorption_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "ui_package_absorption_receipts", updated, connection
            )
            self.ledger.append("ui_package_absorption_recorded", receipt, connection)
        return receipt

    def record_final_route_absorption(
        self,
        *,
        package_hash: str,
        route_nodes: dict[str, str],
        campaign_rounds: dict[str, str],
        claim_rules: dict[str, bool],
        source_ledgers: list[str],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("final route absorption reason is required")
        normalized_hash = package_hash.strip().lower()
        if len(normalized_hash) != 64 or any(
            item not in "0123456789abcdef" for item in normalized_hash
        ):
            raise ValueError("final route hash must be a lowercase SHA256 hex digest")
        allowed_states = {"COVERED", "PARTIAL", "MISSING", "CONFLICT", "OWNER_GATE"}
        invalid_node_states = {
            key: value for key, value in route_nodes.items() if value not in allowed_states
        }
        invalid_round_states = {
            key: value
            for key, value in campaign_rounds.items()
            if value not in allowed_states
        }
        node_gaps = [
            key
            for key, value in route_nodes.items()
            if value in {"MISSING", "CONFLICT"}
        ]
        round_gaps = [
            key
            for key, value in campaign_rounds.items()
            if value in {"MISSING", "CONFLICT"}
        ]
        required_claim_rules = {
            "coded_vs_tested_separated",
            "campaign_verified_requires_campaign",
            "external_verified_requires_external_evidence",
            "promotion_requires_owner_authorization",
            "no_second_runtime_from_route_package",
        }
        claim_rule_failures = [
            key for key in sorted(required_claim_rules) if claim_rules.get(key) is not True
        ]
        source_ledger_failures = [
            item for item in source_ledgers if not isinstance(item, str) or not item
        ]
        owner_gates = sorted(
            [
                key
                for key, value in {**route_nodes, **campaign_rounds}.items()
                if value == "OWNER_GATE"
            ]
        )
        failure_groups = {
            "invalid_node_states": sorted(invalid_node_states),
            "invalid_round_states": sorted(invalid_round_states),
            "node_gaps": sorted(node_gaps),
            "round_gaps": sorted(round_gaps),
            "claim_rule_failures": claim_rule_failures,
            "source_ledger_failures": source_ledger_failures,
            "owner_gates": owner_gates,
        }
        candidate_ready = not (
            invalid_node_states
            or invalid_round_states
            or node_gaps
            or round_gaps
            or claim_rule_failures
            or source_ledger_failures
        )
        receipt = {
            "receipt_type": "FINAL_ROUTE_ABSORPTION",
            "status": "FINAL_ROUTE_ABSORBED_AS_CANDIDATE_MAP"
            if candidate_ready
            else "FINAL_ROUTE_ABSORPTION_BLOCKED",
            "audit_id": new_id("final_route_absorption"),
            "package_hash": normalized_hash,
            "route_nodes": route_nodes,
            "campaign_rounds": campaign_rounds,
            "claim_rules": claim_rules,
            "source_ledgers": sorted(source_ledgers),
            "failure_groups": failure_groups,
            "allowed_conclusion": (
                "final route package mapped into repository candidate coverage"
                if candidate_ready
                else "final route package absorption requires repair before handoff"
            ),
            "installed_route_package": False,
            "created_second_runtime": False,
            "promotion_executed": False,
            "merge_executed": False,
            "claim_ceiling": (
                "Final route absorption receipt only; maps roadmap and claim rules "
                "into current repository evidence without installing package content, "
                "merging, promoting, or proving Owner-host campaigns"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.final_route_absorption_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "final_route_absorption_receipts", updated, connection
            )
            self.ledger.append("final_route_absorption_recorded", receipt, connection)
        return receipt

    def record_source_artifact_inventory(
        self,
        *,
        artifact_hashes: dict[str, str],
        coverage: dict[str, str],
        owner_host_gates: dict[str, bool],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("source artifact inventory reason is required")
        allowed_states = {"ABSORBED", "SUPERSEDED", "OWNER_GATE", "PARTIAL", "MISSING", "CONFLICT"}
        invalid_hashes = [
            key
            for key, value in artifact_hashes.items()
            if len(str(value).strip()) != 64
            or any(item not in "0123456789abcdef" for item in str(value).strip().lower())
        ]
        invalid_coverage = {
            key: value for key, value in coverage.items() if value not in allowed_states
        }
        missing_artifacts = [
            key for key, value in coverage.items() if value in {"MISSING", "CONFLICT"}
        ]
        untracked_hashes = sorted(set(artifact_hashes) - set(coverage))
        owner_gate_failures = [
            key for key, value in owner_host_gates.items() if value is not True
        ]
        failure_groups = {
            "invalid_hashes": sorted(invalid_hashes),
            "invalid_coverage": sorted(invalid_coverage),
            "missing_artifacts": sorted(missing_artifacts),
            "untracked_hashes": untracked_hashes,
            "owner_host_gates": sorted(owner_gate_failures),
        }
        candidate_ready = not (
            invalid_hashes or invalid_coverage or missing_artifacts or untracked_hashes
        )
        receipt = {
            "receipt_type": "SOURCE_ARTIFACT_INVENTORY",
            "status": "SOURCE_ARTIFACTS_MAPPED_AS_CANDIDATE_EVIDENCE"
            if candidate_ready
            else "SOURCE_ARTIFACT_INVENTORY_BLOCKED",
            "audit_id": new_id("source_artifact_inventory"),
            "artifact_hashes": {
                key: str(value).strip().lower() for key, value in artifact_hashes.items()
            },
            "coverage": coverage,
            "owner_host_gates": owner_host_gates,
            "failure_groups": failure_groups,
            "allowed_conclusion": (
                "local source artifacts mapped into repository candidate evidence"
                if candidate_ready
                else "source artifact inventory requires repair before handoff"
            ),
            "live_install_modified": False,
            "artifact_install_executed": False,
            "merge_executed": False,
            "deploy_executed": False,
            "claim_ceiling": (
                "source artifact inventory receipt only; hashes and coverage states "
                "are mapped into repository evidence while Owner-host gates remain separate"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.source_artifact_inventory_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "source_artifact_inventory_receipts", updated, connection
            )
            self.ledger.append("source_artifact_inventory_recorded", receipt, connection)
        return receipt

    def record_delivery_self_check(
        self,
        *,
        exact_head: str,
        validation_results: list[dict[str, Any]],
        handoff_summary: dict[str, Any],
        repository_checks: dict[str, bool],
        boundary_checks: dict[str, bool],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("delivery self-check reason is required")
        if not exact_head.strip():
            raise ValueError("delivery self-check exact_head is required")
        validation_failures = [
            str(item.get("pass_id", "UNKNOWN"))
            for item in validation_results
            if item.get("verdict") != "ADMIT_SHADOW_ONLY"
        ]
        required_passes = {f"P{index:02d}" for index in range(81, 89)}
        observed_passes = {
            str(item.get("pass_id"))
            for item in validation_results
            if isinstance(item, dict)
        }
        missing_passes = sorted(required_passes - observed_passes)
        repository_failures = [
            key for key, value in repository_checks.items() if value is not True
        ]
        boundary_failures = [
            key for key, value in boundary_checks.items() if value is True
        ]
        handoff_failures: list[str] = []
        if handoff_summary.get("artifact_type") != "WLS_DELIVERY_HANDOFF_PACKAGE":
            handoff_failures.append("artifact_type")
        candidate = handoff_summary.get("candidate", {})
        if not isinstance(candidate, dict) or candidate.get("commit") != exact_head:
            handoff_failures.append("candidate_commit")
        delivery_gap = handoff_summary.get("delivery_gap_summary", {})
        if not isinstance(delivery_gap, dict) or delivery_gap.get("overall_status") != (
            "CANDIDATE_READY_WITH_OWNER_HOST_GATES"
        ):
            handoff_failures.append("delivery_gap_summary")
        failure_groups = {
            "validation_failures": validation_failures,
            "missing_passes": missing_passes,
            "repository_failures": sorted(repository_failures),
            "boundary_failures": sorted(boundary_failures),
            "handoff_failures": sorted(handoff_failures),
        }
        candidate_ready = not any(failure_groups.values())
        receipt = {
            "receipt_type": "DELIVERY_SELF_CHECK",
            "status": "DELIVERY_SELF_CHECK_PASSED"
            if candidate_ready
            else "DELIVERY_SELF_CHECK_BLOCKED",
            "audit_id": new_id("delivery_self_check"),
            "exact_head": exact_head,
            "validation_results": validation_results,
            "handoff_summary": handoff_summary,
            "repository_checks": repository_checks,
            "boundary_checks": boundary_checks,
            "failure_groups": failure_groups,
            "allowed_conclusion": (
                "candidate self-check passed for repository handoff"
                if candidate_ready
                else "candidate self-check requires repair before handoff"
            ),
            "live_install_modified": False,
            "live_config_modified": False,
            "live_database_modified": False,
            "merge_executed": False,
            "deploy_executed": False,
            "claim_ceiling": (
                "delivery self-check receipt only; proves repository handoff "
                "self-consistency for the checked exact head, not live deployment "
                "or Owner-host longitudinal readiness"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.delivery_self_check_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("delivery_self_check_receipts", updated, connection)
            self.ledger.append("delivery_self_check_recorded", receipt, connection)
        return receipt

    def record_installed_tail_check_audit(
        self,
        *,
        report: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("installed tail check audit reason is required")
        checks = report.get("checks", {})
        if not isinstance(checks, dict):
            checks = {}
        required_checks = report.get("required_checks", [])
        if not isinstance(required_checks, list):
            required_checks = []
        failed_required = [
            str(name)
            for name in required_checks
            if checks.get(str(name)) is not True
        ]
        before = report.get("live_hashes_before", {})
        after = report.get("live_hashes_after", {})
        if not isinstance(before, dict):
            before = {}
        if not isinstance(after, dict):
            after = {}
        live_hash_failures = [
            key for key, value in before.items() if after.get(key) != value
        ]
        smoke = report.get("status_smoke", {})
        if not isinstance(smoke, dict):
            smoke = {}
        smoke_failure = smoke.get("ok") is False
        required_paths = ["install_root", "live_home", "campaign_home"]
        path_failures = [key for key in required_paths if not str(report.get(key, "")).strip()]
        failure_groups = {
            "failed_required_checks": failed_required,
            "live_hash_failures": live_hash_failures,
            "smoke_failures": ["status_smoke"] if smoke_failure else [],
            "path_failures": path_failures,
        }
        passed = (
            report.get("receipt_type") == "SINGLE_SOFTWARE_TAIL_CHECK"
            and report.get("status") == "PASS_WITH_LIMITS"
            and not any(failure_groups.values())
        )
        receipt = {
            "receipt_type": "INSTALLED_TAIL_CHECK_AUDIT",
            "status": "INSTALLED_TAIL_CHECK_PASSED" if passed else "INSTALLED_TAIL_CHECK_BLOCKED",
            "audit_id": new_id("installed_tail_check_audit"),
            "reason": reason,
            "report": report,
            "failure_groups": failure_groups,
            "install_root": report.get("install_root"),
            "live_home": report.get("live_home"),
            "campaign_home": report.get("campaign_home"),
            "live_hashes_before": before,
            "live_hashes_after": after,
            "status_smoke": smoke,
            "live_config_modified": False if not live_hash_failures else True,
            "live_database_modified": False if not live_hash_failures else True,
            "merge_executed": False,
            "deploy_executed": False,
            "skill_promoted": False,
            "claim_ceiling": (
                "installed-package tail-check receipt only; it verifies bounded "
                "disposable compatibility evidence and live hash preservation from "
                "a report but does not install, upgrade, deploy, or prove final readiness"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.installed_tail_check_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("installed_tail_check_receipts", updated, connection)
            self.ledger.append("installed_tail_check_audit_recorded", receipt, connection)
        return receipt

    def record_delivery_readiness_audit(
        self,
        *,
        branch: str,
        commit: str,
        owner_commands: dict[str, str],
        campaign_assets: dict[str, bool],
        rollback_steps: list[str],
        boundaries: dict[str, bool],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("delivery readiness audit reason is required")
        branch_name = branch.strip()
        commit_ref = commit.strip()
        branch_failures = []
        if not branch_name:
            branch_failures.append("missing candidate branch")
        if branch_name in {"main", "master"}:
            branch_failures.append("candidate branch must not be main/master")
        commit_failures = [] if commit_ref else ["missing candidate commit"]
        required_assets = {
            "campaign_spec": "source/verification/life_campaign_30.json",
            "python_runner": "source/scripts/run_life_campaign_30.py",
            "powershell_entry": "scripts/run_life_campaign_30.ps1",
            "campaign_tests": "source/tests/test_life_campaign_30.py",
            "campaign_architecture_doc": "docs/architecture/WLS_LIFE_CAMPAIGN_30.md",
        }
        asset_failures = [
            path
            for key, path in required_assets.items()
            if campaign_assets.get(key) is not True
        ]
        required_commands = {"R01_R05": ("R01", "R05"), "R01_R40": ("R01", "R40")}
        command_failures: list[str] = []
        for command_id, (start, end) in required_commands.items():
            command = owner_commands.get(command_id, "")
            required_fragments = [
                "scripts",
                "run_life_campaign_30.ps1",
                "-CampaignHome",
                "-StartRound",
                start,
                "-EndRound",
                end,
                "--execute",
            ]
            if not command or any(fragment not in command for fragment in required_fragments):
                command_failures.append(command_id)
        rollback_text = "\n".join(rollback_steps).lower()
        rollback_failures = []
        if not rollback_steps:
            rollback_failures.append("missing rollback steps")
        if "campaign" not in rollback_text or not (
            "delete" in rollback_text or "remove" in rollback_text
        ):
            rollback_failures.append("disposable campaign home cleanup missing")
        if "git" not in rollback_text:
            rollback_failures.append("candidate branch rollback missing")
        if "live" not in rollback_text:
            rollback_failures.append("live installation boundary missing")
        expected_false_boundaries = {
            "live_install_modified",
            "live_config_modified",
            "live_database_modified",
            "merge_executed",
            "deploy_executed",
            "skill_promoted",
        }
        boundary_failures = [
            key for key in sorted(expected_false_boundaries) if boundaries.get(key) is not False
        ]
        failure_groups = {
            "branch_failures": branch_failures,
            "commit_failures": commit_failures,
            "asset_failures": asset_failures,
            "command_failures": command_failures,
            "rollback_failures": rollback_failures,
            "boundary_failures": boundary_failures,
        }
        passed = not any(failure_groups.values())
        receipt = {
            "receipt_type": "DELIVERY_READINESS_AUDIT",
            "status": "DELIVERY_READY_CANDIDATE" if passed else "DELIVERY_READY_BLOCKED",
            "audit_id": new_id("delivery_readiness_audit"),
            "reason": reason,
            "candidate": {
                "branch": branch_name,
                "commit": commit_ref,
                "branch_failures": branch_failures,
                "commit_failures": commit_failures,
            },
            "campaign_assets": {
                "required": required_assets,
                "present": campaign_assets,
                "missing": asset_failures,
            },
            "owner_commands": {
                "commands": owner_commands,
                "required": sorted(required_commands),
                "failures": command_failures,
            },
            "rollback": {
                "steps": rollback_steps,
                "failures": rollback_failures,
            },
            "boundaries": {
                "expected_false": sorted(expected_false_boundaries),
                "observed": boundaries,
                "failures": boundary_failures,
            },
            "failure_groups": failure_groups,
            "live_install_modified": False,
            "merge_executed": False,
            "deploy_executed": False,
            "skill_promoted": False,
            "claim_ceiling": (
                "candidate delivery readiness receipt only; it verifies repository "
                "commands, rollback instructions, and safety boundaries but does not "
                "prove live deployment, package installation, or external readiness"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.delivery_readiness_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("delivery_readiness_receipts", updated, connection)
            self.ledger.append("delivery_readiness_audit_recorded", receipt, connection)
        return receipt

    def record_final_delivery_audit(
        self,
        *,
        console_trace: list[dict[str, Any]],
        installer_recovery: dict[str, Any],
        claim_ledger: list[dict[str, Any]],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("final delivery audit reason is required")
        trace_failures = [
            item
            for item in console_trace
            if not (
                item.get("input")
                and item.get("tool")
                and item.get("receipt")
                and item.get("approval") is not None
                and item.get("artifact")
            )
        ]
        install_phases = ["preflight", "backup", "apply", "verify", "rollback", "uninstall"]
        install_failures = [
            phase
            for phase in install_phases
            if installer_recovery.get(phase, {}).get("passed") is not True
        ]
        allowed_evidence_levels = {"CODED", "TESTED", "CAMPAIGN", "EXTERNAL"}
        level_order = {"CODED": 0, "TESTED": 1, "CAMPAIGN": 2, "EXTERNAL": 3}
        max_claim_level = str(installer_recovery.get("max_claim_level", "TESTED"))
        claim_failures = []
        for claim in claim_ledger:
            evidence_level = str(claim.get("evidence_level", ""))
            claim_level = str(claim.get("claim_level", ""))
            if (
                evidence_level not in allowed_evidence_levels
                or claim_level not in allowed_evidence_levels
                or level_order[claim_level] > level_order[evidence_level]
                or level_order[claim_level] > level_order.get(max_claim_level, 1)
            ):
                claim_failures.append(claim)
        ui_unknown = any(item.get("coverage") == "UNKNOWN" for item in console_trace)
        passed = not trace_failures and not install_failures and not claim_failures and not ui_unknown
        receipt = {
            "receipt_type": "FINAL_DELIVERY_AUDIT",
            "status": "DELIVERY_AUDIT_PASSED" if passed else "DELIVERY_AUDIT_BLOCKED",
            "audit_id": new_id("final_delivery_audit"),
            "reason": reason,
            "console_convergence": {
                "trace_count": len(console_trace),
                "trace_failures": trace_failures,
                "ui_unknown": ui_unknown,
                "default_read_only": True,
                "dangerous_actions_require_approval": True,
            },
            "installer_recovery": {
                "phases": installer_recovery,
                "required_phases": install_phases,
                "failed_phases": install_failures,
                "reversible": not install_failures,
            },
            "claim_ledger": {
                "entries": claim_ledger,
                "allowed_levels": sorted(allowed_evidence_levels),
                "max_claim_level": max_claim_level,
                "claim_failures": claim_failures,
            },
            "canonical_state_mutated": False,
            "live_install_modified": False,
            "publish_executed": False,
            "claim_ceiling": (
                "final delivery audit receipt only; repository-scoped traceability, "
                "recovery, and claim-ledger checks do not prove live deployment or "
                "external product readiness"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.final_delivery_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("final_delivery_audit_receipts", updated, connection)
            self.ledger.append("final_delivery_audit_recorded", receipt, connection)
        return receipt

    def record_commercial_readiness_audit(
        self,
        *,
        reason: str,
        rc_min_qualified_measurements: int = 8,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("commercial readiness audit reason is required")
        if rc_min_qualified_measurements < 1:
            raise ValueError("rc_min_qualified_measurements must be positive")

        health = self.health_snapshot()
        report = health.get("longitudinal_report")
        measurements = self.longitudinal_measurement_receipts(limit=500)
        qualified_measurement_count = sum(
            1 for item in measurements if self._owner_task_evidence_complete(item)
        )
        task_classes = sorted(
            {
                str(item.get("task_class"))
                for item in measurements
                if self._owner_task_evidence_complete(item) and item.get("task_class")
            }
        )
        performance = health.get("performance_budget")
        soak = health.get("bounded_soak")
        garbage = health.get("garbage_audit")
        retention = health.get("retention_audit")
        upgrade = health.get("upgrade_drill")

        def status_is(item: Any, allowed: set[str]) -> bool:
            return isinstance(item, dict) and str(item.get("status")) in allowed

        beta_gates = {
            "health_ok": health.get("status") == "OK",
            "no_pending_owner_actions": int(health.get("pending_action_count", 0)) == 0,
            "garbage_audit_present": isinstance(garbage, dict),
            "performance_budget_passed": status_is(
                performance, {"PERFORMANCE_BUDGET_PASSED", "PASSED"}
            ),
            "retention_audit_passed": status_is(
                retention, {"RETENTION_AUDIT_PASSED", "PASSED"}
            ),
            "bounded_soak_passed": status_is(
                soak, {"BOUNDED_SOAK_PASSED", "SOAK_AUDIT_PASSED", "PASSED"}
            ),
            "rollback_drill_passed": status_is(
                upgrade, {"UPGRADE_DRILL_PASSED", "PASSED"}
            ),
            "first_owner_task_measurement": qualified_measurement_count >= 1,
        }
        rc_gates = {
            **beta_gates,
            "repeated_owner_task_measurements": (
                qualified_measurement_count >= rc_min_qualified_measurements
            ),
            "owner_task_class_coverage": len(task_classes) >= 4,
            "longitudinal_report_passed": status_is(
                report, {"LONGITUDINAL_REPORT_PASSED", "PASSED"}
            ),
            "disposable_clone_rollback_verified": (
                isinstance(upgrade, dict)
                and upgrade.get("disposable_clone_executed") is True
                and (
                    upgrade.get("disposable_clone_rollback", {}).get("passed")
                    is True
                )
            ),
            "readiness_runbook_present": (
                self._commercial_readiness_runbook_path() is not None
            ),
        }
        beta_missing = [key for key, passed in beta_gates.items() if not passed]
        rc_missing = [key for key, passed in rc_gates.items() if not passed]
        receipt = {
            "receipt_type": "COMMERCIAL_READINESS_AUDIT",
            "audit_id": new_id("commercial_readiness_audit"),
            "status": "RC_PASSED"
            if not rc_missing
            else "BETA_PASSED_RC_BLOCKED"
            if not beta_missing
            else "BETA_BLOCKED",
            "reason": reason,
            "beta": {
                "status": "BETA_PASSED" if not beta_missing else "BETA_BLOCKED",
                "gates": beta_gates,
                "missing": beta_missing,
            },
            "rc": {
                "status": "RC_PASSED" if not rc_missing else "RC_BLOCKED",
                "gates": rc_gates,
                "missing": rc_missing,
                "required_qualified_measurements": rc_min_qualified_measurements,
            },
            "evidence": {
                "health_status": health.get("status"),
                "garbage_audit_id": garbage.get("audit_id")
                if isinstance(garbage, dict)
                else None,
                "performance_budget_id": performance.get("audit_id")
                if isinstance(performance, dict)
                else None,
                "bounded_soak_id": soak.get("audit_id")
                if isinstance(soak, dict)
                else None,
                "retention_audit_id": retention.get("audit_id")
                if isinstance(retention, dict)
                else None,
                "upgrade_drill_id": upgrade.get("drill_id")
                if isinstance(upgrade, dict)
                else None,
                "longitudinal_report_id": report.get("report_id")
                if isinstance(report, dict)
                else None,
                "qualified_measurement_count": qualified_measurement_count,
                "task_classes": task_classes,
            },
            "live_install_modified": False,
            "cleanup_executed": False,
            "claim_ceiling": (
                "commercial readiness audit reads local WLS receipts only; RC "
                "requires repeated owner-task measurements and does not claim "
                "external multi-user product readiness"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.commercial_readiness_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "commercial_readiness_audit_receipts", updated, connection
            )
            self.db.set_runtime("commercial_readiness_audit_last", receipt, connection)
            self.ledger.append(
                "commercial_readiness_audit_recorded", receipt, connection
            )
        return receipt

    def _commercial_readiness_runbook_path(self) -> Path | None:
        candidates = [
            Path.cwd() / "docs" / "COMMERCIAL_READINESS_GATES.md",
            self.config.home_path / "docs" / "COMMERCIAL_READINESS_GATES.md",
            self.config.home_path / "COMMERCIAL_READINESS_GATES.md",
        ]
        module_path = Path(__file__).resolve()
        candidates.extend(
            parent / "docs" / "COMMERCIAL_READINESS_GATES.md"
            for parent in module_path.parents
        )
        for path in candidates:
            if path.is_file():
                return path
        return None

    def record_transfer_efficiency_audit(
        self,
        *,
        capability_id: str,
        transfer_cases: list[dict[str, Any]],
        regression_cases: list[dict[str, Any]],
        efficiency_thresholds: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if not capability_id.strip() or not reason.strip():
            raise ValueError("transfer audit requires capability_id and reason")
        if not transfer_cases:
            raise ValueError("transfer audit requires transfer cases")
        if not regression_cases:
            raise ValueError("transfer audit requires regression cases")
        max_cost_ratio = float(efficiency_thresholds.get("max_cost_ratio", 1.0))
        min_success_per_cost = float(
            efficiency_thresholds.get("min_success_per_cost", 0.0)
        )
        transfer_matrix = []
        transfer_failures = []
        efficiency_failures = []
        success_per_cost_values = []
        cost_ratio_values: list[float] = []
        for case in transfer_cases:
            case_id = str(case.get("case_id", ""))
            baseline_success = float(case.get("baseline_success_rate", 0.0))
            candidate_success = float(case.get("candidate_success_rate", 0.0))
            baseline_cost = max(float(case.get("baseline_cost", 0.0)), 0.000001)
            candidate_cost = max(float(case.get("candidate_cost", 0.0)), 0.000001)
            cost_ratio = candidate_cost / baseline_cost
            success_per_cost = candidate_success / candidate_cost
            cost_ratio_values.append(cost_ratio)
            success_per_cost_values.append(success_per_cost)
            transfer_passed = candidate_success >= baseline_success
            efficient = (
                cost_ratio <= max_cost_ratio
                and success_per_cost >= min_success_per_cost
            )
            row = {
                "case_id": case_id,
                "domain": case.get("domain"),
                "model": case.get("model"),
                "environment": case.get("environment"),
                "baseline_success_rate": baseline_success,
                "candidate_success_rate": candidate_success,
                "success_delta": candidate_success - baseline_success,
                "baseline_cost": baseline_cost,
                "candidate_cost": candidate_cost,
                "cost_ratio": cost_ratio,
                "success_per_cost": success_per_cost,
                "transfer_passed": transfer_passed,
                "efficiency_passed": efficient,
            }
            transfer_matrix.append(row)
            if not transfer_passed:
                transfer_failures.append(row)
            if not efficient:
                efficiency_failures.append(row)
        regression_matrix = []
        regression_failures = []
        for case in regression_cases:
            case_id = str(case.get("case_id", ""))
            baseline_success = float(case.get("baseline_success_rate", 0.0))
            candidate_success = float(case.get("candidate_success_rate", 0.0))
            safety_passed = bool(case.get("safety_passed", True))
            regression_free = candidate_success >= baseline_success and safety_passed
            row = {
                "case_id": case_id,
                "organ": case.get("organ"),
                "baseline_success_rate": baseline_success,
                "candidate_success_rate": candidate_success,
                "success_delta": candidate_success - baseline_success,
                "safety_passed": safety_passed,
                "regression_free": regression_free,
            }
            regression_matrix.append(row)
            if not regression_free:
                regression_failures.append(row)
        zero_key_regressions = len(regression_failures) == 0
        hidden_best_only_result = any(
            row["transfer_passed"] for row in transfer_matrix
        ) and bool(transfer_failures)
        efficiency_summary = {
            "average_success_per_cost": (
                sum(success_per_cost_values) / len(success_per_cost_values)
            ),
            "max_cost_ratio": max(cost_ratio_values),
            "thresholds": efficiency_thresholds,
            "efficiency_failure_count": len(efficiency_failures),
        }
        if regression_failures:
            decision = "REJECT_REGRESSION"
        elif transfer_failures:
            decision = "PARTIAL_CANARY_ONLY"
        elif efficiency_failures:
            decision = "REJECT_EFFICIENCY"
        else:
            decision = "TRANSFER_AUDIT_PASSED"
        if hidden_best_only_result and decision == "PARTIAL_CANARY_ONLY":
            owner_exception_required = True
        else:
            owner_exception_required = decision.startswith("REJECT")
        receipt = {
            "receipt_type": "TRANSFER_REGRESSION_EFFICIENCY_AUDIT",
            "status": decision,
            "audit_id": new_id("transfer_audit"),
            "capability_id": capability_id,
            "reason": reason,
            "transfer_matrix": transfer_matrix,
            "regression_matrix": regression_matrix,
            "efficiency_summary": efficiency_summary,
            "transfer_failures": transfer_failures,
            "regression_failures": regression_failures,
            "efficiency_failures": efficiency_failures,
            "zero_key_regressions": zero_key_regressions,
            "hidden_best_only_result_detected": hidden_best_only_result,
            "owner_exception_required": owner_exception_required,
            "promotion_executed": False,
            "partial_promotion_executed": False,
            "canonical_state_mutated": False,
            "claim_ceiling": (
                "transfer/regression/efficiency audit receipt only; no promotion, "
                "partial absorption, approval exception, or canonical mutation is inferred"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.transfer_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("transfer_audit_receipts", updated, connection)
            self.ledger.append("transfer_efficiency_audit_recorded", receipt, connection)
        return receipt

    def draft_promotion_bundle(
        self,
        *,
        capability_ids: list[str],
        patch: dict[str, Any],
        skill_refs: list[str],
        epoch_id: str,
        budget: dict[str, Any],
        limits: dict[str, Any],
        rollback_assets: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if not capability_ids or not reason.strip():
            raise ValueError("promotion bundle requires capability_ids and reason")
        bundle = {
            "bundle_id": new_id("promotion_bundle"),
            "capability_ids": capability_ids,
            "patch": patch,
            "skill_refs": skill_refs,
            "epoch_id": epoch_id,
            "budget": budget,
            "limits": limits,
            "rollback_assets": rollback_assets,
            "created_at": utc_now(),
        }
        bundle["bundle_digest"] = digest_json(bundle)
        receipt = {
            "receipt_type": "PROMOTION_BUNDLE_DRAFTED",
            "status": "BUNDLE_DRAFTED",
            "bundle_id": bundle["bundle_id"],
            "bundle_digest": bundle["bundle_digest"],
            "bundle": bundle,
            "reason": reason,
            "canonical_state_mutated": False,
            "promotion_executed": False,
            "partial_absorption_only": True,
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_promotion_bundle_receipt("promotion_bundle_drafted", receipt)
        return receipt

    def bind_owner_promotion_approval(
        self,
        *,
        bundle_id: str,
        bundle_digest: str,
        actor: str,
        scope: list[str],
        reason: str,
    ) -> dict[str, Any]:
        bundle = self._find_promotion_bundle(bundle_id)
        if bundle is None:
            raise KeyError(f"unknown promotion bundle: {bundle_id}")
        if bundle.get("bundle_digest") != bundle_digest:
            raise PermissionError("promotion approval digest mismatch")
        allowed = set(bundle["bundle"]["capability_ids"])
        requested = set(scope)
        if not requested or not requested <= allowed:
            raise PermissionError("promotion approval scope must be a bundle subset")
        receipt = {
            "receipt_type": "PROMOTION_OWNER_APPROVAL_BOUND",
            "status": "OWNER_APPROVAL_BOUND",
            "bundle_id": bundle_id,
            "bundle_digest": bundle_digest,
            "actor": actor,
            "scope": sorted(requested),
            "reason": reason,
            "approval_bound_at": utc_now(),
            "canonical_state_mutated": False,
            "promotion_executed": False,
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_promotion_bundle_receipt("promotion_owner_approval_bound", receipt)
        return receipt

    def prepare_promotion_canary(
        self, *, bundle_id: str, scope: list[str], reason: str
    ) -> dict[str, Any]:
        approval = self._find_promotion_approval(bundle_id)
        if approval is None:
            raise PermissionError("promotion canary requires owner approval")
        if not set(scope) <= set(approval["scope"]):
            raise PermissionError("promotion canary scope exceeds owner approval")
        receipt = {
            "receipt_type": "PROMOTION_CANARY_PREPARED",
            "status": "CANARY_PREPARED",
            "bundle_id": bundle_id,
            "scope": scope,
            "reason": reason,
            "post_promotion_monitor_required": True,
            "canonical_state_mutated": False,
            "promotion_executed": False,
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_promotion_bundle_receipt("promotion_canary_prepared", receipt)
        return receipt

    def verify_promotion_rollback(
        self, *, bundle_id: str, reason: str
    ) -> dict[str, Any]:
        bundle = self._find_promotion_bundle(bundle_id)
        if bundle is None:
            raise KeyError(f"unknown promotion bundle: {bundle_id}")
        assets = dict(bundle["bundle"].get("rollback_assets", {}))
        required = {"code", "db", "config", "skill"}
        missing = sorted(required - set(assets))
        receipt = {
            "receipt_type": "PROMOTION_ROLLBACK_VERIFIED",
            "status": "ROLLBACK_VERIFIED" if not missing else "ROLLBACK_INCOMPLETE",
            "bundle_id": bundle_id,
            "reason": reason,
            "rollback_assets": assets,
            "missing_assets": missing,
            "canonical_state_mutated": False,
            "promotion_executed": False,
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_promotion_bundle_receipt("promotion_rollback_verified", receipt)
        return receipt

    def _record_promotion_bundle_receipt(
        self, event_type: str, receipt: dict[str, Any]
    ) -> None:
        current = self.promotion_bundle_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("promotion_bundle_receipts", updated, connection)
            self.ledger.append(event_type, receipt, connection)

    def _find_promotion_bundle(self, bundle_id: str) -> dict[str, Any] | None:
        for receipt in self.promotion_bundle_receipts(limit=100):
            if (
                receipt.get("receipt_type") == "PROMOTION_BUNDLE_DRAFTED"
                and receipt.get("bundle_id") == bundle_id
            ):
                return receipt
        return None

    def _find_promotion_approval(self, bundle_id: str) -> dict[str, Any] | None:
        for receipt in self.promotion_bundle_receipts(limit=100):
            if (
                receipt.get("receipt_type") == "PROMOTION_OWNER_APPROVAL_BOUND"
                and receipt.get("bundle_id") == bundle_id
            ):
                return receipt
        return None

    def freeze_holdout_epoch(
        self,
        *,
        evaluator: dict[str, Any],
        holdout_manifest: dict[str, Any],
        thresholds: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("holdout epoch reason is required")
        epoch = {
            "epoch_id": new_id("holdout_epoch"),
            "evaluator": evaluator,
            "holdout_manifest": holdout_manifest,
            "thresholds": thresholds,
            "evaluator_digest": digest_json(evaluator),
            "holdout_digest": digest_json(holdout_manifest),
            "threshold_digest": digest_json(thresholds),
            "candidate_workspace_write_allowed": False,
            "created_at": utc_now(),
        }
        epoch["epoch_digest"] = digest_json(epoch)
        receipt = {
            "receipt_type": "HOLDOUT_EPOCH_FROZEN",
            "status": "EPOCH_FROZEN",
            "epoch_id": epoch["epoch_id"],
            "reason": reason,
            "epoch": epoch,
            "holdout_write_allowed": False,
            "threshold_mutation_allowed": False,
            "evaluator_mutation_allowed": False,
            "promotion_executed": False,
            "second_authority_created": False,
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_holdout_epoch_receipt("holdout_epoch_frozen", receipt)
        return receipt

    def run_holdout_epoch(
        self,
        *,
        epoch_id: str,
        evaluator: dict[str, Any],
        holdout_manifest: dict[str, Any],
        thresholds: dict[str, Any],
        candidate_results: list[dict[str, Any]],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("holdout run reason is required")
        frozen = self._find_holdout_epoch(epoch_id)
        if frozen is None:
            raise KeyError(f"unknown holdout epoch: {epoch_id}")
        epoch = frozen["epoch"]
        observed = {
            "evaluator_digest": digest_json(evaluator),
            "holdout_digest": digest_json(holdout_manifest),
            "threshold_digest": digest_json(thresholds),
        }
        mismatches = {
            key: {"expected": epoch[key], "actual": actual}
            for key, actual in observed.items()
            if epoch[key] != actual
        }
        pass_count = sum(1 for item in candidate_results if item.get("passed") is True)
        total = len(candidate_results)
        pass_rate = pass_count / total if total else 0.0
        required = float(thresholds.get("min_pass_rate", 1.0))
        receipt = {
            "receipt_type": "HOLDOUT_EPOCH_RUN",
            "status": "INVALID_EPOCH"
            if mismatches
            else "HOLDOUT_PASSED"
            if pass_rate >= required
            else "HOLDOUT_FAILED",
            "epoch_id": epoch_id,
            "reason": reason,
            "observed_digests": observed,
            "mismatches": mismatches,
            "requires_rebaseline": bool(mismatches),
            "candidate_results": candidate_results,
            "pass_rate": pass_rate,
            "required_pass_rate": required,
            "holdout_write_allowed": False,
            "threshold_mutated": False,
            "evaluator_mutated": False,
            "promotion_executed": False,
            "approval_executed": False,
            "second_authority_created": False,
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_holdout_epoch_receipt("holdout_epoch_run_recorded", receipt)
        return receipt

    def _record_holdout_epoch_receipt(
        self, event_type: str, receipt: dict[str, Any]
    ) -> None:
        current = self.holdout_epoch_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("holdout_epoch_receipts", updated, connection)
            self.ledger.append(event_type, receipt, connection)

    def _find_holdout_epoch(self, epoch_id: str) -> dict[str, Any] | None:
        for receipt in self.holdout_epoch_receipts(limit=100):
            if (
                receipt.get("receipt_type") == "HOLDOUT_EPOCH_FROZEN"
                and receipt.get("epoch_id") == epoch_id
            ):
                return receipt
        return None

    def run_paired_candidate_experiment(
        self,
        *,
        preregistration: dict[str, Any],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        cases: list[dict[str, Any]],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("paired experiment reason is required")
        if not baseline:
            raise ValueError("paired experiment requires a comparable baseline")
        if not candidate:
            raise ValueError("paired experiment requires a candidate")
        if not cases:
            raise ValueError("paired experiment requires at least one case")
        experiment_id = new_id("paired_experiment")
        condition_keys = ["fixture_digest", "environment_digest", "budget", "evaluator_digest"]
        invalid_reasons: list[str] = []
        case_results: list[dict[str, Any]] = []
        baseline_passes = 0
        candidate_passes = 0
        baseline_cost = 0.0
        candidate_cost = 0.0
        failure_spectrum: dict[str, int] = {}
        for case in cases:
            case_id = str(case.get("case_id", ""))
            baseline_case = dict(case.get("baseline", {}))
            candidate_case = dict(case.get("candidate", {}))
            for key in condition_keys:
                if baseline_case.get(key) != candidate_case.get(key):
                    invalid_reasons.append(f"{case_id}:{key}")
            expected = case.get("expected_output")
            baseline_passed = baseline_case.get("output") == expected
            candidate_passed = candidate_case.get("output") == expected
            baseline_passes += int(baseline_passed)
            candidate_passes += int(candidate_passed)
            baseline_cost += float(baseline_case.get("cost", 0.0))
            candidate_cost += float(candidate_case.get("cost", 0.0))
            for side, passed, sample in (
                ("baseline", baseline_passed, baseline_case),
                ("candidate", candidate_passed, candidate_case),
            ):
                if not passed:
                    failure_class = str(sample.get("failure_class", "wrong_output"))
                    failure_spectrum[f"{side}:{failure_class}"] = (
                        failure_spectrum.get(f"{side}:{failure_class}", 0) + 1
                    )
            case_results.append(
                {
                    "case_id": case_id,
                    "fixture_digest": baseline_case.get("fixture_digest"),
                    "baseline_passed": baseline_passed,
                    "candidate_passed": candidate_passed,
                    "baseline_cost": float(baseline_case.get("cost", 0.0)),
                    "candidate_cost": float(candidate_case.get("cost", 0.0)),
                    "negative_sample_retained": not candidate_passed,
                    "failure_sample_retained": not baseline_passed or not candidate_passed,
                }
            )
        case_count = len(cases)
        baseline_rate = baseline_passes / case_count
        candidate_rate = candidate_passes / case_count
        valid_conditions = not invalid_reasons
        repetitions = {
            str(case.get("case_id", "")): sum(
                1 for item in cases if item.get("case_id") == case.get("case_id")
            )
            for case in cases
        }
        stability_summary = {
            "case_count": case_count,
            "repeated_case_count": sum(1 for count in repetitions.values() if count > 1),
            "all_cases_repeated": all(count > 1 for count in repetitions.values()),
            "single_success_claim_rejected": case_count < 2,
        }
        candidate_validated = (
            valid_conditions
            and candidate_rate > baseline_rate
            and case_count >= 2
            and not stability_summary["single_success_claim_rejected"]
        )
        report = {
            "experiment_id": experiment_id,
            "preregistration": preregistration,
            "preregistration_digest": digest_json(preregistration),
            "baseline_digest": digest_json(baseline),
            "candidate_digest": digest_json(candidate),
            "candidate_diff_digest": digest_json(candidate.get("diff", {})),
            "expected_effect": preregistration.get("expected_effect"),
            "condition_lock": {
                "model": preregistration.get("model"),
                "harness": preregistration.get("harness"),
                "environment": preregistration.get("environment"),
                "budget": preregistration.get("budget"),
                "evaluator": preregistration.get("evaluator"),
            },
            "case_results": case_results,
            "completion": {
                "baseline_success_rate": baseline_rate,
                "candidate_success_rate": candidate_rate,
                "delta": candidate_rate - baseline_rate,
            },
            "process_quality": {
                "valid_conditions": valid_conditions,
                "invalid_reasons": invalid_reasons,
                "negative_samples_retained": any(
                    item["negative_sample_retained"] for item in case_results
                ),
                "failure_samples_retained": any(
                    item["failure_sample_retained"] for item in case_results
                ),
            },
            "cost": {
                "baseline_total": baseline_cost,
                "candidate_total": candidate_cost,
                "delta": candidate_cost - baseline_cost,
            },
            "failure_spectrum": failure_spectrum,
            "stability_summary": stability_summary,
        }
        receipt = {
            "receipt_type": "PAIRED_BASELINE_CANDIDATE_EXPERIMENT",
            "status": "CANDIDATE_VALIDATED"
            if candidate_validated
            else "INVALID_CONDITIONS"
            if not valid_conditions
            else "CANDIDATE_NOT_VALIDATED",
            "experiment_id": experiment_id,
            "reason": reason,
            "report": report,
            "candidate_validated": candidate_validated,
            "promotion_executed": False,
            "absorption_executed": False,
            "evaluator_mutated": False,
            "holdout_mutated": False,
            "thresholds_mutated": False,
            "approval_executed": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "paired baseline-candidate experiment receipt only; no promotion, "
                "absorption, evaluator change, holdout change, or approval is inferred"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.paired_experiment_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("paired_experiment_receipts", updated, connection)
            self.ledger.append("paired_candidate_experiment_recorded", receipt, connection)
        return receipt

    def propose_skill_candidates_from_receipts(
        self, *, minimum_repeats: int = 3, reason: str
    ) -> dict[str, Any]:
        created_ids = self.skills.propose_from_action_sequences(
            minimum_repeats=minimum_repeats
        )
        rows = []
        for skill_id in created_ids:
            row = self.db.query_one(
                "SELECT skill_id,name,status,definition_json FROM skills WHERE skill_id=?",
                (skill_id,),
            )
            if row is not None:
                definition = json.loads(str(row["definition_json"]))
                rows.append(
                    {
                        "skill_id": row["skill_id"],
                        "name": row["name"],
                        "status": row["status"],
                        "risk": definition.get("risk"),
                        "source_episode_ids": definition.get("source_episode_ids", []),
                    }
                )
        receipt = {
            "receipt_type": "SKILL_CANDIDATE_EXTRACTION",
            "status": "CANDIDATES_PROPOSED" if rows else "NO_CANDIDATES",
            "created_skill_ids": created_ids,
            "candidate_count": len(rows),
            "candidates": rows,
            "minimum_repeats": minimum_repeats,
            "reason": reason,
            "promotion_executed": False,
            "approval_executed": False,
            "sandbox_executed": False,
            "candidate_only": True,
            "claim_ceiling": "proposed skill candidates only; no sandbox, approval, promotion, or rollback",
            "created_at": utc_now(),
        }
        current = self.skill_candidate_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("skill_candidate_receipts", updated, connection)
            self.ledger.append("skill_candidates_extracted", receipt, connection)
        return receipt

    def start_skill_sandbox_validation(
        self, *, skill_id: str, reason: str
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT skill_id,version,status,definition_json FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        if row is None:
            raise KeyError(f"unknown skill: {skill_id}")
        if str(row["status"]) != CandidateStatus.PROPOSED.value:
            raise ValueError("skill sandbox validation requires a PROPOSED skill")
        definition = json.loads(str(row["definition_json"]))
        experiment_id = new_id("skill_exp")
        artifact_dir = self.config.sandbox_path / "skill-experiments" / experiment_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        candidate_cases = [
            {
                "case_id": f"source-{index + 1}",
                "source_episode_id": source_id,
            }
            for index, source_id in enumerate(
                definition.get("source_episode_ids", [])
            )
        ]
        manifest = {
            "experiment_id": experiment_id,
            "skill_id": skill_id,
            "skill_version": int(row["version"]),
            "definition_sha256": digest_json(definition),
            "reason": reason,
            "created_at": utc_now(),
            "mode": "sandbox_candidate_only",
            "candidate_cases": candidate_cases,
            "approval_executed": False,
            "promotion_executed": False,
            "deployment_executed": False,
        }
        baseline = {
            "source_episode_count": len(definition.get("source_episode_ids", [])),
            "skill_status_before": row["status"],
            "risk": definition.get("risk"),
        }
        manifest_sha256 = digest_json(manifest)
        manifest_path = artifact_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO skill_experiments(
                    experiment_id,skill_id,skill_version,status,manifest_json,
                    baseline_json,result_json,artifact_path,started_at,finished_at
                ) VALUES (?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    experiment_id,
                    skill_id,
                    int(row["version"]),
                    "RUNNING",
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                    None,
                    str(artifact_dir),
                    utc_now(),
                ),
            )
            self.ledger.append(
                "skill_sandbox_validation_started",
                {
                    "experiment_id": experiment_id,
                    "skill_id": skill_id,
                    "manifest_sha256": manifest_sha256,
                    "candidate_only": True,
                },
                connection,
            )
        self.skills.transition(
            skill_id,
            CandidateStatus.SANDBOXED,
            {"experiment_id": experiment_id, "manifest_sha256": manifest_sha256},
        )
        receipt = {
            "receipt_type": "SKILL_SANDBOX_VALIDATION",
            "status": "SANDBOX_STARTED",
            "skill_id": skill_id,
            "experiment_id": experiment_id,
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_sha256,
            "candidate_case_count": len(candidate_cases),
            "skill_status_before": row["status"],
            "skill_status_after": CandidateStatus.SANDBOXED.value,
            "candidate_only": True,
            "validation_passed": False,
            "approval_executed": False,
            "promotion_executed": False,
            "deployment_executed": False,
            "claim_ceiling": "skill sandbox started only; no validation pass, approval, promotion, active skill, or deployment",
            "created_at": utc_now(),
        }
        current = self.skill_sandbox_receipts(limit=100)
        updated = [
            receipt,
            *[item for item in current if item.get("skill_id") != skill_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("skill_sandbox_receipts", updated, connection)
            self.ledger.append("skill_sandbox_receipt_recorded", receipt, connection)
        return receipt

    def run_sandbox_adapter_probe(
        self,
        *,
        adapter_id: str,
        tool: str,
        arguments: dict[str, Any],
        purpose: str,
        allowed_tools: list[str],
        reason: str,
        network_enabled: bool = False,
        secret_injection: str = "none",
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("sandbox adapter probe reason is required")
        probe_id = new_id("sandbox_probe")
        sandbox_root = self.config.sandbox_path / "sandbox-adapters" / probe_id
        contract = SandboxContract(
            adapter_id=adapter_id,
            sandbox_root=str(sandbox_root),
            allowed_tools=list(allowed_tools),
            network_enabled=network_enabled,
            secret_injection=secret_injection,
        )
        adapter = SandboxAdapter(contract)
        receipt = adapter.run_probe(
            tool=tool,
            arguments=arguments,
            purpose=purpose,
            risk=RiskLevel.READ,
        )
        receipt.update(
            {
                "receipt_type": "SANDBOX_ADAPTER_PROBE",
                "probe_id": probe_id,
                "reason": reason,
                "created_at": utc_now(),
            }
        )
        current = self.sandbox_adapter_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("sandbox_adapter_receipts", updated, connection)
            self.ledger.append("sandbox_adapter_probe_recorded", receipt, connection)
        return receipt

    def review_learning_epoch_from_receipts(
        self,
        *,
        reason: str,
        minimum_repeats: int = 3,
        allow_candidate_extraction: bool = False,
        owner_authorization: str | None = None,
    ) -> dict[str, Any]:
        before_candidates = self.db.query_one("SELECT COUNT(*) AS count FROM skills")
        before_active = len(self.skills.active())
        frozen_snapshot = {
            "mode": "learning_frozen",
            "candidate_extraction_executed": False,
            "skill_count": int(before_candidates["count"])
            if before_candidates is not None
            else 0,
            "active_skill_count": before_active,
        }
        candidate_receipt = None
        if allow_candidate_extraction:
            if not owner_authorization:
                raise PermissionError(
                    "candidate learning epoch review requires owner authorization"
                )
            candidate_receipt = self.propose_skill_candidates_from_receipts(
                minimum_repeats=minimum_repeats,
                reason=reason,
            )
        after_candidates = self.db.query_one("SELECT COUNT(*) AS count FROM skills")
        after_active = len(self.skills.active())
        receipt = {
            "receipt_type": "LEARNING_EPOCH_REVIEW",
            "status": "REVIEW_RECORDED",
            "reason": reason,
            "owner_authorization": owner_authorization,
            "minimum_repeats": minimum_repeats,
            "learning_modes": [
                frozen_snapshot,
                {
                    "mode": "candidate_only",
                    "candidate_extraction_executed": candidate_receipt is not None,
                    "candidate_count": candidate_receipt.get("candidate_count", 0)
                    if isinstance(candidate_receipt, dict)
                    else 0,
                    "created_skill_ids": candidate_receipt.get("created_skill_ids", [])
                    if isinstance(candidate_receipt, dict)
                    else [],
                },
            ],
            "skill_count_before": frozen_snapshot["skill_count"],
            "skill_count_after": int(after_candidates["count"])
            if after_candidates is not None
            else frozen_snapshot["skill_count"],
            "active_skill_count_before": before_active,
            "active_skill_count_after": after_active,
            "promotion_executed": False,
            "approval_executed": False,
            "sandbox_executed": False,
            "candidate_only": True,
            "claim_ceiling": "learning mode review only; no autonomous promotion or live deployment",
            "created_at": utc_now(),
        }
        current = self.learning_epoch_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("learning_epoch_receipts", updated, connection)
            self.ledger.append("learning_epoch_reviewed", receipt, connection)
        return receipt

    def record_capability_epoch_audit(
        self, *, reason: str, completed_passes: list[str]
    ) -> dict[str, Any]:
        capability_summary = self.capabilities.summary()
        receipts = {
            "read_only_execution": len(self.read_only_execution_receipts(limit=100)),
            "projection_review": len(self.read_only_projection_reviews(limit=100)),
            "provider_route": len(self.provider_route_receipts(limit=100)),
            "skill_candidate": len(self.skill_candidate_receipts(limit=100)),
            "learning_epoch": len(self.learning_epoch_receipts(limit=100)),
        }
        second_authority_admitted = any(
            item.get("declares_authority")
            for item in capability_summary.get("capabilities", [])
            if isinstance(item, dict)
        )
        active_skill_count = len(self.skills.active())
        receipt = {
            "receipt_type": "CAPABILITY_EPOCH_AUDIT",
            "status": "AUDIT_RECORDED",
            "reason": reason,
            "completed_passes": completed_passes,
            "highest_pass": completed_passes[-1] if completed_passes else None,
            "receipt_counts": receipts,
            "capability_state": {
                "second_authority_admitted": second_authority_admitted,
                "active_skill_count": active_skill_count,
                "skill_promotion_executed": False,
                "live_deployment_executed": False,
                "external_system_modified": False,
            },
            "phase2_admission_decision": {
                "status": "ADMIT_LOW_RISK_PREPARATION_ONLY",
                "owner_gate_required_for": [
                    "Skill promotion",
                    "live deployment",
                    "external write",
                    "long-running daemon",
                ],
            },
            "allowed_conclusion": "FUNCTIONAL_RUNTIME_ONLY",
            "claim_ceiling": "repository runtime admission evidence only; no live or production readiness claim",
            "created_at": utc_now(),
        }
        current = self.capability_epoch_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("capability_epoch_audit_receipts", updated, connection)
            self.ledger.append("capability_epoch_audited", receipt, connection)
        return receipt

    def intake_voice_transcript(
        self,
        *,
        transcript_id: str,
        speaker_id: str,
        transcript: str,
        locale: str = "und",
        confidence: float | None = None,
        source: str = "local_transcript",
    ) -> dict[str, Any]:
        if not transcript_id.strip():
            raise ValueError("transcript_id is required")
        if not transcript.strip():
            raise ValueError("voice transcript cannot be empty")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("voice transcript confidence must be within [0, 1]")
        message = ChannelMessage(
            channel="voice",
            sender_id=speaker_id,
            content=transcript,
            message_id=transcript_id,
            metadata={
                "locale": locale,
                "confidence": confidence,
                "source": source,
                "audio_captured": False,
                "stt_executed": False,
            },
        )
        event_id, inserted = self.channel_gateway.submit(message, self.events)
        receipt = {
            "receipt_type": "VOICE_TRANSCRIPT_INGRESS",
            "status": "QUEUED_EVENT_ONLY",
            "transcript_id": transcript_id,
            "event_id": event_id,
            "inserted": inserted,
            "source": source,
            "locale": locale,
            "confidence": confidence,
            "content_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
            "audio_captured": False,
            "stt_executed": False,
            "creates_goal": False,
            "creates_action": False,
            "direct_tool_execution": False,
            "writes_canonical_state": False,
            "allowed_next_authority": "EventStore",
            "claim_ceiling": "voice transcript queued as channel Event only; no audio capture, STT, planning, or action execution",
            "created_at": utc_now(),
        }
        current = self.voice_transcript_receipts(limit=100)
        updated = [
            receipt,
            *[row for row in current if row.get("transcript_id") != transcript_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("voice_transcript_receipts", updated, connection)
            self.ledger.append("voice_transcript_queued", receipt, connection)
        return receipt

    def draft_local_notification(
        self,
        *,
        channel: str,
        body: str,
        purpose: str,
        mode: str = "text",
    ) -> dict[str, Any]:
        if channel not in {"owner_console", "wechat", "voice"}:
            raise ValueError("unsupported notification channel")
        if mode not in {"text", "speech_script"}:
            raise ValueError("unsupported notification mode")
        if not body.strip():
            raise ValueError("notification body cannot be empty")
        draft_id = new_id("notification")
        payload = {
            "draft_id": draft_id,
            "channel": channel,
            "mode": mode,
            "purpose": purpose,
            "body": body,
            "delivery_executed": False,
            "tts_executed": False,
            "audio_played": False,
            "created_at": utc_now(),
        }
        target = self.config.outbox_path / f"{draft_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        receipt = {
            "receipt_type": "LOCAL_NOTIFICATION_DRAFT",
            "status": "DRAFT_WRITTEN",
            "draft_id": draft_id,
            "channel": channel,
            "mode": mode,
            "purpose": purpose,
            "outbox_path": str(target),
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "delivery_executed": False,
            "tts_executed": False,
            "audio_played": False,
            "external_send_executed": False,
            "creates_goal": False,
            "creates_action": False,
            "direct_tool_execution": False,
            "claim_ceiling": "local outbox draft only; no external delivery, TTS, playback, planning, or action execution",
            "created_at": payload["created_at"],
        }
        current = self.notification_draft_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("notification_draft_receipts", updated, connection)
            self.ledger.append("local_notification_drafted", receipt, connection)
        return receipt

    def intake_screen_snapshot_asset(
        self,
        *,
        snapshot_id: str,
        path: str | Path,
        source: str = "local_screen_capture",
        purpose: str = "screen context",
    ) -> dict[str, Any]:
        if not snapshot_id.strip():
            raise ValueError("snapshot_id is required")
        snapshot_path = Path(path).expanduser().resolve()
        if not snapshot_path.is_file():
            raise ValueError("screen snapshot requires an existing file")
        data = snapshot_path.read_bytes()
        content_sha256 = hashlib.sha256(data).hexdigest()
        message = ChannelMessage(
            channel="screen",
            sender_id="local_host",
            content=f"screen snapshot asset {snapshot_id}",
            message_id=snapshot_id,
            metadata={
                "path": str(snapshot_path),
                "sha256": content_sha256,
                "size_bytes": len(data),
                "source": source,
                "purpose": purpose,
                "ocr_executed": False,
                "ui_control_executed": False,
            },
        )
        event_id, inserted = self.channel_gateway.submit(message, self.events)
        receipt = {
            "receipt_type": "SCREEN_SNAPSHOT_INGRESS",
            "status": "QUEUED_EVENT_ONLY",
            "snapshot_id": snapshot_id,
            "event_id": event_id,
            "inserted": inserted,
            "path": str(snapshot_path),
            "size_bytes": len(data),
            "sha256": content_sha256,
            "source": source,
            "purpose": purpose,
            "ocr_executed": False,
            "ui_control_executed": False,
            "external_upload_executed": False,
            "creates_goal": False,
            "creates_action": False,
            "direct_tool_execution": False,
            "claim_ceiling": "screen snapshot queued as local asset Event only; no OCR, UI control, upload, planning, or action execution",
            "created_at": utc_now(),
        }
        current = self.screen_snapshot_receipts(limit=100)
        updated = [
            receipt,
            *[row for row in current if row.get("snapshot_id") != snapshot_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("screen_snapshot_receipts", updated, connection)
            self.ledger.append("screen_snapshot_queued", receipt, connection)
        return receipt

    def draft_browser_form_submission(
        self,
        *,
        form_id: str,
        url: str,
        fields: dict[str, str],
        purpose: str,
    ) -> dict[str, Any]:
        if not form_id.strip():
            raise ValueError("form_id is required")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("browser form draft requires an http(s) URL")
        host = parsed.hostname or ""
        if parsed.scheme != "https" and host not in {"127.0.0.1", "localhost"}:
            raise PermissionError("browser form draft requires HTTPS or loopback HTTP")
        if not fields:
            raise ValueError("browser form draft requires fields")
        field_names = sorted(fields)
        field_digest = hashlib.sha256(
            json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        receipt = {
            "receipt_type": "BROWSER_FORM_DRAFT",
            "status": "DRAFT_RECORDED",
            "form_id": form_id,
            "url": url,
            "scheme": parsed.scheme,
            "host": host,
            "field_names": field_names,
            "field_count": len(field_names),
            "field_digest": field_digest,
            "purpose": purpose,
            "browser_opened": False,
            "form_submitted": False,
            "network_post_executed": False,
            "download_executed": False,
            "creates_goal": False,
            "creates_action": False,
            "direct_tool_execution": False,
            "approval_required_for_submission": True,
            "claim_ceiling": "browser form draft only; no browser session, POST, submit, download, planning execution, or action execution",
            "created_at": utc_now(),
        }
        current = self.browser_form_draft_receipts(limit=100)
        updated = [
            receipt,
            *[row for row in current if row.get("form_id") != form_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("browser_form_draft_receipts", updated, connection)
            self.ledger.append("browser_form_drafted", receipt, connection)
        return receipt

    def draft_download_quarantine(
        self,
        *,
        download_id: str,
        url: str,
        filename: str,
        purpose: str,
    ) -> dict[str, Any]:
        if not download_id.strip():
            raise ValueError("download_id is required")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("download quarantine draft requires an http(s) URL")
        host = parsed.hostname or ""
        if parsed.scheme != "https" and host not in {"127.0.0.1", "localhost"}:
            raise PermissionError(
                "download quarantine draft requires HTTPS or loopback HTTP"
            )
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("download quarantine draft requires a safe filename")
        quarantine_dir = self.config.sandbox_path / "download-quarantine" / download_id
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        target_path = quarantine_dir / safe_name
        manifest = {
            "download_id": download_id,
            "url": url,
            "scheme": parsed.scheme,
            "host": host,
            "filename": safe_name,
            "target_path": str(target_path),
            "purpose": purpose,
            "network_fetch_executed": False,
            "file_materialized": False,
            "external_write_executed": False,
            "created_at": utc_now(),
        }
        manifest_path = quarantine_dir / "manifest.json"
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, manifest_path)
        receipt = {
            "receipt_type": "DOWNLOAD_QUARANTINE_DRAFT",
            "status": "QUARANTINE_DRAFT_RECORDED",
            "download_id": download_id,
            "url": url,
            "scheme": parsed.scheme,
            "host": host,
            "filename": safe_name,
            "manifest_path": str(manifest_path),
            "target_path": str(target_path),
            "manifest_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
            "network_fetch_executed": False,
            "file_materialized": False,
            "external_write_executed": False,
            "creates_goal": False,
            "creates_action": False,
            "direct_tool_execution": False,
            "approval_required_for_fetch": True,
            "claim_ceiling": "download quarantine draft only; no network fetch, file materialization, external write, planning execution, or action execution",
            "created_at": manifest["created_at"],
        }
        current = self.download_quarantine_receipts(limit=100)
        updated = [
            receipt,
            *[row for row in current if row.get("download_id") != download_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("download_quarantine_receipts", updated, connection)
            self.ledger.append("download_quarantine_drafted", receipt, connection)
        return receipt

    def intake_document_asset(
        self,
        *,
        document_id: str,
        path: str | Path,
        source: str = "local_document",
        purpose: str = "document context",
        max_bytes: int = 25 * 1024 * 1024,
    ) -> dict[str, Any]:
        if not document_id.strip():
            raise ValueError("document_id is required")
        document_path = Path(path).expanduser().resolve()
        if not document_path.is_file():
            raise ValueError("document ingress requires an existing file")
        stat = document_path.stat()
        if stat.st_size > max_bytes:
            raise ValueError("document exceeds max_bytes")
        data = document_path.read_bytes()
        content_sha256 = hashlib.sha256(data).hexdigest()
        mime_type, encoding = mimetypes.guess_type(document_path.name)
        message = ChannelMessage(
            channel="document",
            sender_id="local_host",
            content=f"document asset {document_id}",
            message_id=document_id,
            metadata={
                "path": str(document_path),
                "sha256": content_sha256,
                "size_bytes": len(data),
                "mime_type": mime_type or "application/octet-stream",
                "encoding": encoding,
                "source": source,
                "purpose": purpose,
                "text_extracted": False,
                "vector_indexed": False,
            },
        )
        event_id, inserted = self.channel_gateway.submit(message, self.events)
        receipt = {
            "receipt_type": "DOCUMENT_INGRESS",
            "status": "QUEUED_EVENT_ONLY",
            "document_id": document_id,
            "event_id": event_id,
            "inserted": inserted,
            "path": str(document_path),
            "name": document_path.name,
            "size_bytes": len(data),
            "sha256": content_sha256,
            "mime_type": mime_type or "application/octet-stream",
            "encoding": encoding,
            "source": source,
            "purpose": purpose,
            "text_extracted": False,
            "ocr_executed": False,
            "vector_indexed": False,
            "external_upload_executed": False,
            "creates_goal": False,
            "creates_action": False,
            "direct_tool_execution": False,
            "claim_ceiling": "document asset queued as local Event only; no parsing, OCR, vector index, upload, planning, or action execution",
            "created_at": utc_now(),
        }
        current = self.document_ingress_receipts(limit=100)
        updated = [
            receipt,
            *[row for row in current if row.get("document_id") != document_id],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("document_ingress_receipts", updated, connection)
            self.ledger.append("document_asset_queued", receipt, connection)
        return receipt

    def prepare_document_retrieval_preview(
        self,
        *,
        document_id: str,
        request_id: str,
        owner_intent: str,
        max_bytes: int = 524288,
    ) -> dict[str, Any]:
        receipt = next(
            (
                item
                for item in self.document_ingress_receipts(limit=100)
                if item.get("document_id") == document_id
            ),
            None,
        )
        if receipt is None:
            raise KeyError(f"unknown document ingress receipt: {document_id}")
        path = str(receipt.get("path", ""))
        if not path:
            raise ValueError("document ingress receipt has no path")
        result = self.intake_read_only_task(
            ReadOnlyTaskRequest(
                request_id=request_id,
                organ_id="document",
                owner_intent=owner_intent,
                source="document_ingress",
                inputs={
                    "document_id": document_id,
                    "path": path,
                    "file_path": path,
                    "asset_path": path,
                    "sha256": receipt.get("sha256"),
                    "mime_type": receipt.get("mime_type"),
                    "max_bytes": max_bytes,
                },
                evidence_required=[
                    "document_ingress_receipt",
                    "document_sha256",
                    "event_receipt",
                ],
            )
        )
        return {
            "receipt_type": "DOCUMENT_RETRIEVAL_PREVIEW",
            "status": "PREVIEW_ONLY",
            "document_id": document_id,
            "request_id": request_id,
            "source_receipt_sha256": receipt.get("sha256"),
            "read_only_task": result,
            "creates_plan": False,
            "creates_action": False,
            "direct_tool_execution": False,
            "text_extracted": False,
            "ocr_executed": False,
            "vector_indexed": False,
            "claim_ceiling": "document retrieval preview only; no Planner admission, parsing, OCR, vector index, or action execution",
        }

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
        cycle_started = time.monotonic()
        phase_started = cycle_started
        phase_timings: list[dict[str, Any]] = []
        event_goal_timings: list[dict[str, Any]] = []

        def finish_phase(phase: str) -> None:
            nonlocal phase_started
            now = time.monotonic()
            phase_timings.append(
                {
                    "phase": phase,
                    "elapsed_seconds": round(now - phase_started, 4),
                }
            )
            phase_started = now

        self.db.execute(
            "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
            (cycle_id, started_at, "RUNNING"),
        )
        finish_phase("cycle_record_start")
        prediction_errors: list[dict[str, Any]] = []
        sensor_summary: list[dict[str, Any]] = []
        reserved: list[Event] = []
        selected_event_ids: set[str] = set()
        plan_persisted = False
        try:
            recovery_outcomes = self._resume_durable_actions()
            finish_phase("durable_action_recovery")
            sensor_summary, prediction_errors = self._poll_due_sensors()
            finish_phase("sensor_polling")
            step_started = time.monotonic()
            reserve_limit = self._event_reserve_limit()
            reserved = self.events.reserve(self.worker_id, reserve_limit)
            event_goal_timings.append(
                {
                    "step": "reserve_events",
                    "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    "limit": reserve_limit,
                    "reserved": len(reserved),
                }
            )
            step_started = time.monotonic()
            autonomous_goal_ids = self.autonomy.consider()
            event_goal_timings.append(
                {
                    "step": "autonomy_consider",
                    "elapsed_seconds": round(time.monotonic() - step_started, 4),
                }
            )
            step_started = time.monotonic()
            active_goals = self.goals.active(limit=20)
            event_goal_timings.append(
                {
                    "step": "active_goals",
                    "elapsed_seconds": round(time.monotonic() - step_started, 4),
                }
            )
            step_started = time.monotonic()
            query_text = self._query_text(reserved, active_goals)
            event_goal_timings.append(
                {
                    "step": "query_text",
                    "elapsed_seconds": round(time.monotonic() - step_started, 4),
                }
            )
            finish_phase("event_goal_intake")
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
            finish_phase("memory_retrieval")
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
            persisted_workspace = self._compact_workspace(workspace)
            selected_event_ids = self.attention.event_ids(workspace)
            self.events.release_unselected(self.worker_id, selected_event_ids)
            selected_events = [
                event for event in reserved if event.event_id in selected_event_ids
            ]
            finish_phase("appraisal_attention")
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
            finish_phase("context_assembly")
            if meaningful_input:
                planning_timings: list[dict[str, Any]] = []
                planning_started = time.monotonic()
                plan = self.planner.plan(context)
                planning_timings.append(
                    {
                        "step": "planner_plan",
                        "elapsed_seconds": round(time.monotonic() - planning_started, 4),
                    }
                )
                step_started = time.monotonic()
                self.memory_attribution.record(
                    cycle_id, plan.memory_ids, memory_retrieval
                )
                planning_timings.append(
                    {
                        "step": "memory_attribution_record",
                        "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    }
                )
                plan.actions = plan.actions[: int(budget["max_actions"])]
                step_started = time.monotonic()
                self._persist_plan_and_ack_events(
                    cycle_id, plan, [event.event_id for event in selected_events]
                )
                planning_timings.append(
                    {
                        "step": "persist_plan_ack_events_attach_cognition",
                        "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    }
                )
                plan_persisted = True
                finish_phase("planning_and_plan_persistence")
                outcomes = [*recovery_outcomes, *self._execute_plan(plan)]
                finish_phase("action_execution")
                cognition_learning_timings: list[dict[str, Any]] = []
                step_started = time.monotonic()
                cognition_result = self.cognition.resolve_cycle(cycle_id, plan, outcomes)
                cognition_learning_timings.append(
                    {
                        "step": "cognition_resolve_cycle",
                        "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    }
                )
                step_started = time.monotonic()
                memory_resolution = self.memory_attribution.resolve(
                    cycle_id,
                    outcomes,
                    cognition_result,
                    frozen=memory_mode == "frozen",
                )
                cognition_learning_timings.append(
                    {
                        "step": "memory_attribution_resolve",
                        "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    }
                )
                if cognition_result is not None and memory_resolution is not None:
                    cognition_result["memory_attribution"] = memory_resolution
                step_started = time.monotonic()
                plan_status = self._plan_status(plan.plan_id)
                cognition_learning_timings.append(
                    {
                        "step": "plan_status_refresh",
                        "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    }
                )
                step_started = time.monotonic()
                episode_id = self.learning.record_episode(
                    cycle_id=cycle_id,
                    event_ids=[event.event_id for event in selected_events],
                    plan_id=plan.plan_id,
                    outcomes=outcomes,
                    prediction_errors=prediction_errors,
                    workspace=persisted_workspace,
                )
                cognition_learning_timings.append(
                    {
                        "step": "learning_record_episode",
                        "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    }
                )
                step_started = time.monotonic()
                drives, affect, post_appraisal = self.drives.appraise(
                    selected_events,
                    outcomes=outcomes,
                    resource_snapshot=resource_snapshot,
                )
                cognition_learning_timings.append(
                    {
                        "step": "post_action_appraisal",
                        "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    }
                )
                finish_phase("cognition_learning_appraisal")
            else:
                planning_timings = []
                cognition_learning_timings = []
                plan = Plan(
                    rationale="Idle cycle: no external change, active goal, recovery, or prediction error.",
                    actions=[],
                )
                outcomes = []
                cognition_result = None
                plan_status = "IDLE"
                episode_id = None
                post_appraisal = {"idle": True}
                finish_phase("idle_plan")
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
            finish_phase("idle_sleep_maintenance")
            phase_total_seconds = round(sum(
                float(item["elapsed_seconds"]) for item in phase_timings
            ), 4)
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
                "phase_timings": phase_timings,
                "event_goal_timings": event_goal_timings,
                "planning_timings": planning_timings,
                "cognition_learning_timings": cognition_learning_timings,
                "phase_total_seconds": phase_total_seconds,
                "cycle_wall_seconds": round(time.monotonic() - cycle_started, 4),
            }
            self.db.execute(
                "UPDATE cycles SET finished_at=?,status=?,workspace_json=?,metrics_json=? WHERE cycle_id=?",
                (
                    utc_now(),
                    "SUCCEEDED",
                    json.dumps(persisted_workspace, ensure_ascii=False, sort_keys=True),
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

    @classmethod
    def _compact_workspace(cls, workspace: list[WorkspaceItem]) -> list[dict[str, Any]]:
        return [cls._compact_workspace_item(item) for item in workspace]

    @classmethod
    def _compact_workspace_item(cls, item: WorkspaceItem) -> dict[str, Any]:
        payload = item.payload
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(payload_json) <= cls.MAX_PERSISTED_WORKSPACE_PAYLOAD_CHARS:
            compact_payload: dict[str, Any] = payload
        else:
            compact_payload = {
                "truncated": True,
                "digest": digest_json(payload),
                "preview_json": payload_json[
                    : cls.MAX_PERSISTED_WORKSPACE_PAYLOAD_CHARS
                ],
                "top_level_keys": sorted(payload)[:50],
            }
        return {
            "item_type": item.item_type,
            "reference_id": item.reference_id,
            "summary": item.summary[:1000],
            "salience": item.salience,
            "reasons": item.reasons[:10],
            "payload": compact_payload,
        }

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

    def _event_reserve_limit(self) -> int:
        attention_window = max(
            self.config.workspace_capacity,
            int(self.config.workspace_capacity) * 2,
        )
        return max(1, min(int(self.config.max_events_per_cycle), attention_window))

    def _poll_due_sensors(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        summaries: list[dict[str, Any]] = []
        prediction_errors: list[dict[str, Any]] = []
        state_updates: list[tuple[str, tuple[Any, ...], dict[str, Any], str]] = []
        now = datetime.now(UTC)
        for sensor_config in self.config.sensors:
            if not sensor_config.enabled:
                continue
            sensor_started = time.monotonic()
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
                poll_started = time.monotonic()
                observations, next_state = sensor.poll(previous_state)
                poll_elapsed = time.monotonic() - poll_started
                observation_started = time.monotonic()
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
                observation_elapsed = time.monotonic() - observation_started
                summary = {
                    "sensor": sensor_config.name,
                    "observations": len(observations),
                    "status": "ok",
                    "elapsed_seconds": round(time.monotonic() - sensor_started, 4),
                    "poll_seconds": round(poll_elapsed, 4),
                    "observation_seconds": round(observation_elapsed, 4),
                }
                summaries.append(summary)
                polled_at = utc_now()
                state_updates.append(
                    (
                        """
                        INSERT INTO sensor_state(sensor_name,state_json,last_polled_at,last_success_at,last_error)
                        VALUES (?,?,?,?,NULL)
                        ON CONFLICT(sensor_name) DO UPDATE SET state_json=excluded.state_json,
                            last_polled_at=excluded.last_polled_at,last_success_at=excluded.last_success_at,last_error=NULL
                        """,
                        (
                            sensor_config.name,
                            json.dumps(next_state, ensure_ascii=False, sort_keys=True),
                            polled_at,
                            polled_at,
                        ),
                        summary,
                        "state_seconds",
                    )
                )
            except Exception as exc:
                error_started = time.monotonic()
                error_observation = self._sensor_error_observation(
                    sensor_config.name, exc
                )
                self.events.add_observation(error_observation)
                self.world.assimilate(error_observation)
                summary = {
                    "sensor": sensor_config.name,
                    "observations": 1,
                    "status": "error",
                    "error": str(exc),
                    "elapsed_seconds": round(time.monotonic() - sensor_started, 4),
                }
                summaries.append(summary)
                state_updates.append(
                    (
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
                        summary,
                        "error_record_seconds",
                    )
                )
                summary["error_prepare_seconds"] = round(
                    time.monotonic() - error_started, 4
                )
        if state_updates:
            state_started = time.monotonic()
            with self.db.transaction() as connection:
                for statement, parameters, _summary, _field in state_updates:
                    connection.execute(statement, parameters)
            state_elapsed = time.monotonic() - state_started
            per_sensor_elapsed = round(state_elapsed / len(state_updates), 4)
            batch_elapsed = round(state_elapsed, 4)
            for _statement, _parameters, summary, field in state_updates:
                summary[field] = per_sensor_elapsed
                summary["state_batch_seconds"] = batch_elapsed
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
            self.cognition.attach_plan(cycle_id, plan, connection)

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
            self.self_model.record_action_outcome(
                action, result, evidence_id, connection
            )
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

    def garbage_audit_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("garbage_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return [dict(item) for item in receipts[:limit] if isinstance(item, dict)]

    def garbage_quarantine_clear_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("garbage_quarantine_clear_receipts", [])
        if not isinstance(receipts, list):
            return []
        return [dict(item) for item in receipts[:limit] if isinstance(item, dict)]

    def garbage_audit(
        self,
        *,
        roots: list[str | Path] | None = None,
        max_candidates: int = 500,
        reason: str = "owner requested garbage audit",
        execute_cleanup: bool = False,
        approval_reference: str | None = None,
    ) -> dict[str, Any]:
        scan_roots = list(roots) if roots else [self.config.home_path]
        auditor = GarbageAuditor(
            lease_probe=self._lease_file_state,
            max_candidates=max_candidates,
        )
        receipt = auditor.audit(scan_roots)
        receipt["reason"] = reason
        receipt["authority"] = "owner_review_required_before_cleanup"
        if execute_cleanup:
            receipt = auditor.execute_cleanup(
                receipt, approval_reference=approval_reference or ""
            )
            receipt["reason"] = reason
            receipt["authority"] = "owner_approved_cleanup"
        summary = {
            key: receipt[key]
            for key in (
                "audit_id",
                "created_at",
                "status",
                "roots",
                "candidate_count",
                "total_candidate_bytes",
                "by_kind",
                "by_risk",
                "truncated",
                "owner_review_required",
                "cleanup_executed",
                "claim_ceiling",
                "reason",
                "authority",
            )
        }
        if execute_cleanup:
            summary["cleanup_executed_at"] = receipt.get("cleanup_executed_at")
            summary["approval_reference"] = receipt.get("approval_reference")
            summary["deleted_candidate_count"] = receipt.get(
                "deleted_candidate_count", 0
            )
            summary["deleted_bytes"] = receipt.get("deleted_bytes", 0)
            summary["quarantine_root"] = receipt.get("quarantine_root")
            summary["quarantined_candidate_count"] = receipt.get(
                "quarantined_candidate_count", 0
            )
            summary["quarantined_bytes"] = receipt.get("quarantined_bytes", 0)
        with self.db.transaction() as connection:
            current = self.garbage_audit_receipts(limit=20)
            updated = [summary, *current][:20]
            self.db.set_runtime("garbage_audit_receipts", updated, connection)
            self.db.set_runtime("garbage_audit_last", summary, connection)
            self.ledger.append(
                "garbage_cleanup_executed"
                if execute_cleanup
                else "garbage_audit_recorded",
                receipt,
                connection,
            )
        return receipt

    def clear_garbage_quarantine(
        self, *, audit_id: str, approval_reference: str, reason: str
    ) -> dict[str, Any]:
        if not audit_id.strip():
            raise ValueError("audit_id is required")
        if not approval_reference.strip():
            raise PermissionError("quarantine clear requires owner approval reference")
        if not reason.strip():
            raise ValueError("quarantine clear reason is required")
        matching = [
            item
            for item in self.garbage_audit_receipts(limit=100)
            if item.get("audit_id") == audit_id
        ]
        if not matching:
            raise KeyError(f"garbage audit not found: {audit_id}")
        audit = matching[0]
        quarantine_root_value = audit.get("quarantine_root")
        if not quarantine_root_value:
            raise ValueError("garbage audit has no quarantine root")
        quarantine_root = Path(str(quarantine_root_value)).expanduser().resolve()
        home = self.config.home_path.resolve()
        audit_roots = [
            Path(str(root)).expanduser().resolve()
            for root in audit.get("roots", [])
            if str(root).strip()
        ]
        try:
            quarantine_root.relative_to(home)
        except ValueError as exc:
            raise PermissionError("quarantine root is outside WLS home") from exc
        if ".wls_quarantine" not in quarantine_root.parts:
            raise PermissionError("quarantine clear path is not a WLS quarantine")
        if not any(self._path_within(quarantine_root, root) for root in audit_roots):
            raise PermissionError("quarantine root is outside the audit roots")
        if quarantine_root == home or quarantine_root.parent == home:
            raise PermissionError("refusing to clear broad home-level path")
        existed = quarantine_root.exists()
        bytes_before = GarbageAuditor._path_size(quarantine_root) if existed else 0
        if existed:
            shutil.rmtree(quarantine_root)
        receipt = {
            "receipt_type": "GARBAGE_QUARANTINE_CLEARED",
            "clear_id": new_id("garbage_quarantine_clear"),
            "audit_id": audit_id,
            "approval_reference": approval_reference,
            "reason": reason,
            "quarantine_root": str(quarantine_root),
            "existed": existed,
            "status": "QUARANTINE_CLEARED" if existed else "QUARANTINE_CLEAR_NOOP",
            "cleared_bytes": bytes_before,
            "cleanup_executed": True,
            "daemon_started": False,
            "claim_ceiling": (
                "owner-approved quarantine clear only; clears the quarantine "
                "directory recorded for one garbage audit"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.garbage_quarantine_clear_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "garbage_quarantine_clear_receipts", updated, connection
            )
            self.db.set_runtime("garbage_quarantine_clear_last", receipt, connection)
            self.ledger.append("garbage_quarantine_cleared", receipt, connection)
        return receipt

    @staticmethod
    def _path_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

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
                    gate = self._daemon_health_gate()
                    if not gate["allowed"]:
                        return
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

    def _daemon_health_gate(self) -> dict[str, Any]:
        health = self.health_snapshot()
        if health["status"] != "BLOCKED":
            return {"allowed": True, "health": health}
        payload = {
            "stopped_at": utc_now(),
            "status": health["status"],
            "critical": health.get("critical", []),
            "warnings": health.get("warnings", []),
            "database": health.get("database", {}),
            "cycle_count": health.get("cycle_count"),
            "claim_ceiling": "daemon stopped before next cycle; no recovery or cleanup was performed",
        }
        with self.db.transaction() as connection:
            self.db.set_runtime("daemon_last_stop", payload, connection)
            self.ledger.append("daemon_health_stop", payload, connection)
        return {"allowed": False, "health": health}

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
            "voice_transcript_receipts": self.voice_transcript_receipts(),
            "notification_draft_receipts": self.notification_draft_receipts(),
            "screen_snapshot_receipts": self.screen_snapshot_receipts(),
            "browser_form_draft_receipts": self.browser_form_draft_receipts(),
            "download_quarantine_receipts": self.download_quarantine_receipts(),
            "document_ingress_receipts": self.document_ingress_receipts(),
            "provider_route_receipts": self.provider_route_receipts(),
            "skill_candidate_receipts": self.skill_candidate_receipts(),
            "skill_sandbox_receipts": self.skill_sandbox_receipts(),
            "sandbox_adapter_receipts": self.sandbox_adapter_receipts(),
            "offspring_birth_receipts": self.offspring_birth_receipts(),
            "offspring_state_receipts": self.offspring_state_receipts(),
            "offspring_retirement_receipts": self.offspring_retirement_receipts(),
            "offspring_retirement_cleanup_receipts": (
                self.offspring_retirement_cleanup_receipts()
            ),
            "offspring_budget_receipts": self.offspring_budget_receipts(),
            "offspring_checkpoint_receipts": self.offspring_checkpoint_receipts(),
            "offspring_mailbox_receipts": self.offspring_mailbox_receipts(),
            "offspring_ecology_receipts": self.offspring_ecology_receipts(),
            "agentic_task_receipts": self.agentic_task_receipts(),
            "agentic_context_manifest_receipts": self.agentic_context_manifest_receipts(),
            "agentic_worker_profile_receipts": self.agentic_worker_profile_receipts(),
            "agentic_worker_lifecycle_receipts": self.agentic_worker_lifecycle_receipts(),
            "agentic_node_action_receipts": self.agentic_node_action_receipts(),
            "agentic_failure_attribution_receipts": self.agentic_failure_attribution_receipts(),
            "agentic_acceptance_trace_receipts": self.agentic_acceptance_trace_receipts(),
            "agentic_mailbox_receipts": self.agentic_mailbox_receipts(),
            "agentic_repair_candidate_receipts": self.agentic_repair_candidate_receipts(),
            "agentic_budget_receipts": self.agentic_budget_receipts(),
            "agentic_checkpoint_resume_receipts": self.agentic_checkpoint_resume_receipts(),
            "agentic_context_packet_receipts": self.agentic_context_packet_receipts(),
            "agentic_context_epoch_receipts": self.agentic_context_epoch_receipts(),
            "agentic_worker_lease_recovery_receipts": self.agentic_worker_lease_recovery_receipts(),
            "agentic_worker_arbitration_receipts": self.agentic_worker_arbitration_receipts(),
            "agentic_worker_trust_receipts": self.agentic_worker_trust_receipts(),
            "agentic_retry_gate_receipts": self.agentic_retry_gate_receipts(),
            "agentic_replan_candidate_receipts": self.agentic_replan_candidate_receipts(),
            "agentic_process_audit_receipts": self.agentic_process_audit_receipts(),
            "agentic_benchmark_scorecard_receipts": self.agentic_benchmark_scorecard_receipts(),
            "agentic_harness_epoch_audit_receipts": self.agentic_harness_epoch_audit_receipts(),
            "single_software_convergence_receipts": self.single_software_convergence_receipts(),
            "learning_epoch_receipts": self.learning_epoch_receipts(),
            "capability_epoch_audit_receipts": self.capability_epoch_audit_receipts(),
            "paired_experiment_receipts": self.paired_experiment_receipts(),
            "holdout_epoch_receipts": self.holdout_epoch_receipts(),
            "promotion_bundle_receipts": self.promotion_bundle_receipts(),
            "transfer_audit_receipts": self.transfer_audit_receipts(),
            "packaging_layout_receipts": self.packaging_layout_receipts(),
            "delivery_readiness_receipts": self.delivery_readiness_receipts(),
            "delivery_handoff_receipts": self.delivery_handoff_receipts(),
            "release_state_audit_receipts": self.release_state_audit_receipts(),
            "ui_hardening_audit_receipts": self.ui_hardening_audit_receipts(),
            "ui_package_absorption_receipts": self.ui_package_absorption_receipts(),
            "final_route_absorption_receipts": self.final_route_absorption_receipts(),
            "source_artifact_inventory_receipts": self.source_artifact_inventory_receipts(),
            "delivery_self_check_receipts": self.delivery_self_check_receipts(),
            "delivery_gap_audit_receipts": self.delivery_gap_audit_receipts(),
            "installed_tail_check_receipts": self.installed_tail_check_receipts(),
            "operational_preflight_receipts": self.operational_preflight_receipts(),
            "upgrade_drill_receipts": self.upgrade_drill_receipts(),
            "garbage_quarantine_clear_receipts": (
                self.garbage_quarantine_clear_receipts()
            ),
            "longitudinal_protocol_receipts": self.longitudinal_protocol_receipts(),
            "longitudinal_measurement_receipts": (
                self.longitudinal_measurement_receipts()
            ),
            "longitudinal_report_receipts": self.longitudinal_report_receipts(),
            "commercial_readiness_audit_receipts": (
                self.commercial_readiness_audit_receipts()
            ),
            "final_delivery_audit_receipts": self.final_delivery_audit_receipts(),
            "read_only_execution_preflights": self.read_only_execution_preflights(),
            "read_only_execution_receipts": self.read_only_execution_receipts(),
            "read_only_result_projections": self.read_only_result_projections(),
            "read_only_projection_reviews": self.read_only_projection_reviews(),
            "next_focus": self.db.get_runtime("next_focus", []),
        }

    def health_snapshot(self) -> dict[str, Any]:
        """Return a bounded operational health view without building full status.

        This is intentionally light enough for owner-console polling and host
        preflight checks on large live databases.
        """
        return self.build_health_snapshot(self.config, self.db)

    @classmethod
    def build_health_snapshot(cls, config: RuntimeConfig, db: Database) -> dict[str, Any]:
        db_path = config.db_path
        database_bytes = db_path.stat().st_size if db_path.exists() else 0
        database_limit = int(config.daemon_max_database_bytes)
        latest_cycle = db.query_one(
            """
            SELECT cycle_id,started_at,finished_at,status,error
            FROM cycles
            ORDER BY rowid DESC
            LIMIT 1
            """
        )
        pending_actions = db.query_one(
            """
            SELECT COUNT(*) AS n
            FROM actions
            WHERE status IN ('WAITING_APPROVAL','APPROVED','UNKNOWN_SIDE_EFFECT')
            """
        )
        sensor_rows = db.query_all(
            """
            SELECT sensor_name,last_polled_at,last_success_at,last_error
            FROM sensor_state
            ORDER BY sensor_name
            """
        )
        runtime_lock = config.home_path / "state" / "runtime.lock"
        daemon_lock = config.home_path / "state" / "daemon.lock"
        runtime_lock_state = cls._lease_file_state(runtime_lock)
        daemon_lock_state = cls._lease_file_state(daemon_lock)
        warnings: list[str] = []
        critical: list[str] = []
        if database_limit > 0 and database_bytes > database_limit:
            critical.append("database_size_over_daemon_limit")
        latest_status = str(latest_cycle["status"]) if latest_cycle else "NONE"
        if latest_cycle and latest_status not in {
            "COMPLETED",
            "IDLE",
            "NONE",
            "SUCCEEDED",
        }:
            warnings.append(f"latest_cycle_{latest_status.lower()}")
        if bool(db.get_runtime("kill_switch", False)):
            critical.append("kill_switch_active")
        if bool(db.get_runtime("paused", False)):
            warnings.append("runtime_paused")
        if runtime_lock_state["present"] and not runtime_lock_state["held"]:
            warnings.append("stale_runtime_lock")
        if daemon_lock_state["present"] and not daemon_lock_state["held"]:
            warnings.append("stale_daemon_lock")
        sensor_errors = [
            str(row["sensor_name"]) for row in sensor_rows if row["last_error"]
        ]
        if sensor_errors:
            warnings.append("sensor_errors")
        overall = "BLOCKED" if critical else ("WARN" if warnings else "OK")
        def latest_runtime_receipt(last_key: str, receipts_key: str) -> dict[str, Any] | None:
            latest = db.get_runtime(last_key, None)
            if isinstance(latest, dict):
                return latest
            receipts = db.get_runtime(receipts_key, [])
            if not isinstance(receipts, list):
                return None
            for item in receipts:
                if isinstance(item, dict):
                    return item
            return None

        return {
            "ok": overall == "OK",
            "status": overall,
            "version": __version__,
            "home": str(config.home_path),
            "read_only": config.read_only,
            "cycle_count": int(db.get_runtime("cycle_count", 0)),
            "latest_cycle": dict(latest_cycle) if latest_cycle else None,
            "pending_action_count": int(pending_actions["n"]) if pending_actions else 0,
            "database": {
                "path": str(db_path),
                "bytes": database_bytes,
                "daemon_limit_bytes": database_limit,
                "over_daemon_limit": database_limit > 0
                and database_bytes > database_limit,
            },
            "locks": {
                "runtime_lock_present": runtime_lock_state["present"],
                "daemon_lock_present": daemon_lock_state["present"],
                "runtime_lock_held": runtime_lock_state["held"],
                "daemon_lock_held": daemon_lock_state["held"],
                "runtime_lock_pid": runtime_lock_state["pid"],
                "daemon_lock_pid": daemon_lock_state["pid"],
            },
            "sensors": [dict(row) for row in sensor_rows],
            "garbage_audit": latest_runtime_receipt(
                "garbage_audit_last", "garbage_audit_receipts"
            ),
            "garbage_quarantine_clear": latest_runtime_receipt(
                "garbage_quarantine_clear_last", "garbage_quarantine_clear_receipts"
            ),
            "performance_budget": latest_runtime_receipt(
                "performance_budget_last", "performance_budget_receipts"
            ),
            "bounded_soak": latest_runtime_receipt(
                "bounded_soak_last", "bounded_soak_receipts"
            ),
            "upgrade_drill": latest_runtime_receipt(
                "upgrade_drill_last", "upgrade_drill_receipts"
            ),
            "longitudinal_report": latest_runtime_receipt(
                "longitudinal_report_last", "longitudinal_report_receipts"
            ),
            "retention_audit": latest_runtime_receipt(
                "retention_audit_last", "retention_audit_receipts"
            ),
            "commercial_readiness": latest_runtime_receipt(
                "commercial_readiness_audit_last",
                "commercial_readiness_audit_receipts",
            ),
            "critical": critical,
            "warnings": warnings,
            "projection_only": False,
        }

    @staticmethod
    def _lease_file_state(path: Path) -> dict[str, Any]:
        present = path.exists()
        pid: str | None = None
        if not present:
            return {"present": False, "held": False, "pid": None}
        try:
            pid_text = path.read_text(encoding="ascii").strip()
            pid = pid_text or None
        except OSError:
            pid = None
        held = False
        handle = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                except OSError:
                    held = True
            else:
                fcntl: Any = importlib.import_module("fcntl")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    held = True
        finally:
            handle.close()
        return {"present": present, "held": held, "pid": pid}

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
