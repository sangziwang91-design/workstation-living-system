from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import difflib
import hashlib
import importlib
import json
import mimetypes
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from ._version import __version__
from .action_candidate import BoundedActionCandidateBuilder
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
from .goal_pressure import GoalPressureRanker
from .growth_cycle import GrowthCycleManager
from .learning import LearningSystem
from .memory_attribution import MemoryAttributionStore
from .memory_influence import MemoryInfluenceAnalyzer
from .lease import ProcessLease
from .longitudinal import LongitudinalEvaluator, LongitudinalProtocol, MeasurementPoint
from .offspring import OffspringRegistry
from .outcome_learning import OwnerOutcomeLearner
from .performance import PerformanceMeasurement, evaluate_performance_budget
from .perception import PerceptionClassifier
from .planner import Planner
from .policy import PolicyEngine
from .relationships import RelationshipMemory
from .a2a_adapter import A2AAdapter, ArtifactEnvelope, TaskContract
from .mcp_adapter import McpCandidate, McpTrustGate
from .read_only_organs import ReadOnlyTaskReceipt, ReadOnlyTaskRequest
from .repo_explorer import RepoExplorer
from .rsi_evolution import RsiEvolutionPilot
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
    SkillDefinition,
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
    MAX_LIFE_STATE_ITEMS = 5
    MAX_LIFE_STATE_TEXT_CHARS = 400

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
        self._restore_patch_mission_read_roots()
        self._restore_patch_mission_write_roots()
        self.approvals = ApprovalManager(
            self.db, self.ledger, config.secret_path.with_name("approval.key")
        )
        self.tools = ToolRegistry(self.policy)
        self.repo_explorer = RepoExplorer()
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
        self.memory_influence = MemoryInfluenceAnalyzer()
        self.action_candidates = BoundedActionCandidateBuilder()
        self.outcome_learning = OwnerOutcomeLearner()
        self.perception = PerceptionClassifier()
        self.goal_pressure = GoalPressureRanker()
        self.planner = Planner(config, self.cognition, self.ledger)
        self.growth = GrowthCycleManager(self)
        self.agentic = AgenticHarness(self.db, self.ledger)
        self.offspring = OffspringRegistry(config, self.db, self.ledger)
        self.rsi_pilot = RsiEvolutionPilot(self.db, self.ledger)
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

    def bind_rsi_model_experiment(
        self,
        *,
        model_port: Any,
        policy: Any,
        objective: str,
        allowed_files: tuple[str, ...],
        independent_evaluator: Any,
    ) -> Any:
        """Bind an owner-started RSI candidate trial to canonical WLS authority.

        Reuses the existing WLS database, evidence ledger, evolution pilot and
        sandbox *directory* for candidate artifacts. This does not execute
        model-authored code or establish OS-grade sandbox isolation.
        """
        from .rsi_artifact_gate import RsiArtifactGate
        from .rsi_model_port import RsiModelCandidateBuilder, RsiModelExperiment

        gate = RsiArtifactGate(
            self.config.sandbox_path / "rsi_candidate_artifacts",
            self.ledger,
            allowed_files=frozenset(allowed_files),
        )
        return RsiModelExperiment(
            self.rsi_pilot,
            RsiModelCandidateBuilder(
                model_port,
                gate,
                policy_digest=policy.digest(),
                evaluator_digest=policy.evaluator_digest,
            ),
            policy,
            objective=objective,
            allowed_files=allowed_files,
            independent_evaluator=independent_evaluator,
        )

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

    def start_patch_mission(
        self,
        *,
        repo_path: str | Path,
        mission: str,
        execute_first_action: bool = True,
    ) -> dict[str, Any]:
        """Start a local GitHub-style patch mission with one real read-only action."""

        mission_text = str(mission).strip()
        if not mission_text:
            raise ValueError("patch mission text is required")
        repo_root = Path(repo_path).expanduser().resolve(strict=True)
        if not repo_root.is_dir():
            raise ValueError(f"repo path is not a directory: {repo_root}")
        self._allow_explicit_patch_mission_read_root(repo_root)
        repo_map = self.repo_explorer.explore(repo_root)
        mission_id = new_id("patch_mission")
        goal_id = self.add_goal(
            Goal(
                title=f"Patch mission: {mission_text[:120]}",
                description=(
                    f"Local repo: {repo_root}\n"
                    "Mission chain: inspect repo -> identify smallest patch -> "
                    "draft change -> run tests -> wait for owner approval before external write."
                ),
                priority=0.85,
                success_criteria=[
                    "repository instructions inspected",
                    "one bounded next action produced",
                    "writes remain owner-approved",
                ],
                source="patch-mission",
                autonomous=False,
                task_spec={
                    "mission_id": mission_id,
                    "repo_path": str(repo_root),
                    "mission": mission_text,
                    "representative_task_chain": "github_patch_mission",
                },
                risk=RiskLevel.READ,
            )
        )
        event = Event(
            event_type="patch_mission.started",
            source="patch-mission",
            payload={
                "mission_id": mission_id,
                "repo_path": str(repo_root),
                "mission": mission_text,
                "goal_id": goal_id,
                "repo_map_id": repo_map.map_id,
            },
            salience_hint=0.9,
            dedupe_key=f"patch_mission:{mission_id}",
        )
        event_id, inserted = self.events.add_event(event)
        action = self._patch_mission_first_action(
            repo_root=repo_root,
            repo_map=repo_map,
            mission_text=mission_text,
            goal_id=goal_id,
        )
        plan = Plan(
            rationale=(
                "Patch Mission first step: inspect local repository context with "
                "a bounded read-only action before any code write."
            ),
            actions=[action],
            unknowns=[
                "exact bug/fix target not selected yet",
                "write actions require owner approval",
                "external PR/push is out of scope for this first link",
            ],
        )
        cycle_id = new_id("cycle")
        self.db.execute(
            "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
            (cycle_id, utc_now(), "RUNNING"),
        )
        daily_perception = self._life_state_daily_perception(self.MAX_LIFE_STATE_ITEMS)
        goal_pressure = self._goal_pressure_summary(
            goals=self.goals.active(limit=20),
            daily_perception=daily_perception,
        )
        action_candidate = self._record_action_candidate(
            cycle_id=cycle_id,
            plan=plan,
            goal_pressure=goal_pressure,
            daily_perception=daily_perception,
            memory_influence=None,
        )
        action_candidate = self._patch_mission_with_skill_action_candidate_metadata(
            action_candidate, action
        )
        self.db.set_runtime("last_action_candidate", action_candidate)
        if action_candidate.get("suppressed"):
            plan.actions = []
        outcomes: list[dict[str, Any]] = []
        self._persist_plan_and_ack_events(cycle_id, plan, [])
        if execute_first_action and plan.actions:
            outcomes = self._execute_plan(plan)
        else:
            self._refresh_plan_status(plan.plan_id)
        plan_status = self._plan_status(plan.plan_id)
        mission_record = {
            "mission_id": mission_id,
            "status": "STARTED",
            "repo_path": str(repo_root),
            "mission": mission_text,
            "goal_id": goal_id,
            "event_id": event_id,
            "event_inserted": inserted,
            "cycle_id": cycle_id,
            "plan_id": plan.plan_id,
            "action_id": action.action_id,
            "repo_map": {
                "map_id": repo_map.map_id,
                "root": repo_map.root,
                "file_count": len(repo_map.files),
                "instruction_files": repo_map.instruction_files[:10],
                "test_hints": self._patch_mission_test_hints(repo_map),
            },
            "next_action_candidate": action_candidate,
            "outcomes": outcomes,
            "plan_status": plan_status,
            "authority": {
                "read_only_repo_scope": str(repo_root),
                "writes_canonical_repo": False,
                "push_or_pr_created": False,
                "owner_approval_required_for_write": True,
            },
            "created_at": utc_now(),
        }
        self._record_patch_mission_state(mission_record)
        self.db.execute(
            "UPDATE cycles SET finished_at=?,status=?,workspace_json=?,metrics_json=? WHERE cycle_id=?",
            (
                utc_now(),
                "SUCCEEDED",
                json.dumps([], ensure_ascii=False),
                json.dumps(
                    {
                        "patch_mission": mission_record,
                        "actions": len(plan.actions),
                        "outcomes": outcomes,
                        "plan_status": plan_status,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                cycle_id,
            ),
        )
        return mission_record

    def patch_missions(self, limit: int = 20) -> list[dict[str, Any]]:
        records = self.db.get_runtime("patch_missions", [])
        if not isinstance(records, list):
            return []
        return [
            self._patch_mission_with_continuity(dict(item))
            for item in records[: max(0, int(limit))]
            if isinstance(item, dict)
        ]

    def _patch_mission_with_continuity(
        self, mission_record: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            **mission_record,
            "continuity": self._patch_mission_continuity(mission_record),
        }

    def _patch_mission_continuity(
        self, mission_record: dict[str, Any]
    ) -> dict[str, Any]:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            followups = []
        waiting = self._patch_mission_first_followup_with_status(
            followups, {"WAITING_APPROVAL", "APPROVED"}
        )
        if waiting:
            row = waiting["row"]
            confidence_decision = self._patch_mission_step_confidence_decision(
                waiting["step"]
            )
            summary = f"Waiting for owner action on `{waiting['mode']}`."
            next_step = f"Approve or reject action `{waiting['action_id']}`."
            if (
                isinstance(confidence_decision, dict)
                and confidence_decision.get("decision")
                == "outcome_supported_direct_test"
                and confidence_decision.get("level") == "recovered_outcome_supported"
            ):
                summary = (
                    "Recovered promoted confidence restored a direct narrow pytest "
                    "action, now waiting for owner approval."
                )
                next_step = (
                    f"Approve or reject recovered direct pytest action `{waiting['action_id']}`."
                )
            return {
                "state": "waiting_approval",
                "summary": summary,
                "next_step": next_step,
                "blocking_action_id": waiting["action_id"],
                "blocking_mode": waiting["mode"],
                "tool": row.get("tool"),
                "risk": row.get("risk"),
                "reason": row.get("error") or "",
                "confidence_decision": confidence_decision,
            }
        mode_states = {
            "pr-update-next": (
                "pr_update_decided",
                "Follow the generated wait/review note or next CI fix plan.",
            ),
            "pr-update-verify": (
                "pr_update_verified",
                "Run `patch-mission-step --mode pr-update-next --action-id <verify_action>`.",
            ),
            "pr-update-status": (
                "pr_update_status_checked",
                "Run `patch-mission-step --mode pr-update-verify --action-id <post_update_status_action>`.",
            ),
            "pr-update-push-draft": (
                "pr_branch_updated",
                "Run `patch-mission-step --mode pr-update-status --action-id <push_action>`.",
            ),
            "ci-next-action": (
                "local_repair_action_created",
                "Approve/run the local action, then continue from its result.",
            ),
            "ci-fix-plan": (
                "local_repair_planned",
                "Run `patch-mission-step --mode ci-next-action --action-id <ci_fix_plan_action>`.",
            ),
            "ci-log-evidence": (
                "ci_logs_captured",
                "Run `patch-mission-step --mode ci-fix-plan --action-id <ci_log_evidence_action>`.",
            ),
            "pr-status": (
                "pr_status_checked",
                "Run `patch-mission-step --mode ci-log-evidence --action-id <pr_status_action>` if failures remain.",
            ),
            "pr-create-draft": (
                "draft_pr_created_or_prepared",
                "Run `patch-mission-step --mode pr-status --action-id <pr_create_action>` after PR creation succeeds.",
            ),
            "push-draft": (
                "branch_pushed_or_prepared",
                "Create or inspect the PR after the push succeeds.",
            ),
            "remote-live-summary": (
                "live_remote_summary_ready",
                "Run `patch-mission-step --mode push-draft --action-id <remote_live_summary_action>`.",
            ),
            "remote-live": (
                "live_remote_checked",
                "Run `patch-mission-step --mode remote-live-summary --action-id <remote_live_action>`.",
            ),
            "branch-draft": (
                "local_branch_prepared",
                "Run `patch-mission-step --mode remote-live` before pushing.",
            ),
            "remote-summary": (
                "remote_summary_ready",
                "Run `patch-mission-step --mode branch-draft --action-id <remote_summary_action>`.",
            ),
            "commit-draft": (
                "local_commit_prepared",
                "Run `patch-mission-step --mode git-metadata` to refresh remote/branch context.",
            ),
            "git-prep": (
                "commit_checklist_ready",
                "Run `patch-mission-step --mode commit-draft --action-id <git_prep_action>`.",
            ),
            "git-metadata": (
                "git_metadata_checked",
                "Continue with `git-prep` before commit or `remote-summary` after commit.",
            ),
            "pr-summary": (
                "local_fix_verified",
                "Prepare commit/push or owner review from the verified PR summary.",
            ),
            "apply-patch": (
                "patch_applied",
                "Run `patch-mission-step --mode test` to verify the applied change.",
            ),
            "from-test-result": (
                "patch_draft_ready",
                "Approve/apply the patch draft or inspect it manually.",
            ),
            "test": (
                "local_test_result_available",
                "Continue from the approved local test result.",
            ),
        }
        for item in followups:
            if not isinstance(item, dict) or not item.get("action_id"):
                continue
            mode = str(item.get("mode", "")).lower()
            state_next = mode_states.get(mode)
            if not state_next:
                continue
            row = self._patch_mission_action_row(str(item["action_id"]))
            if row and str(row.get("status")) == ActionStatus.SUCCEEDED.value:
                state, next_step = state_next
                return self._patch_mission_continuity_for_succeeded_step(
                    mission_record=mission_record,
                    step={**item, "_action_row": row},
                    state=state,
                    next_step=next_step,
                )
        return {
            "state": "started",
            "summary": "Patch Mission is started and awaiting the first follow-up action.",
            "next_step": "Run `patch-mission-step` to inspect, test, or continue the mission.",
            "latest_mode": None,
            "latest_action_id": mission_record.get("action_id"),
        }

    def continue_patch_mission(
        self,
        *,
        mission_id: str | None = None,
        mode: str = "auto",
        target: str | None = None,
        draft: str | None = None,
        action_id: str | None = None,
        execute: bool = True,
    ) -> dict[str, Any]:
        """Advance a patch mission by one bounded action under canonical policy."""

        mission_record = self._patch_mission_record(mission_id)
        repo_root = Path(str(mission_record["repo_path"])).expanduser().resolve(
            strict=True
        )
        self._allow_explicit_patch_mission_read_root(repo_root)
        repo_map = self.repo_explorer.explore(repo_root)
        requested_mode = str(mode or "auto").strip().lower()
        resume_next: dict[str, Any] | None = None
        if requested_mode == "resume-next":
            if target or draft or action_id:
                raise ValueError(
                    "resume-next consumes mission continuity and does not accept --target, --draft, or --action-id"
                )
            resume_next = self._patch_mission_resume_next_instruction(mission_record)
            mode = str(resume_next["mode"])
            target = resume_next.get("target")
            action_id = resume_next.get("action_id")
        action = self._patch_mission_followup_action(
            mission_record=mission_record,
            repo_root=repo_root,
            repo_map=repo_map,
            mode=mode,
            target=target,
            draft=draft,
            action_id=action_id,
        )
        cycle_id = new_id("cycle")
        plan = Plan(
            rationale=(
                "Patch Mission follow-up step: advance the local repo task by one "
                "bounded action while preserving write and external approval gates."
            ),
            actions=[action],
            unknowns=[
                "patch mission is still local-only",
                "repo writes, commits, pushes, and PR creation require owner approval",
            ],
        )
        self.db.execute(
            "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
            (cycle_id, utc_now(), "RUNNING"),
        )
        daily_perception = self._life_state_daily_perception(self.MAX_LIFE_STATE_ITEMS)
        goal_pressure = self._goal_pressure_summary(
            goals=self.goals.active(limit=20),
            daily_perception=daily_perception,
        )
        action_candidate = self._record_action_candidate(
            cycle_id=cycle_id,
            plan=plan,
            goal_pressure=goal_pressure,
            daily_perception=daily_perception,
            memory_influence=None,
        )
        action_candidate = self._patch_mission_with_skill_action_candidate_metadata(
            action_candidate, action
        )
        self.db.set_runtime("last_action_candidate", action_candidate)
        if action_candidate.get("suppressed"):
            plan.actions = []
        outcomes: list[dict[str, Any]] = []
        self._persist_plan_and_ack_events(cycle_id, plan, [])
        if execute and plan.actions:
            outcomes = self._execute_plan(plan)
        else:
            self._refresh_plan_status(plan.plan_id)
        plan_status = self._plan_status(plan.plan_id)
        step = {
            "step_id": new_id("patch_step"),
            "mission_id": mission_record["mission_id"],
            "mode": mode,
            "target": target,
            "source_action_id": action_id,
            "cycle_id": cycle_id,
            "plan_id": plan.plan_id,
            "action_id": action.action_id,
            "action": action.to_dict(),
            "next_action_candidate": action_candidate,
            "outcomes": outcomes,
            "plan_status": plan_status,
            "authority": {
                "writes_canonical_repo": self.policy._contained(
                    Path(str(action.arguments.get("path", ""))).expanduser().resolve(
                        strict=False
                    ),
                    repo_root,
                )
                if action.tool == "write_file"
                else False,
                "draft_patch_target": "wls_outbox_only"
                if action.tool == "write_file"
                and self.policy._contained(
                    Path(str(action.arguments.get("path", ""))).expanduser().resolve(
                        strict=False
                    ),
                    self.config.outbox_path,
                )
                else None,
                "test_command_requires_policy": action.tool == "run_command"
                and (
                    str(mode).lower() == "test"
                    or list(action.arguments.get("command") or [])[:3]
                    == ["python", "-m", "pytest"]
                ),
                "owner_approval_required_for_write_or_external": action.risk
                != RiskLevel.READ
                or action.tool == "run_command",
                "push_or_pr_created": False,
            },
            "created_at": utc_now(),
        }
        confidence_decision = self._patch_mission_action_confidence_decision(
            action_candidate=action_candidate,
            action=action,
            repo_root=repo_root,
        )
        if confidence_decision is not None:
            step["confidence_decision"] = confidence_decision
        if resume_next is not None:
            step["requested_mode"] = requested_mode
            step["resume_next"] = resume_next
        updated = self._update_patch_mission_record(mission_record["mission_id"], step)
        self.db.execute(
            "UPDATE cycles SET finished_at=?,status=?,workspace_json=?,metrics_json=? WHERE cycle_id=?",
            (
                utc_now(),
                "SUCCEEDED",
                json.dumps([], ensure_ascii=False),
                json.dumps(
                    {
                        "patch_mission_step": step,
                        "actions": len(plan.actions),
                        "outcomes": outcomes,
                        "plan_status": plan_status,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                cycle_id,
            ),
        )
        return {**step, "mission": updated}

    def _patch_mission_resume_next_instruction(
        self, mission_record: dict[str, Any]
    ) -> dict[str, Any]:
        continuity = self._patch_mission_continuity(mission_record)
        state = str(continuity.get("state") or "")
        if state == "waiting_approval":
            raise ValueError(
                "patch mission is waiting for owner approval on "
                f"{continuity.get('blocking_mode')} action {continuity.get('blocking_action_id')}"
            )
        latest_mode = str(continuity.get("latest_mode") or "")
        latest_action_id = str(continuity.get("latest_action_id") or "")

        def instruction(mode: str, action_id: str | None = None) -> dict[str, Any]:
            return {
                "mode": mode,
                "action_id": action_id,
                "target": None,
                "from_state": state,
                "from_mode": latest_mode or None,
                "from_action_id": latest_action_id or None,
                "reason": continuity.get("summary") or continuity.get("next_step") or "",
            }

        if not latest_mode:
            return instruction("auto", None)
        if latest_mode == "pr-update-next":
            text = self._patch_mission_optional_write_content(latest_action_id)
            if "# Patch Mission CI Fix Plan" in text:
                return instruction("ci-next-action", latest_action_id)
            raise ValueError(
                "patch mission is ready for owner review; no automatic next action is available"
            )
        if latest_mode == "pr-update-verify":
            return instruction("pr-update-next", latest_action_id)
        if latest_mode == "pr-update-status":
            return instruction("pr-update-verify", latest_action_id)
        if latest_mode == "pr-update-push-draft":
            return instruction("pr-update-status", latest_action_id)
        if latest_mode == "ci-fix-plan":
            return instruction("ci-next-action", latest_action_id)
        if latest_mode == "ci-log-evidence":
            return instruction("ci-fix-plan", latest_action_id)
        if latest_mode == "ci-next-action":
            confidence_decision = continuity.get("confidence_decision")
            if (
                isinstance(confidence_decision, dict)
                and confidence_decision.get("decision")
                == "safety_fallback_inspect_file"
            ):
                target_decision = self._patch_mission_fallback_verification_target(
                    confidence_decision=confidence_decision,
                    inspect_action_id=latest_action_id,
                )
                item = instruction("test", None)
                item["target"] = target_decision["target"]
                item["confidence_decision"] = confidence_decision
                item["target_decision"] = target_decision
                item["reason"] = (
                    "Read-only confidence fallback inspection completed; continue "
                    f"with owner-gated local verification target `{target_decision['target']}` "
                    f"before drafting another repair. {target_decision['reason']}"
                )
                return item
            return instruction("from-test-result", latest_action_id)
        if latest_mode == "pr-status":
            if int(continuity.get("failure_count") or 0) > 0:
                return instruction("ci-log-evidence", latest_action_id)
            raise ValueError(
                "latest PR status has no captured failure summary; owner review or later polling is required"
            )
        if latest_mode == "pr-create-draft":
            return instruction("pr-status", latest_action_id)
        if latest_mode == "from-test-result":
            return instruction("apply-patch", latest_action_id)
        if latest_mode == "draft-patch":
            return instruction("apply-patch", latest_action_id)
        if latest_mode == "apply-patch":
            return instruction("test", None)
        if latest_mode == "test":
            return instruction("from-test-result", latest_action_id)
        publish_next = self._patch_mission_resume_publish_instruction(
            mission_record=mission_record,
            state=state,
            latest_mode=latest_mode,
            latest_action_id=latest_action_id,
            reason=continuity.get("summary") or continuity.get("next_step") or "",
        )
        if publish_next is not None:
            return publish_next
        raise ValueError(
            f"resume-next has no safe continuation for latest Patch Mission mode: {latest_mode}"
        )

    def _patch_mission_resume_publish_instruction(
        self,
        *,
        mission_record: dict[str, Any],
        state: str,
        latest_mode: str,
        latest_action_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        summary_id = self._patch_mission_latest_succeeded_action_id(
            mission_record, "pr-summary"
        )
        if not summary_id:
            return None

        def item(mode: str, action_id: str | None = None) -> dict[str, Any]:
            return {
                "mode": mode,
                "action_id": action_id,
                "target": None,
                "from_state": state,
                "from_mode": latest_mode or None,
                "from_action_id": latest_action_id or None,
                "reason": reason,
            }

        push_id = self._patch_mission_latest_succeeded_action_id(
            mission_record, "push-draft"
        )
        if push_id and not self._patch_mission_latest_succeeded_action_id(
            mission_record, "pr-create-draft"
        ):
            if not self._patch_mission_has_github_remote_evidence(mission_record):
                metadata_id = self._patch_mission_latest_succeeded_action_id_after(
                    mission_record, "git-metadata", after_action_id=push_id
                )
                if metadata_id:
                    return item("remote-summary", metadata_id)
                return item("git-metadata", None)
            return item("pr-create-draft", push_id)
        if self._patch_mission_latest_succeeded_action_id(
            mission_record, "pr-create-draft"
        ):
            return item(
                "pr-status",
                self._patch_mission_latest_succeeded_action_id(
                    mission_record, "pr-create-draft"
                ),
            )

        commit_id = self._patch_mission_latest_succeeded_action_id(
            mission_record, "commit-draft"
        )
        if not commit_id:
            prep_id = self._patch_mission_latest_succeeded_action_id(
                mission_record, "git-prep"
            )
            if prep_id:
                return item("commit-draft", prep_id)
            metadata_id = self._patch_mission_latest_succeeded_action_id(
                mission_record, "git-metadata"
            )
            if metadata_id:
                return item("git-prep", metadata_id)
            return item("git-metadata", None)

        failed_pr_status_id = self._patch_mission_latest_failed_pr_status_action_id(
            mission_record
        )
        if failed_pr_status_id and not self._patch_mission_latest_succeeded_action_id(
            mission_record, "pr-update-push-draft"
        ):
            return item("pr-update-push-draft", failed_pr_status_id)

        live_summary_id = self._patch_mission_latest_succeeded_action_id(
            mission_record, "remote-live-summary"
        )
        if live_summary_id:
            return item("push-draft", live_summary_id)
        live_id = self._patch_mission_latest_succeeded_action_id(
            mission_record, "remote-live"
        )
        if live_id:
            return item("remote-live-summary", live_id)
        branch_id = self._patch_mission_latest_succeeded_action_id(
            mission_record, "branch-draft"
        )
        if branch_id:
            return item("remote-live", None)
        remote_summary_id = self._patch_mission_latest_succeeded_action_id(
            mission_record, "remote-summary"
        )
        if remote_summary_id:
            return item("branch-draft", remote_summary_id)
        metadata_id = self._patch_mission_latest_succeeded_action_id_after(
            mission_record, "git-metadata", after_action_id=commit_id
        )
        if metadata_id:
            return item("remote-summary", metadata_id)
        return item("git-metadata", None)

    def _record_patch_mission_state(self, mission_record: dict[str, Any]) -> None:
        current = [
            self._patch_mission_without_continuity(item)
            for item in self.patch_missions(limit=100)
        ]
        updated = [mission_record, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("patch_missions", updated, connection)
            self.db.set_runtime("last_patch_mission", mission_record, connection)
            self.ledger.append(
                "patch_mission_started",
                {
                    "mission_id": mission_record["mission_id"],
                    "repo_path": mission_record["repo_path"],
                    "goal_id": mission_record["goal_id"],
                    "plan_id": mission_record["plan_id"],
                    "action_id": mission_record["action_id"],
                    "plan_status": mission_record["plan_status"],
                },
                connection,
            )

    def _patch_mission_record(self, mission_id: str | None) -> dict[str, Any]:
        records = self.patch_missions(limit=100)
        if mission_id is None:
            if not records:
                raise KeyError("no patch mission has been started")
            return dict(records[0])
        for record in records:
            if str(record.get("mission_id")) == str(mission_id):
                return dict(record)
        raise KeyError(f"unknown patch mission: {mission_id}")

    def _update_patch_mission_record(
        self, mission_id: str, step: dict[str, Any]
    ) -> dict[str, Any]:
        records = self.patch_missions(limit=100)
        updated_record: dict[str, Any] | None = None
        updated_records: list[dict[str, Any]] = []
        for record in records:
            item = dict(record)
            item.pop("continuity", None)
            if str(item.get("mission_id")) == str(mission_id):
                followups = item.get("followups", [])
                if not isinstance(followups, list):
                    followups = []
                item["followups"] = [step, *followups][:50]
                item["latest_step_id"] = step["step_id"]
                item["latest_plan_status"] = step["plan_status"]
                item["updated_at"] = utc_now()
                updated_record = item
            updated_records.append(item)
        if updated_record is None:
            raise KeyError(f"unknown patch mission: {mission_id}")
        with self.db.transaction() as connection:
            self.db.set_runtime("patch_missions", updated_records, connection)
            self.db.set_runtime("last_patch_mission", updated_record, connection)
            self.ledger.append(
                "patch_mission_step_recorded",
                {
                    "mission_id": mission_id,
                    "step_id": step["step_id"],
                    "mode": step["mode"],
                    "action_id": step["action_id"],
                    "plan_status": step["plan_status"],
                },
                connection,
            )
        return updated_record

    @staticmethod
    def _patch_mission_without_continuity(
        mission_record: dict[str, Any]
    ) -> dict[str, Any]:
        item = dict(mission_record)
        item.pop("continuity", None)
        return item

    def _patch_mission_first_followup_with_status(
        self, followups: list[Any], statuses: set[str]
    ) -> dict[str, Any] | None:
        for item in followups:
            if not isinstance(item, dict) or not item.get("action_id"):
                continue
            row = self._patch_mission_action_row(str(item["action_id"]))
            if row and str(row.get("status")) in statuses:
                return {
                    "action_id": str(item["action_id"]),
                    "mode": str(item.get("mode") or ""),
                    "step": item,
                    "row": row,
                }
        return None

    def _patch_mission_first_succeeded_followup(
        self, followups: list[Any], mode: str
    ) -> dict[str, Any] | None:
        for item in followups:
            if (
                not isinstance(item, dict)
                or str(item.get("mode", "")).lower() != mode
                or not item.get("action_id")
            ):
                continue
            row = self._patch_mission_action_row(str(item["action_id"]))
            if row and str(row.get("status")) == ActionStatus.SUCCEEDED.value:
                return {**item, "_action_row": row}
        return None

    @staticmethod
    def _patch_mission_step_confidence_decision(
        step: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(step, dict):
            return None
        decision = step.get("confidence_decision")
        return decision if isinstance(decision, dict) else None

    def _patch_mission_action_confidence_decision(
        self,
        *,
        action_candidate: dict[str, Any],
        action: ActionSpec,
        repo_root: Path,
    ) -> dict[str, Any] | None:
        metadata = action_candidate.get("patch_mission_skill_candidate")
        if not isinstance(metadata, dict):
            return None
        confidence = metadata.get("promoted_outcome_confidence")
        if not isinstance(confidence, dict):
            return None
        level = str(confidence.get("level") or "")
        if not level:
            return None
        selected_mode = "test" if action.tool == "run_command" else None
        selected_target: str | None = None
        command = action.arguments.get("command") if isinstance(action.arguments, dict) else None
        if isinstance(command, list) and len(command) > 3:
            selected_target = str(command[3])
        if action.tool == "read_file":
            selected_mode = "inspect-file"
            raw_path = action.arguments.get("path") if isinstance(action.arguments, dict) else None
            if raw_path:
                try:
                    selected_target = str(
                        Path(str(raw_path))
                        .expanduser()
                        .resolve(strict=False)
                        .relative_to(repo_root)
                    ).replace("\\", "/")
                except ValueError:
                    selected_target = str(raw_path)
        if (
            level in {"outcome_supported", "recovered_outcome_supported"}
            and action.tool == "run_command"
        ):
            decision = "outcome_supported_direct_test"
        elif (
            level
            in {"mixed_outcome", "recovering_mixed_outcome", "confidence_withheld"}
            and action.tool == "read_file"
        ):
            decision = "safety_fallback_inspect_file"
        else:
            decision = "confidence_metadata_only"
        return {
            "level": level,
            "decision": decision,
            "selected_mode": selected_mode,
            "selected_target": selected_target,
            "selected_tool": action.tool,
            "requires_owner_approval": bool(
                action_candidate.get("requires_owner_approval")
                or action_candidate.get("approval_required")
            ),
            "executes_now": bool(action_candidate.get("executes_now")),
            "skill_id": metadata.get("skill_id"),
            "source_action_id": metadata.get("source_action_id"),
            "source_mode": metadata.get("source_mode"),
            "success_count": confidence.get("success_count"),
            "failed_or_rejected_count": confidence.get("failed_or_rejected_count"),
            "recovery_count": confidence.get("recovery_count"),
            "unrecovered_failed_or_rejected_count": confidence.get(
                "unrecovered_failed_or_rejected_count"
            ),
            "reason": confidence.get("reason"),
            "authority": {
                "continuity_only": True,
                "approval_bypass_allowed": False,
                "repo_write_executed": False,
                "push_or_pr_executed": False,
            },
        }

    def _patch_mission_fallback_verification_target(
        self,
        *,
        confidence_decision: dict[str, Any],
        inspect_action_id: str,
    ) -> dict[str, Any]:
        selected_target = str(confidence_decision.get("selected_target") or "").strip()
        source_action_id = str(confidence_decision.get("source_action_id") or "").strip()
        if not selected_target:
            return {
                "target": None,
                "decision": "no_target_available",
                "reason": "fallback inspection did not preserve a target",
            }
        original_target = ""
        if source_action_id:
            plan_text = self._patch_mission_optional_write_content(source_action_id)
            original_target = self._patch_mission_strip_plan_value(
                self._patch_mission_plan_bullet(
                    plan_text,
                    "Original CI-selected target",
                )
            )
        if not original_target or original_target == "(none)":
            return {
                "target": selected_target,
                "decision": "file_level_fallback",
                "reason": "no original CI nodeid was available after fallback inspection",
            }
        try:
            self._patch_mission_validate_pytest_target_text(original_target)
        except ValueError:
            return {
                "target": selected_target,
                "decision": "file_level_fallback",
                "reason": "original CI target was not a safe pytest target",
            }
        file_part = original_target.split("::", 1)[0]
        if file_part != selected_target:
            return {
                "target": selected_target,
                "decision": "file_level_fallback",
                "reason": "original CI target points at a different file than the fallback inspection",
            }
        inspected_text = self._patch_mission_read_file_action_text(inspect_action_id)
        if not self._patch_mission_pytest_nodeid_exists_in_text(
            original_target,
            inspected_text,
        ):
            return {
                "target": selected_target,
                "decision": "file_level_fallback",
                "reason": "original CI nodeid was not found in the inspected file content",
            }
        return {
            "target": original_target,
            "decision": "nodeid_restored_from_ci_and_inspection",
            "reason": (
                "original CI nodeid was restored because the inspected file contains "
                "the referenced test function"
            ),
        }

    @staticmethod
    def _patch_mission_strip_plan_value(value: str) -> str:
        text = str(value or "").strip()
        if text.startswith("`") and text.endswith("`") and len(text) >= 2:
            return text[1:-1].strip()
        return text

    def _patch_mission_read_file_action_text(self, action_id: str) -> str:
        row = self.db.query_one(
            "SELECT tool,status,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "read_file":
            raise ValueError("source action must be a read_file action")
        if row["status"] != ActionStatus.SUCCEEDED.value:
            raise ValueError(f"read_file action has not succeeded: {row['status']}")
        payload = json.loads(row["result_json"])
        result = payload.get("result") if isinstance(payload, dict) else None
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            raise ValueError("read_file action result has no output object")
        return str(output.get("text") or "")

    @staticmethod
    def _patch_mission_pytest_nodeid_exists_in_text(
        pytest_target: str,
        text: str,
    ) -> bool:
        parts = str(pytest_target).split("::")
        if len(parts) < 2:
            return False
        node = parts[-1]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node):
            return False
        return bool(
            re.search(
                rf"^\s*(?:async\s+def|def)\s+{re.escape(node)}\s*\(",
                text,
                re.MULTILINE,
            )
        )

    def _patch_mission_action_row(self, action_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT action_id,tool,status,risk,error,result_json,arguments_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        return dict(row) if row is not None else None

    def _patch_mission_continuity_for_succeeded_step(
        self,
        *,
        mission_record: dict[str, Any],
        step: dict[str, Any],
        state: str,
        next_step: str,
    ) -> dict[str, Any]:
        mode = str(step.get("mode") or "")
        action_id = str(step.get("action_id") or "")
        summary = f"Latest completed Patch Mission step is `{mode}`."
        extra: dict[str, Any] = {}
        confidence_decision = self._patch_mission_step_confidence_decision(step)
        if confidence_decision is not None:
            extra["confidence_decision"] = confidence_decision
        if mode == "pr-update-next":
            text = self._patch_mission_optional_write_content(action_id)
            if "# Patch Mission CI Fix Plan" in text:
                state = "pr_ci_still_failing"
                summary = "Post-update verification still shows failures; another CI fix plan is ready."
                next_step = "Approve/review the generated `ci-fix-plan`, then run `ci-next-action`."
            elif "# Patch Mission PR Update Next Step" in text:
                state = "ready_for_owner_review"
                summary = "No immediate post-update failure summary is captured; PR is ready for owner review or later status polling."
                next_step = "Owner reviews PR state, or run another read-only status check later if checks are pending."
        elif mode == "pr-update-verify":
            text = self._patch_mission_optional_write_content(action_id)
            try:
                after = self._patch_mission_pr_update_verify_after_failure_count(text)
                extra["post_update_failure_count"] = after
                if after > 0:
                    state = "pr_ci_still_failing"
                    summary = "Post-update verification still has failing PR/CI evidence."
                else:
                    state = "pr_branch_updated"
                    summary = "PR branch was updated and post-update verification has no immediate failure summary."
            except ValueError:
                pass
        elif mode in {"pr-status", "pr-update-status"}:
            try:
                output = self._patch_mission_pr_status_output(action_id)
                failures = output.get("failure_summary", [])
                failure_count = len(failures) if isinstance(failures, list) else 0
                extra["failure_count"] = failure_count
                extra["pr_url"] = output.get("url")
                extra["head_sha"] = output.get("head_sha")
                if failure_count:
                    state = "pr_ci_still_failing"
                    summary = "PR/CI status contains failing evidence."
                else:
                    state = "pr_ci_pending_or_ready"
                    summary = "PR/CI status has no immediate failure summary."
            except (KeyError, ValueError):
                pass
        elif mode == "ci-next-action" and isinstance(confidence_decision, dict):
            if confidence_decision.get("decision") == "safety_fallback_inspect_file":
                state = "local_confidence_fallback_inspected"
                summary = (
                    "Mixed/withheld promoted confidence selected a read-only file "
                    "inspection before another command-driven repair."
                )
                next_step = (
                    "Run `resume-next` to create the owner-gated local test for the inspected target."
                )
            elif confidence_decision.get("decision") == "outcome_supported_direct_test":
                if confidence_decision.get("level") == "recovered_outcome_supported":
                    summary = (
                        "Recovered promoted confidence restored the direct narrow "
                        "pytest path after owner-approved recovery evidence."
                    )
                else:
                    summary = (
                        "Outcome-supported promoted confidence selected the direct narrow pytest path."
                    )
                next_step = "Owner approval is still required before running the pytest command."
        return {
            "state": state,
            "summary": summary,
            "next_step": next_step,
            "latest_mode": mode,
            "latest_action_id": action_id,
            **extra,
        }

    def _patch_mission_optional_write_content(self, action_id: str) -> str:
        try:
            return self._patch_mission_write_file_content(action_id)
        except (KeyError, ValueError, PermissionError):
            return ""

    def _patch_mission_first_action(
        self,
        *,
        repo_root: Path,
        repo_map: Any,
        mission_text: str,
        goal_id: str,
    ) -> ActionSpec:
        preferred = [
            "CONTRIBUTING.md",
            "AGENTS.md",
            "CODEX.md",
            "README.md",
            "pyproject.toml",
            "package.json",
        ]
        instruction_by_name = {
            Path(item).name: item for item in repo_map.instruction_files
        }
        for name in preferred:
            relative = instruction_by_name.get(name)
            if relative:
                target = repo_root / relative
                return ActionSpec(
                    tool="read_file",
                    arguments={"path": str(target), "max_bytes": 262144},
                    purpose=(
                        "Inspect repository contribution or project instructions "
                        f"for patch mission: {mission_text[:160]}"
                    ),
                    expected_result=(
                        "Bounded text from the repository instruction file for planning the patch mission"
                    ),
                    risk=RiskLevel.READ,
                    goal_id=goal_id,
                    acceptance=["output contains path", "output contains text or binary marker"],
                )
        return ActionSpec(
            tool="list_directory",
            arguments={"path": str(repo_root), "limit": 200},
            purpose=f"Inspect repository root for patch mission: {mission_text[:160]}",
            expected_result="Bounded repository root listing",
            risk=RiskLevel.READ,
            goal_id=goal_id,
            acceptance=["output contains items"],
        )

    def _patch_mission_followup_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        repo_map: Any,
        mode: str,
        target: str | None,
        draft: str | None,
        action_id: str | None,
    ) -> ActionSpec:
        normalized = str(mode or "auto").strip().lower()
        if normalized == "auto":
            normalized = "inspect-file" if target else "test"
        goal_id = str(mission_record.get("goal_id") or "")
        mission_text = str(mission_record.get("mission") or "")
        if normalized == "inspect-file":
            if not target:
                target = self._patch_mission_default_target(repo_map)
            target_path = self._patch_mission_repo_file(repo_root, str(target))
            return ActionSpec(
                tool="read_file",
                arguments={"path": str(target_path), "max_bytes": 262144},
                purpose=f"Inspect target file for patch mission: {mission_text[:160]}",
                expected_result="Bounded target file content for patch planning",
                risk=RiskLevel.READ,
                goal_id=goal_id or None,
                acceptance=["output contains path", "output contains text or binary marker"],
            )
        if normalized == "test":
            repo_state_digest = self._patch_mission_repo_state_digest(
                repo_root, repo_map
            )
            pytest_target = (
                self._patch_mission_safe_pytest_target(str(target), repo_map)
                if target
                else None
            )
            command = ["python", "-m", "pytest"]
            if pytest_target:
                command.append(pytest_target)
            return ActionSpec(
                tool="run_command",
                arguments={
                    "command": command,
                    "cwd": str(repo_root),
                    "timeout": 120,
                    "max_output_bytes": 524288,
                },
                purpose=(
                    f"Run the repository test probe for patch mission: {mission_text[:160]}"
                    + (f" target {pytest_target}" if pytest_target else "")
                ),
                expected_result="Bounded pytest output for deciding the smallest patch",
                risk=RiskLevel.HIGH,
                goal_id=goal_id or None,
                idempotency_key=digest_json(
                    {
                        "tool": "run_command",
                        "command": command,
                        "cwd": str(repo_root),
                        "repo_state_digest": repo_state_digest,
                    }
                ),
                acceptance=["output contains returncode"],
            )
        if normalized == "draft-patch":
            if not draft or not str(draft).strip():
                raise ValueError("draft-patch mode requires --draft content")
            outbox = (
                self.config.outbox_path
                / "patch-missions"
                / str(mission_record["mission_id"])
                / "patch-draft.diff"
            )
            return ActionSpec(
                tool="write_file",
                arguments={
                    "path": str(outbox),
                    "content": str(draft),
                    "max_bytes": 2 * 1024 * 1024,
                },
                purpose=(
                    "Write a patch draft to WLS outbox only; canonical repo remains unchanged"
                ),
                expected_result="Patch draft is available for owner review outside the repo",
                risk=RiskLevel.REVERSIBLE_WRITE,
                goal_id=goal_id or None,
                acceptance=["output contains path"],
            )
        if normalized == "from-test-result":
            return self._patch_mission_draft_from_test_result(
                mission_record=mission_record,
                repo_root=repo_root,
                repo_map=repo_map,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "apply-patch":
            return self._patch_mission_apply_patch_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "pr-summary":
            return self._patch_mission_pr_summary_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "git-metadata":
            return self._patch_mission_git_metadata_action(
                mission_record=mission_record,
                repo_root=repo_root,
                repo_map=repo_map,
                goal_id=goal_id or None,
            )
        if normalized == "git-prep":
            return self._patch_mission_git_prep_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "commit-draft":
            return self._patch_mission_commit_draft_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "remote-summary":
            return self._patch_mission_remote_summary_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "branch-draft":
            return self._patch_mission_branch_draft_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "remote-live":
            return self._patch_mission_remote_live_action(
                mission_record=mission_record,
                repo_root=repo_root,
                goal_id=goal_id or None,
            )
        if normalized == "remote-live-summary":
            return self._patch_mission_remote_live_summary_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "push-draft":
            return self._patch_mission_push_draft_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "pr-create-draft":
            return self._patch_mission_pr_create_draft_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "pr-status":
            return self._patch_mission_pr_status_action(
                mission_record=mission_record,
                target=target,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "pr-update-push-draft":
            return self._patch_mission_pr_update_push_draft_action(
                mission_record=mission_record,
                repo_root=repo_root,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "pr-update-status":
            return self._patch_mission_pr_update_status_action(
                mission_record=mission_record,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "pr-update-verify":
            return self._patch_mission_pr_update_verify_action(
                mission_record=mission_record,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "pr-update-next":
            return self._patch_mission_pr_update_next_action(
                mission_record=mission_record,
                repo_root=repo_root,
                repo_map=repo_map,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "ci-fix-plan":
            return self._patch_mission_ci_fix_plan_action(
                mission_record=mission_record,
                repo_root=repo_root,
                repo_map=repo_map,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "ci-log-evidence":
            return self._patch_mission_ci_log_evidence_action(
                mission_record=mission_record,
                action_id=action_id,
                goal_id=goal_id or None,
            )
        if normalized == "ci-next-action":
            return self._patch_mission_ci_next_action(
                mission_record=mission_record,
                repo_root=repo_root,
                repo_map=repo_map,
                action_id=action_id,
            )
        raise ValueError(f"unsupported patch mission step mode: {mode}")

    def _patch_mission_draft_from_test_result(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        repo_map: Any,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        source_action_id = self._patch_mission_test_action_id(
            mission_record, action_id
        )
        output = self._patch_mission_action_result_output(source_action_id)
        stdout = str(output.get("stdout") or "")
        stderr = str(output.get("stderr") or "")
        combined = "\n".join(part for part in (stdout, stderr) if part).strip()
        selected_file = self._patch_mission_failed_file(
            repo_root=repo_root,
            repo_map=repo_map,
            output_text=combined,
        )
        ci_source = self._patch_mission_ci_source_context_for_test_action(
            mission_record, source_action_id
        )
        prior_learning = self._patch_mission_failure_learning_context(
            mission_record=mission_record,
            query_text=combined,
            selected_file=selected_file,
            pytest_target=self._patch_mission_pytest_target_from_command(
                output.get("command")
            ),
        )
        repair_candidates = self._patch_mission_repair_skill_candidate_context()
        approved_repair_skills = self._patch_mission_approved_repair_skill_context(
            mission_record=mission_record,
            query_text=combined,
            selected_file=selected_file,
            pytest_target=self._patch_mission_pytest_target_from_command(
                output.get("command")
            ),
        )
        synthesis = self._patch_mission_synthesize_diff_from_test_result(
            repo_root=repo_root,
            repo_map=repo_map,
            selected_file=selected_file,
            output_text=combined,
        )
        learning = self._record_patch_mission_failure_learning(
            mission_record=mission_record,
            test_action_id=source_action_id,
            command=output.get("command"),
            returncode=output.get("returncode"),
            selected_file=selected_file,
            output_text=combined,
            synthesis=synthesis,
            ci_source=ci_source,
        )
        excerpt = self._bounded_test_excerpt(combined)
        returncode = output.get("returncode")
        command = output.get("command")
        skill_rationale = self._patch_mission_approved_repair_skill_rationale(
            approved_repair_skills,
            default_reason=(
                "No approved repair skill matched; draft relies on current test evidence "
                "and bounded Patch Mission synthesis."
            ),
            action_phrase="the outbox patch draft rationale",
        )
        outbox = (
            self.config.outbox_path
            / "patch-missions"
            / str(mission_record["mission_id"])
            / "test-result-patch-draft.md"
        )
        draft = "\n".join(
            [
                "# Patch Mission Draft From Approved Test Result",
                "",
                f"Mission: {mission_record.get('mission', '')}",
                f"Mission ID: {mission_record['mission_id']}",
                f"Approved test action: {source_action_id}",
                f"Command: {command}",
                f"Return code: {returncode}",
                f"Selected failing file: {selected_file}",
                f"Patch synthesis: {synthesis['status']}",
                f"Patch target: {synthesis.get('target_file') or '(not identified)'}",
                "",
                "Approved repair skill rationale:",
                skill_rationale,
                "",
                "PR/CI source evidence:",
                ci_source or "(not a CI-triggered local test action)",
                "",
                "Failure learning evidence:",
                f"- Evidence: {learning.get('evidence_id') or '(not recorded)'}",
                f"- Memory: {learning.get('memory_id') or '(not recorded)'}",
                f"- Cause: {learning.get('cause') or '(not identified)'}",
                f"- Fix: {learning.get('fix') or '(not identified)'}",
                f"- Regression: {learning.get('regression') or '(not identified)'}",
                "",
                "Prior failure-learning memory used:",
                *self._patch_mission_failure_learning_lines(prior_learning),
                "",
                "Reviewed repair skill candidate used:",
                *self._patch_mission_repair_skill_candidate_lines(repair_candidates),
                "",
                "Approved repair skill advisory context:",
                *self._patch_mission_approved_repair_skill_lines(
                    approved_repair_skills
                ),
                "",
                "Suggested next action:",
                str(synthesis["next_action"]),
                "- Keep the canonical repository unchanged until the owner approves an exact write action.",
                "",
                "Unified diff draft:",
                "```diff",
                str(synthesis["diff"]),
                "```",
                "",
                "Bounded failure excerpt:",
                "```text",
                excerpt or "(no test output captured)",
                "```",
                "",
                "Authority:",
                "- This draft is written to the WLS outbox only.",
                "- No repository file, commit, push, or PR is created by this step.",
            ]
        )
        return ActionSpec(
            tool="write_file",
            arguments={
                "path": str(outbox),
                "content": draft,
                "max_bytes": 2 * 1024 * 1024,
            },
            purpose=(
                "Write an outbox-only patch mission draft derived from approved local test evidence and prior failure memory"
            ),
            expected_result=(
                "Owner-reviewable diff draft points to the failing file without modifying the repo"
            ),
            risk=RiskLevel.REVERSIBLE_WRITE,
            goal_id=goal_id,
            acceptance=["output contains path"],
        )

    def _patch_mission_synthesize_diff_from_test_result(
        self,
        *,
        repo_root: Path,
        repo_map: Any,
        selected_file: str,
        output_text: str,
    ) -> dict[str, Any]:
        mismatch = self._patch_mission_pytest_literal_mismatch(output_text)
        if mismatch is None:
            return {
                "status": "needs_manual_patch",
                "target_file": None,
                "diff": "# No safe literal mismatch was found in the approved pytest output.",
                "next_action": (
                    f"- Inspect `{selected_file}` and draft the smallest patch manually from the failure evidence."
                ),
            }
        actual, expected = mismatch
        candidates = self._patch_mission_adjacent_source_candidates(
            repo_root=repo_root,
            repo_map=repo_map,
            selected_file=selected_file,
        )
        for candidate in candidates:
            target_path = self._patch_mission_repo_file(repo_root, candidate)
            try:
                original = self._read_patch_mission_text(target_path)
            except (UnicodeError, ValueError):
                continue
            replacements = [
                (f"'{actual}'", f"'{expected}'"),
                (f'"{actual}"', f'"{expected}"'),
            ]
            for old, new in replacements:
                if original.count(old) != 1:
                    continue
                revised = original.replace(old, new, 1)
                relative = str(target_path.relative_to(repo_root)).replace("\\", "/")
                return {
                    "status": "synthesized_literal_diff",
                    "target_file": relative,
                    "actual": actual,
                    "expected": expected,
                    "diff": self._unified_diff_for_patch_mission(
                        relative, original, revised
                    ),
                    "next_action": (
                        f"- Review the synthesized diff for `{relative}`, then approve an exact repo write only if it is correct."
                    ),
                }
        return {
            "status": "needs_manual_patch",
            "target_file": None,
            "actual": actual,
            "expected": expected,
            "diff": (
                "# Pytest exposed a literal mismatch, but no adjacent source file "
                "contained the actual literal exactly once in a safe replacement form."
            ),
            "next_action": (
                f"- Inspect `{selected_file}` and adjacent source files; expected `{expected}` but observed `{actual}`."
            ),
        }

    def _record_patch_mission_failure_learning(
        self,
        *,
        mission_record: dict[str, Any],
        test_action_id: str,
        command: Any,
        returncode: Any,
        selected_file: str,
        output_text: str,
        synthesis: dict[str, Any],
        ci_source: str,
    ) -> dict[str, Any]:
        pytest_target = self._patch_mission_pytest_target_from_command(command)
        cause = self._patch_mission_failure_cause_note(
            output_text=output_text,
            selected_file=selected_file,
            pytest_target=pytest_target,
            synthesis=synthesis,
        )
        fix = self._patch_mission_failure_fix_note(synthesis)
        regression = self._patch_mission_failure_regression_note(
            command=command,
            pytest_target=pytest_target,
        )
        learning = {
            "schema_version": 1,
            "mission_id": mission_record.get("mission_id"),
            "mission": mission_record.get("mission"),
            "test_action_id": test_action_id,
            "command": command,
            "returncode": returncode,
            "pytest_target": pytest_target,
            "selected_file": selected_file,
            "synthesis_status": synthesis.get("status"),
            "patch_target": synthesis.get("target_file"),
            "cause": cause,
            "fix": fix,
            "regression": regression,
            "ci_source": ci_source,
            "claim_ceiling": (
                "failure-learning note from one Patch Mission action; not a promoted skill"
            ),
        }
        signature = digest_json(
            {
                "mission_id": mission_record.get("mission_id"),
                "test_action_id": test_action_id,
                "synthesis_status": synthesis.get("status"),
                "patch_target": synthesis.get("target_file"),
                "cause": cause,
                "fix": fix,
                "regression": regression,
            }
        )
        recorded = self.db.get_runtime("patch_mission_failure_learning", [])
        if isinstance(recorded, list):
            for item in recorded:
                if isinstance(item, dict) and item.get("signature") == signature:
                    return {**learning, **item, "duplicate": True}
        else:
            recorded = []
        memory = MemoryItem(
            memory_type="procedural",
            content={
                "kind": "patch_mission_failure_learning",
                **learning,
            },
            importance=0.72 if synthesis.get("status") != "needs_manual_patch" else 0.58,
            confidence=0.78 if synthesis.get("status") != "needs_manual_patch" else 0.62,
            source_ids=[str(test_action_id)],
            tags=[
                "patch-mission",
                "failure-learning",
                str(synthesis.get("status") or "unknown"),
            ],
        )
        memory_id = self.memories.add(memory)
        evidence_id = self.ledger.append(
            "patch_mission_failure_learning_recorded",
            {
                **learning,
                "memory_id": memory_id,
                "signature": signature,
            },
        )
        record = {
            "signature": signature,
            "memory_id": memory_id,
            "evidence_id": evidence_id,
            "test_action_id": test_action_id,
            "created_at": utc_now(),
        }
        self.db.set_runtime(
            "patch_mission_failure_learning",
            [record, *recorded][:100],
        )
        return {**learning, **record, "duplicate": False}

    def _record_patch_mission_successful_repair_pattern(
        self,
        *,
        mission_record: dict[str, Any],
        failing_test_id: str,
        failing_output: dict[str, Any],
        passing_test_id: str,
        passing_output: dict[str, Any],
        apply_action_id: str,
        draft_action_id: str,
        ci_source: str,
    ) -> dict[str, Any]:
        learning = self._patch_mission_failure_learning_for_test(failing_test_id)
        if not learning:
            return {
                "available": False,
                "reason": "no failure-learning memory for failing test action",
            }
        memory_content = learning.get("memory_content")
        if not isinstance(memory_content, dict):
            return {
                "available": False,
                "reason": "failure-learning memory content is unavailable",
            }
        if memory_content.get("synthesis_status") != "synthesized_literal_diff":
            return {
                "available": False,
                "reason": "repair was not a synthesized literal diff",
            }
        pytest_target = memory_content.get("pytest_target")
        patch_target = memory_content.get("patch_target")
        pattern_family = "patch_mission_pytest_literal_mismatch_repair"
        signature = digest_json(
            {
                "pattern_family": pattern_family,
                "mission_id": mission_record.get("mission_id"),
                "failing_test_id": failing_test_id,
                "passing_test_id": passing_test_id,
                "apply_action_id": apply_action_id,
                "draft_action_id": draft_action_id,
                "memory_id": learning.get("memory_id"),
            }
        )
        recorded = self.db.get_runtime("patch_mission_successful_repair_learning", [])
        if not isinstance(recorded, list):
            recorded = []
        for item in recorded:
            if isinstance(item, dict) and item.get("signature") == signature:
                recovery = self._record_patch_mission_promoted_skill_recovery_outcome(
                    mission_record=mission_record,
                    success_record=item,
                    failing_test_id=failing_test_id,
                    passing_test_id=passing_test_id,
                    apply_action_id=apply_action_id,
                    draft_action_id=draft_action_id,
                )
                return {
                    **item,
                    "available": True,
                    "duplicate": True,
                    "promoted_skill_recovery": recovery,
                    "skill_candidate": self._record_patch_mission_repair_skill_candidate(
                        success_records=recorded
                    ),
                }
        record = {
            "schema_version": 1,
            "signature": signature,
            "pattern_family": pattern_family,
            "mission_id": mission_record.get("mission_id"),
            "mission": mission_record.get("mission"),
            "memory_id": learning.get("memory_id"),
            "failure_learning_evidence_id": learning.get("evidence_id"),
            "failing_test_action_id": failing_test_id,
            "failing_returncode": failing_output.get("returncode"),
            "passing_test_action_id": passing_test_id,
            "passing_returncode": passing_output.get("returncode"),
            "apply_action_id": apply_action_id,
            "draft_action_id": draft_action_id,
            "pytest_target": pytest_target,
            "selected_file": memory_content.get("selected_file"),
            "patch_target": patch_target,
            "cause": memory_content.get("cause"),
            "fix": memory_content.get("fix"),
            "regression": memory_content.get("regression"),
            "ci_source": ci_source,
            "created_at": utc_now(),
            "claim_ceiling": (
                "successful Patch Mission repair sample; supports a candidate only, "
                "not a promoted skill"
            ),
        }
        evidence_id = self.ledger.append(
            "patch_mission_repair_success_recorded",
            record,
        )
        record["success_evidence_id"] = evidence_id
        updated = [record, *recorded][:100]
        self.db.set_runtime("patch_mission_successful_repair_learning", updated)
        recovery = self._record_patch_mission_promoted_skill_recovery_outcome(
            mission_record=mission_record,
            success_record=record,
            failing_test_id=failing_test_id,
            passing_test_id=passing_test_id,
            apply_action_id=apply_action_id,
            draft_action_id=draft_action_id,
        )
        return {
            **record,
            "available": True,
            "duplicate": False,
            "promoted_skill_recovery": recovery,
            "skill_candidate": self._record_patch_mission_repair_skill_candidate(
                success_records=updated
            ),
        }

    def _record_patch_mission_promoted_skill_recovery_outcome(
        self,
        *,
        mission_record: dict[str, Any],
        success_record: dict[str, Any],
        failing_test_id: str,
        passing_test_id: str,
        apply_action_id: str,
        draft_action_id: str,
    ) -> dict[str, Any]:
        context = self._patch_mission_fallback_recovery_context_for_test(
            mission_record=mission_record,
            test_action_id=failing_test_id,
        )
        if context is None:
            return {
                "available": False,
                "reason": "failing test was not a promoted-confidence fallback-restored verification",
            }
        required_approvals = {
            "failing_test_action_id": failing_test_id,
            "draft_action_id": draft_action_id,
            "apply_action_id": apply_action_id,
            "passing_test_action_id": passing_test_id,
        }
        approval_checks = {
            name: self._patch_mission_action_has_consumed_owner_approval(action_id)
            for name, action_id in required_approvals.items()
        }
        if not all(approval_checks.values()):
            return {
                "available": False,
                "reason": "recovery requires owner-approved failing, draft, apply, and passing actions",
                "approval_checks": approval_checks,
            }
        skill_id = str(context.get("skill_id") or "")
        signature = digest_json(
            {
                "receipt_type": "PATCH_MISSION_PROMOTED_REPAIR_SKILL_RECOVERY_OUTCOME",
                "skill_id": skill_id,
                "success_signature": success_record.get("signature"),
                "failing_test_id": failing_test_id,
                "passing_test_id": passing_test_id,
                "apply_action_id": apply_action_id,
                "draft_action_id": draft_action_id,
            }
        )
        recorded = self.db.get_runtime(
            "patch_mission_promoted_skill_recovery_outcomes", []
        )
        if not isinstance(recorded, list):
            recorded = []
        for item in recorded:
            if isinstance(item, dict) and item.get("signature") == signature:
                return {**item, "available": True, "duplicate": True}
        receipt = {
            "schema_version": 1,
            "receipt_type": "PATCH_MISSION_PROMOTED_REPAIR_SKILL_RECOVERY_OUTCOME",
            "status": "RECORDED",
            "signature": signature,
            "skill_id": skill_id,
            "success": True,
            "approval_valid": True,
            "mission_id": mission_record.get("mission_id"),
            "successful_repair_signature": success_record.get("signature"),
            "success_evidence_id": success_record.get("success_evidence_id"),
            "failing_test_action_id": failing_test_id,
            "passing_test_action_id": passing_test_id,
            "apply_action_id": apply_action_id,
            "draft_action_id": draft_action_id,
            "fallback_inspect_action_id": context.get("fallback_inspect_action_id"),
            "source_action_id": context.get("source_action_id"),
            "recovered_target": context.get("target"),
            "confidence_level_at_fallback": context.get("confidence_level"),
            "fallback_decision": context.get("fallback_decision"),
            "target_decision": context.get("target_decision"),
            "approval_checks": approval_checks,
            "failed_or_rejected_evidence_preserved": True,
            "promotion_executed": False,
            "repo_write_executed": False,
            "push_or_pr_executed": False,
            "claim_ceiling": (
                "fallback-restored successful repair outcome adjusts future planning "
                "confidence only; it does not delete failures, execute a skill, bypass "
                "approval, write the repo, push, or open a PR"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        updated = [receipt, *recorded][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "patch_mission_promoted_skill_recovery_outcomes",
                updated,
                connection,
            )
            self.ledger.append(
                "patch_mission_promoted_repair_skill_recovery_recorded",
                receipt,
                connection,
            )
        return {**receipt, "available": True, "duplicate": False}

    def _patch_mission_fallback_recovery_context_for_test(
        self, *, mission_record: dict[str, Any], test_action_id: str
    ) -> dict[str, Any] | None:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            return None
        test_step = next(
            (
                item
                for item in followups
                if isinstance(item, dict)
                and str(item.get("mode", "")).lower() == "test"
                and str(item.get("action_id", "")) == str(test_action_id)
            ),
            None,
        )
        if not isinstance(test_step, dict):
            return None
        resume_next = test_step.get("resume_next")
        if not isinstance(resume_next, dict):
            return None
        confidence_decision = resume_next.get("confidence_decision")
        target_decision = resume_next.get("target_decision")
        if not isinstance(confidence_decision, dict) or not isinstance(
            target_decision, dict
        ):
            return None
        if confidence_decision.get("decision") != "safety_fallback_inspect_file":
            return None
        if target_decision.get("decision") != "nodeid_restored_from_ci_and_inspection":
            return None
        skill_id = str(confidence_decision.get("skill_id") or "")
        if not skill_id:
            return None
        return {
            "skill_id": skill_id,
            "confidence_level": confidence_decision.get("level"),
            "fallback_decision": confidence_decision.get("decision"),
            "target_decision": target_decision.get("decision"),
            "target": resume_next.get("target"),
            "fallback_inspect_action_id": resume_next.get("from_action_id"),
            "source_action_id": confidence_decision.get("source_action_id"),
        }

    def _patch_mission_action_has_consumed_owner_approval(
        self, action_id: str
    ) -> bool:
        row = self.db.query_one(
            "SELECT status,approval_id FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None or row["status"] != ActionStatus.SUCCEEDED.value:
            return False
        approval_id = str(row["approval_id"] or "")
        if not approval_id:
            return False
        approval = self.db.query_one(
            "SELECT decision,consumed_at FROM approvals WHERE approval_id=?",
            (approval_id,),
        )
        return bool(
            approval is not None
            and approval["decision"] == "APPROVE"
            and approval["consumed_at"]
        )

    def _patch_mission_failure_learning_for_test(
        self, test_action_id: str
    ) -> dict[str, Any] | None:
        records = self.db.get_runtime("patch_mission_failure_learning", [])
        if not isinstance(records, list):
            return None
        for item in records:
            if not isinstance(item, dict):
                continue
            if item.get("test_action_id") != test_action_id:
                continue
            memory_id = str(item.get("memory_id") or "")
            if not memory_id:
                continue
            row = self.db.query_one(
                "SELECT content_json FROM memories WHERE memory_id=?",
                (memory_id,),
            )
            if row is None:
                continue
            try:
                content = json.loads(row["content_json"])
            except json.JSONDecodeError:
                continue
            if isinstance(content, dict):
                return {**content, **item, "memory_content": content}
            return {**item, "memory_content": content}
        return None

    def _record_patch_mission_repair_skill_candidate(
        self, *, success_records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        pattern_family = "patch_mission_pytest_literal_mismatch_repair"
        relevant = [
            record
            for record in success_records
            if isinstance(record, dict)
            and record.get("pattern_family") == pattern_family
            and record.get("passing_returncode") == 0
            and record.get("patch_target")
        ]
        if len(relevant) < 2:
            return {
                "available": False,
                "reason": "fewer than two successful literal-mismatch repairs",
                "repeat_count": len(relevant),
            }
        source_ids: list[str] = []
        for record in relevant[:5]:
            for key in (
                "success_evidence_id",
                "failure_learning_evidence_id",
                "memory_id",
                "failing_test_action_id",
                "draft_action_id",
                "apply_action_id",
                "passing_test_action_id",
            ):
                value = record.get(key)
                if value:
                    source_ids.append(str(value))
        source_ids = list(dict.fromkeys(source_ids))
        proposal = {
            "schema_version": 1,
            "candidate_only": True,
            "pattern_family": pattern_family,
            "minimum_success_count": 2,
            "observed_success_count": len(relevant),
            "trigger": (
                "Patch Mission has owner-approved failing pytest evidence with a "
                "literal expected/actual mismatch and a synthesized single-file diff."
            ),
            "steps": [
                "Inspect PR/local failure evidence and choose the narrow pytest target.",
                "Run the owner-approved local pytest reproduction.",
                "Record cause, fix, and regression notes from the failing output.",
                "Draft an outbox-only unified diff for owner review.",
                "Apply the exact approved diff to the canonical repo file.",
                "Rerun the narrow pytest target, then prepare the outbox PR summary.",
            ],
            "tools": [
                "inspect_github_pr_status",
                "inspect_github_ci_logs",
                "run_command: python -m pytest <target>",
                "write_file: WLS outbox draft",
                "write_file: owner-approved repo patch",
            ],
            "risks": [
                "GitHub and local command actions remain owner-approved HIGH actions.",
                "Repo writes are exact approved writes only.",
                "No commit, push, PR creation, or skill promotion is authorized by this candidate.",
            ],
            "verification": [
                "Passing pytest action must return 0 after the approved patch.",
                "PR summary must cite failing evidence, applied patch, and passing verification.",
            ],
            "rollback": [
                "Use the recorded outbox diff and action evidence to review or reverse the file write.",
                "If later committed, use normal git revert/reset under owner approval.",
            ],
            "owner_review_required": True,
            "claim_ceiling": (
                "PROPOSED repair skill candidate only; no sandbox, approval, promotion, "
                "active skill, or external authority"
            ),
        }
        candidate_type = "patch_mission_repair_skill"
        title = "Candidate skill: repair Patch Mission pytest literal mismatch"
        candidate_id = self.learning._upsert_candidate(
            candidate_type=candidate_type,
            title=title,
            proposal=proposal,
            source_ids=source_ids,
        )
        created = candidate_id is not None
        if candidate_id is None:
            candidate_id = self._patch_mission_existing_evolution_candidate_id(
                candidate_type=candidate_type,
                proposal=proposal,
            )
        return {
            "available": bool(candidate_id),
            "candidate_id": candidate_id,
            "created": created,
            "candidate_type": candidate_type,
            "repeat_count": len(relevant),
            "source_ids": source_ids,
            "claim_ceiling": proposal["claim_ceiling"],
        }

    def _patch_mission_existing_evolution_candidate_id(
        self, *, candidate_type: str, proposal: dict[str, Any]
    ) -> str | None:
        fingerprint = json.dumps(
            {"type": candidate_type, "proposal": proposal}, sort_keys=True
        )
        row = self.db.query_one(
            """
            SELECT candidate_id FROM evolution_candidates
            WHERE candidate_type=? AND proposal_json=? AND status NOT IN ('REJECTED','ROLLED_BACK')
            ORDER BY created_at DESC LIMIT 1
            """,
            (candidate_type, fingerprint),
        )
        if row is None:
            return None
        return str(row["candidate_id"])

    def review_patch_mission_repair_skill_candidate(
        self, *, candidate_id: str, reason: str = ""
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if row is None:
            raise KeyError(f"unknown candidate: {candidate_id}")
        if row["candidate_type"] != "patch_mission_repair_skill":
            raise ValueError("candidate is not a Patch Mission repair skill candidate")
        if row["status"] in {CandidateStatus.REJECTED.value, CandidateStatus.ROLLED_BACK.value}:
            raise ValueError(f"candidate cannot be replayed from status {row['status']}")
        proposal_envelope = json.loads(str(row["proposal_json"]))
        proposal = proposal_envelope.get("proposal")
        if not isinstance(proposal, dict):
            raise ValueError("candidate proposal is malformed")
        source_ids = json.loads(str(row["source_ids_json"]))
        if not isinstance(source_ids, list):
            source_ids = []
        source_id_set = {str(item) for item in source_ids}
        success_records = self._patch_mission_candidate_success_records(source_id_set)
        sample_reviews = [
            self._patch_mission_replay_success_record(record, source_id_set)
            for record in success_records
        ]
        passed_samples = [
            item for item in sample_reviews if item.get("status") == "SAMPLE_PASSED"
        ]
        required_fields = [
            "trigger",
            "steps",
            "tools",
            "risks",
            "verification",
            "rollback",
        ]
        proposal_complete = (
            proposal.get("candidate_only") is True
            and proposal.get("owner_review_required") is True
            and all(proposal.get(field) for field in required_fields)
        )
        replay_passed = proposal_complete and len(passed_samples) >= 2
        receipt = {
            "receipt_type": "PATCH_MISSION_REPAIR_SKILL_CANDIDATE_REPLAY",
            "status": "REPLAY_PASSED" if replay_passed else "REPLAY_FAILED",
            "candidate_id": candidate_id,
            "candidate_type": row["candidate_type"],
            "candidate_status_before": row["status"],
            "candidate_status_after": row["status"],
            "reason": reason,
            "proposal_complete": proposal_complete,
            "required_fields": required_fields,
            "source_id_count": len(source_id_set),
            "sample_count": len(sample_reviews),
            "passed_sample_count": len(passed_samples),
            "samples": sample_reviews[:10],
            "candidate_only": True,
            "approval_executed": False,
            "promotion_executed": False,
            "active_skill_created": False,
            "command_executed": False,
            "external_action_executed": False,
            "claim_ceiling": (
                "evidence replay for Patch Mission repair skill candidate only; "
                "no status transition, sandbox execution, approval, promotion, "
                "active skill, command, push, or PR is inferred"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.db.get_runtime(
            "patch_mission_repair_skill_candidate_reviews", []
        )
        if not isinstance(current, list):
            current = []
        updated = [
            receipt,
            *[
                item
                for item in current
                if isinstance(item, dict)
                and item.get("candidate_id") != candidate_id
            ],
        ][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "patch_mission_repair_skill_candidate_reviews",
                updated,
                connection,
            )
            self.ledger.append(
                "patch_mission_repair_skill_candidate_replayed",
                receipt,
                connection,
            )
        return receipt

    def sandbox_patch_mission_repair_skill_candidate(
        self,
        *,
        candidate_id: str,
        reason: str = "",
        owner_approved: bool = False,
    ) -> dict[str, Any]:
        if not owner_approved:
            raise PermissionError("Patch Mission repair candidate sandbox requires owner approval")
        row = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if row is None:
            raise KeyError(f"unknown candidate: {candidate_id}")
        if row["candidate_type"] != "patch_mission_repair_skill":
            raise ValueError("candidate is not a Patch Mission repair skill candidate")
        if row["status"] not in {
            CandidateStatus.PROPOSED.value,
            CandidateStatus.SANDBOXED.value,
        }:
            raise ValueError(f"candidate cannot be sandboxed from status {row['status']}")
        replay = self._patch_mission_repair_candidate_latest_replay(candidate_id)
        if replay is None or replay.get("status") != "REPLAY_PASSED":
            raise ValueError("candidate sandbox requires a passing replay receipt first")
        samples = replay.get("samples", [])
        sample = next(
            (
                item
                for item in samples
                if isinstance(item, dict)
                and item.get("status") == "SAMPLE_PASSED"
            ),
            None,
        )
        if not isinstance(sample, dict):
            raise ValueError("candidate replay has no passing sample")
        literal_pair = self._patch_mission_literal_pair_from_sample(sample)
        if literal_pair is None:
            raise ValueError("candidate sample does not expose a literal mismatch pair")
        actual, expected = literal_pair
        experiment_id = new_id("patch_repair_sandbox")
        artifact_dir = (
            self.config.sandbox_path
            / "patch-mission-repair-candidates"
            / experiment_id
        )
        repo_root = artifact_dir / "repo"
        tests_root = repo_root / "tests"
        tests_root.mkdir(parents=True, exist_ok=False)
        patch_target = str(sample.get("patch_target") or "demo.py").replace("\\", "/")
        patch_path = repo_root / patch_target
        if not self.policy._contained(patch_path.resolve(strict=False), repo_root.resolve(strict=False)):
            raise PermissionError("sandbox patch target must stay inside sandbox repo")
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        module_name = Path(patch_target).with_suffix("").name or "demo"
        function_name = "greet"
        patch_path.write_text(
            f"def {function_name}():\n    return {actual!r}\n",
            encoding="utf-8",
        )
        test_file = str(sample.get("pytest_target") or "tests/test_demo.py").split(
            "::", 1
        )[0]
        test_path = repo_root / test_file
        if not self.policy._contained(test_path.resolve(strict=False), repo_root.resolve(strict=False)):
            raise PermissionError("sandbox test target must stay inside sandbox repo")
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(
            f"from {module_name} import {function_name}\n\n"
            "def test_demo():\n"
            f"    assert {function_name}() == {expected!r}\n",
            encoding="utf-8",
        )
        (repo_root / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\n",
            encoding="utf-8",
        )
        pytest_target = str(sample.get("pytest_target") or test_file)
        failing = self._run_patch_mission_sandbox_pytest(
            repo_root=repo_root, pytest_target=pytest_target
        )
        repo_map = self.repo_explorer.explore(repo_root)
        selected_file = test_file.replace("\\", "/")
        synthesis = self._patch_mission_synthesize_diff_from_test_result(
            repo_root=repo_root,
            repo_map=repo_map,
            selected_file=selected_file,
            output_text="\n".join(
                part
                for part in (failing.get("stdout", ""), failing.get("stderr", ""))
                if part
            ),
        )
        applied_file = None
        if synthesis.get("status") == "synthesized_literal_diff":
            applied_file, revised = self._apply_single_file_unified_diff(
                repo_root=repo_root,
                diff_text=str(synthesis.get("diff") or ""),
            )
            (repo_root / applied_file).write_text(revised, encoding="utf-8")
        passing = self._run_patch_mission_sandbox_pytest(
            repo_root=repo_root, pytest_target=pytest_target
        )
        passed = (
            int(failing.get("returncode", -1)) != 0
            and synthesis.get("status") == "synthesized_literal_diff"
            and applied_file == patch_target
            and int(passing.get("returncode", -1)) == 0
        )
        manifest = {
            "experiment_id": experiment_id,
            "candidate_id": candidate_id,
            "candidate_type": row["candidate_type"],
            "reason": reason,
            "mode": "disposable_fixture_repo",
            "artifact_dir": str(artifact_dir),
            "repo_root": str(repo_root),
            "pytest_target": pytest_target,
            "patch_target": patch_target,
            "actual": actual,
            "expected": expected,
            "source_replay_digest": replay.get("receipt_digest"),
            "owner_approved": True,
            "canonical_repo_write_executed": False,
            "external_action_executed": False,
            "promotion_executed": False,
            "created_at": utc_now(),
        }
        result = {
            "passed": passed,
            "failing_returncode": failing.get("returncode"),
            "passing_returncode": passing.get("returncode"),
            "synthesis_status": synthesis.get("status"),
            "applied_file": applied_file,
            "diff_sha256": hashlib.sha256(
                str(synthesis.get("diff") or "").encode("utf-8")
            ).hexdigest(),
            "failing_stdout_excerpt": self._bounded_test_excerpt(
                str(failing.get("stdout") or ""), limit=1200
            ),
            "passing_stdout_excerpt": self._bounded_test_excerpt(
                str(passing.get("stdout") or ""), limit=1200
            ),
        }
        manifest_sha256 = digest_json(manifest)
        result_sha256 = digest_json(result)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (artifact_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        receipt = {
            "receipt_type": "PATCH_MISSION_REPAIR_SKILL_CANDIDATE_SANDBOX",
            "status": "SANDBOX_PASSED" if passed else "SANDBOX_FAILED",
            "candidate_id": candidate_id,
            "experiment_id": experiment_id,
            "candidate_status_before": row["status"],
            "candidate_status_after": CandidateStatus.SANDBOXED.value
            if row["status"] == CandidateStatus.PROPOSED.value
            else row["status"],
            "manifest_path": str(artifact_dir / "manifest.json"),
            "result_path": str(artifact_dir / "result.json"),
            "manifest_sha256": manifest_sha256,
            "result_sha256": result_sha256,
            "result": result,
            "candidate_only": True,
            "owner_approved": True,
            "command_executed": True,
            "sandbox_only": True,
            "canonical_repo_write_executed": False,
            "external_action_executed": False,
            "approval_transition_executed": False,
            "promotion_executed": False,
            "active_skill_created": False,
            "claim_ceiling": (
                "owner-approved disposable sandbox validation only; no canonical repo "
                "write, external action, approval transition, promotion, or active skill"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.db.get_runtime(
            "patch_mission_repair_skill_candidate_sandboxes", []
        )
        if not isinstance(current, list):
            current = []
        updated = [
            receipt,
            *[
                item
                for item in current
                if isinstance(item, dict)
                and item.get("candidate_id") != candidate_id
            ],
        ][:100]
        with self.db.transaction() as connection:
            if row["status"] == CandidateStatus.PROPOSED.value:
                connection.execute(
                    "UPDATE evolution_candidates SET status=?,experiment_json=?,updated_at=? WHERE candidate_id=?",
                    (
                        CandidateStatus.SANDBOXED.value,
                        json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                        utc_now(),
                        candidate_id,
                    ),
                )
                self.ledger.append(
                    "evolution_candidate_transition",
                    {
                        "candidate_id": candidate_id,
                        "from": row["status"],
                        "to": CandidateStatus.SANDBOXED.value,
                        "evidence": {
                            "experiment_id": experiment_id,
                            "manifest_sha256": manifest_sha256,
                            "result_sha256": result_sha256,
                            "owner_approved": True,
                        },
                    },
                    connection,
                )
            else:
                connection.execute(
                    "UPDATE evolution_candidates SET experiment_json=?,updated_at=? WHERE candidate_id=?",
                    (
                        json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                        utc_now(),
                        candidate_id,
                    ),
                )
            self.db.set_runtime(
                "patch_mission_repair_skill_candidate_sandboxes",
                updated,
                connection,
            )
            self.ledger.append(
                "patch_mission_repair_skill_candidate_sandboxed",
                receipt,
                connection,
            )
        return receipt

    def validate_patch_mission_repair_skill_candidate(
        self,
        *,
        candidate_id: str,
        reason: str = "",
        human_approved: bool = False,
    ) -> dict[str, Any]:
        if not human_approved:
            raise PermissionError(
                "Patch Mission repair candidate validation requires human approval"
            )
        row = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if row is None:
            raise KeyError(f"unknown candidate: {candidate_id}")
        if row["candidate_type"] != "patch_mission_repair_skill":
            raise ValueError("candidate is not a Patch Mission repair skill candidate")
        if row["status"] != CandidateStatus.SANDBOXED.value:
            raise ValueError(
                f"candidate validation requires SANDBOXED status, got {row['status']}"
            )
        if not row["experiment_json"]:
            raise ValueError("candidate has no sandbox experiment evidence")
        sandbox_receipt = json.loads(str(row["experiment_json"]))
        if sandbox_receipt.get("status") != "SANDBOX_PASSED":
            raise ValueError("candidate sandbox did not pass")
        if sandbox_receipt.get("candidate_id") != candidate_id:
            raise ValueError("sandbox receipt belongs to a different candidate")
        manifest_path = Path(str(sandbox_receipt.get("manifest_path") or "")).resolve(
            strict=True
        )
        result_path = Path(str(sandbox_receipt.get("result_path") or "")).resolve(
            strict=True
        )
        sandbox_root = self.config.sandbox_path.resolve(strict=False)
        if not self.policy._contained(manifest_path, sandbox_root):
            raise PermissionError("sandbox manifest must stay inside sandbox path")
        if not self.policy._contained(result_path, sandbox_root):
            raise PermissionError("sandbox result must stay inside sandbox path")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        manifest_sha256 = digest_json(manifest)
        result_sha256 = digest_json(result)
        if sandbox_receipt.get("manifest_sha256") != manifest_sha256:
            raise ValueError("sandbox manifest digest mismatch")
        if sandbox_receipt.get("result_sha256") != result_sha256:
            raise ValueError("sandbox result digest mismatch")
        if manifest.get("candidate_id") != candidate_id:
            raise ValueError("sandbox manifest belongs to a different candidate")
        if manifest.get("experiment_id") != sandbox_receipt.get("experiment_id"):
            raise ValueError("sandbox manifest experiment id mismatch")
        if not result.get("passed"):
            raise ValueError("sandbox result did not pass")
        validation = {
            "receipt_type": "PATCH_MISSION_REPAIR_SKILL_CANDIDATE_VALIDATION",
            "status": "VALIDATED",
            "candidate_id": candidate_id,
            "candidate_type": row["candidate_type"],
            "candidate_status_before": row["status"],
            "candidate_status_after": CandidateStatus.VALIDATED.value,
            "experiment_id": sandbox_receipt.get("experiment_id"),
            "reason": reason,
            "manifest_sha256": manifest_sha256,
            "result_sha256": result_sha256,
            "sandbox_receipt_digest": sandbox_receipt.get("receipt_digest"),
            "validated_result": {
                "passed": bool(result.get("passed")),
                "failing_returncode": result.get("failing_returncode"),
                "passing_returncode": result.get("passing_returncode"),
                "synthesis_status": result.get("synthesis_status"),
                "applied_file": result.get("applied_file"),
            },
            "human_approved": True,
            "candidate_only": True,
            "approval_transition_executed": False,
            "promotion_executed": False,
            "active_skill_created": False,
            "command_executed": False,
            "canonical_repo_write_executed": False,
            "external_action_executed": False,
            "claim_ceiling": (
                "human-approved validation of disposable sandbox evidence only; "
                "no approval transition, promotion, active skill, command, "
                "canonical repo write, push, or PR is inferred"
            ),
            "created_at": utc_now(),
        }
        validation["receipt_digest"] = digest_json(validation)
        current = self.db.get_runtime(
            "patch_mission_repair_skill_candidate_validations", []
        )
        if not isinstance(current, list):
            current = []
        updated = [
            validation,
            *[
                item
                for item in current
                if isinstance(item, dict)
                and item.get("candidate_id") != candidate_id
            ],
        ][:100]
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE evolution_candidates SET status=?,result_json=?,updated_at=? WHERE candidate_id=?",
                (
                    CandidateStatus.VALIDATED.value,
                    json.dumps(validation, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    candidate_id,
                ),
            )
            self.db.set_runtime(
                "patch_mission_repair_skill_candidate_validations",
                updated,
                connection,
            )
            self.ledger.append(
                "evolution_candidate_transition",
                {
                    "candidate_id": candidate_id,
                    "from": row["status"],
                    "to": CandidateStatus.VALIDATED.value,
                    "evidence": {
                        "experiment_id": sandbox_receipt.get("experiment_id"),
                        "manifest_sha256": manifest_sha256,
                        "result_sha256": result_sha256,
                        "human_approved": True,
                    },
                },
                connection,
            )
            self.ledger.append(
                "patch_mission_repair_skill_candidate_validated",
                validation,
                connection,
            )
        return validation

    def propose_patch_mission_repair_skill_from_candidate(
        self, *, candidate_id: str, reason: str = ""
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if row is None:
            raise KeyError(f"unknown candidate: {candidate_id}")
        if row["candidate_type"] != "patch_mission_repair_skill":
            raise ValueError("candidate is not a Patch Mission repair skill candidate")
        if row["status"] != CandidateStatus.VALIDATED.value:
            raise ValueError(
                f"skill proposal requires VALIDATED candidate, got {row['status']}"
            )
        proposal_envelope = json.loads(str(row["proposal_json"]))
        proposal = proposal_envelope.get("proposal")
        if not isinstance(proposal, dict):
            raise ValueError("candidate proposal is malformed")
        validation = json.loads(str(row["result_json"] or "{}"))
        if validation.get("status") != "VALIDATED":
            raise ValueError("candidate validation evidence is missing")
        source_ids = json.loads(str(row["source_ids_json"]))
        if not isinstance(source_ids, list):
            source_ids = []
        source_episode_ids = list(
            dict.fromkeys(
                [
                    candidate_id,
                    str(validation.get("receipt_digest") or ""),
                    str(validation.get("experiment_id") or ""),
                    *[str(item) for item in source_ids],
                ]
            )
        )
        source_episode_ids = [item for item in source_episode_ids if item]
        skill_name = "patch_mission_repair_pytest_literal_mismatch"
        existing = self.db.query_one(
            "SELECT skill_id,status,definition_json FROM skills WHERE name=? ORDER BY version DESC LIMIT 1",
            (skill_name,),
        )
        if existing is not None:
            definition = json.loads(str(existing["definition_json"]))
            return {
                "receipt_type": "PATCH_MISSION_REPAIR_SKILL_PROPOSAL",
                "status": "EXISTING_PROPOSAL",
                "skill_id": existing["skill_id"],
                "skill_status": existing["status"],
                "definition": definition,
                "candidate_id": candidate_id,
                "candidate_status": row["status"],
                "created": False,
                "candidate_only": True,
                "active_skill_created": existing["status"]
                in {CandidateStatus.APPROVED.value, CandidateStatus.PROMOTED.value},
                "promotion_executed": False,
                "created_at": utc_now(),
            }
        steps = [
            {
                "tool": "inspect_github_pr_status",
                "arguments": {
                    "owner": "<owner>",
                    "repo": "<repo>",
                    "number": "<pull_request_number>",
                    "token_env": "GITHUB_TOKEN",
                },
                "purpose": "Read PR status and bounded CI failure summaries for a Patch Mission.",
            },
            {
                "tool": "inspect_github_ci_logs",
                "arguments": {
                    "owner": "<owner>",
                    "repo": "<repo>",
                    "workflow_run_ids": "<failed_run_ids>",
                    "token_env": "GITHUB_TOKEN",
                    "max_runs": 3,
                    "max_jobs": 5,
                    "max_log_bytes": 65536,
                },
                "purpose": "Capture bounded failed job excerpts before choosing a local repair.",
            },
            {
                "tool": "run_command",
                "arguments": {
                    "command": ["python", "-m", "pytest", "<safe_pytest_target>"],
                    "cwd": "<repo_root>",
                    "timeout": 120,
                },
                "purpose": "Owner-approved narrow local pytest reproduction.",
            },
            {
                "tool": "write_file",
                "arguments": {
                    "path": "<wls_outbox>/patch-missions/<mission_id>/test-result-patch-draft.md",
                    "content": "<owner_reviewable_unified_diff_draft>",
                },
                "purpose": "Write outbox-only patch draft from approved failure evidence.",
            },
            {
                "tool": "write_file",
                "arguments": {
                    "path": "<repo_root>/<patch_target>",
                    "content": "<approved_revised_file_content>",
                },
                "purpose": "Apply exact owner-approved single-file repair to the canonical repo.",
            },
            {
                "tool": "run_command",
                "arguments": {
                    "command": ["python", "-m", "pytest", "<safe_pytest_target>"],
                    "cwd": "<repo_root>",
                    "timeout": 120,
                },
                "purpose": "Owner-approved verification after the exact patch.",
            },
        ]
        description = "\n".join(
            [
                "Repair a Patch Mission pytest literal mismatch through the bounded PR/CI loop.",
                f"Trigger: {proposal.get('trigger')}",
                "Risks: " + "; ".join(str(item) for item in proposal.get("risks", [])),
                "Verification: "
                + "; ".join(str(item) for item in proposal.get("verification", [])),
                "Rollback: "
                + "; ".join(str(item) for item in proposal.get("rollback", [])),
                "Claim ceiling: proposed declarative skill only; not active or promoted.",
            ]
        )
        skill = SkillDefinition(
            name=skill_name,
            description=description,
            trigger_terms=[
                "patch",
                "mission",
                "pytest",
                "literal",
                "mismatch",
                "ci",
                "github",
                "repair",
            ],
            steps=steps,
            risk=RiskLevel.HIGH,
            status=CandidateStatus.PROPOSED,
            source_episode_ids=source_episode_ids,
        )
        skill_id = self.skills.add(skill)
        row_after = self.db.query_one(
            "SELECT skill_id,status,definition_json FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        definition = (
            json.loads(str(row_after["definition_json"])) if row_after is not None else {}
        )
        receipt = {
            "receipt_type": "PATCH_MISSION_REPAIR_SKILL_PROPOSAL",
            "status": "SKILL_PROPOSED",
            "skill_id": skill_id,
            "skill_status": CandidateStatus.PROPOSED.value,
            "candidate_id": candidate_id,
            "candidate_status": row["status"],
            "reason": reason,
            "definition": definition,
            "source_episode_ids": source_episode_ids,
            "candidate_only": True,
            "active_skill_created": False,
            "approval_executed": False,
            "promotion_executed": False,
            "command_executed": False,
            "external_action_executed": False,
            "claim_ceiling": (
                "PROPOSED declarative skill only; no approval, promotion, active use, "
                "command, repo write, push, or PR is inferred"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.skill_candidate_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("skill_candidate_receipts", updated, connection)
            self.ledger.append(
                "patch_mission_repair_skill_proposed",
                receipt,
                connection,
            )
        return receipt

    def start_patch_mission_repair_skill_sandbox(
        self, *, skill_id: str, reason: str = ""
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT skill_id,name,version,status,definition_json FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        if row is None:
            raise KeyError(f"unknown skill: {skill_id}")
        if row["name"] != "patch_mission_repair_pytest_literal_mismatch":
            raise ValueError("skill is not a Patch Mission repair proposal")
        if row["status"] != CandidateStatus.PROPOSED.value:
            raise ValueError(
                f"Patch Mission repair skill sandbox requires PROPOSED status, got {row['status']}"
            )
        definition = json.loads(str(row["definition_json"]))
        candidate_id = next(
            (
                str(item)
                for item in definition.get("source_episode_ids", [])
                if str(item).startswith("candidate_")
            ),
            "",
        )
        if not candidate_id:
            raise ValueError("Patch Mission repair skill proposal has no source candidate")
        candidate_row = self.db.query_one(
            "SELECT candidate_id,status,result_json FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if candidate_row is None:
            raise ValueError("source candidate does not exist")
        if candidate_row["status"] != CandidateStatus.VALIDATED.value:
            raise ValueError("source candidate must be VALIDATED before skill sandbox")
        validation = json.loads(str(candidate_row["result_json"] or "{}"))
        if validation.get("status") != "VALIDATED":
            raise ValueError("source candidate validation evidence is missing")
        experiment_id = new_id("patch_skill_exp")
        artifact_dir = self.config.sandbox_path / "patch-mission-skills" / experiment_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "experiment_id": experiment_id,
            "skill_id": skill_id,
            "skill_name": row["name"],
            "skill_version": int(row["version"]),
            "definition_sha256": digest_json(definition),
            "source_candidate_id": candidate_id,
            "source_candidate_status": candidate_row["status"],
            "source_validation_digest": validation.get("receipt_digest"),
            "source_sandbox_experiment_id": validation.get("experiment_id"),
            "reason": reason,
            "mode": "patch_mission_repair_skill_sandbox_start",
            "candidate_cases": [
                {
                    "case_id": "validated-candidate",
                    "candidate_id": candidate_id,
                    "validation_digest": validation.get("receipt_digest"),
                    "result_sha256": validation.get("result_sha256"),
                }
            ],
            "allowed_tools": [
                "inspect_github_pr_status",
                "inspect_github_ci_logs",
                "run_command",
                "write_file",
            ],
            "authority": {
                "active_skill_created": False,
                "approval_executed": False,
                "promotion_executed": False,
                "command_executed": False,
                "external_action_executed": False,
                "canonical_repo_write_executed": False,
            },
            "created_at": utc_now(),
        }
        baseline = {
            "source": "validated_patch_mission_repair_candidate",
            "candidate_id": candidate_id,
            "candidate_status": candidate_row["status"],
            "skill_status_before": row["status"],
            "definition_step_count": len(definition.get("steps", []) or []),
            "risk": definition.get("risk"),
        }
        manifest_sha256 = digest_json(manifest)
        (artifact_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
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
                "patch_mission_repair_skill_sandbox_started",
                {
                    "experiment_id": experiment_id,
                    "skill_id": skill_id,
                    "candidate_id": candidate_id,
                    "manifest_sha256": manifest_sha256,
                },
                connection,
            )
        self.skills.transition(
            skill_id,
            CandidateStatus.SANDBOXED,
            {"experiment_id": experiment_id, "manifest_sha256": manifest_sha256},
        )
        receipt = {
            "receipt_type": "PATCH_MISSION_REPAIR_SKILL_SANDBOX_START",
            "status": "SKILL_SANDBOXED",
            "skill_id": skill_id,
            "skill_status_before": row["status"],
            "skill_status_after": CandidateStatus.SANDBOXED.value,
            "candidate_id": candidate_id,
            "experiment_id": experiment_id,
            "manifest_path": str(artifact_dir / "manifest.json"),
            "manifest_sha256": manifest_sha256,
            "candidate_only": True,
            "active_skill_created": False,
            "approval_executed": False,
            "promotion_executed": False,
            "command_executed": False,
            "external_action_executed": False,
            "canonical_repo_write_executed": False,
            "claim_ceiling": (
                "skill sandbox started only; no validation result, approval, "
                "promotion, active use, command, repo write, push, or PR"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.skill_sandbox_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("skill_sandbox_receipts", updated, connection)
            self.ledger.append(
                "patch_mission_repair_skill_sandbox_receipt_recorded",
                receipt,
                connection,
            )
        return receipt

    def validate_patch_mission_repair_skill_sandbox(
        self, *, skill_id: str, reason: str = ""
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT skill_id,name,version,status,definition_json FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        if row is None:
            raise KeyError(f"unknown skill: {skill_id}")
        if row["name"] != "patch_mission_repair_pytest_literal_mismatch":
            raise ValueError("skill is not a Patch Mission repair proposal")
        if row["status"] != CandidateStatus.SANDBOXED.value:
            raise ValueError(
                f"Patch Mission repair skill validation requires SANDBOXED status, got {row['status']}"
            )
        experiment = self.db.query_one(
            """
            SELECT * FROM skill_experiments
            WHERE skill_id=? AND status='RUNNING'
            ORDER BY started_at DESC LIMIT 1
            """,
            (skill_id,),
        )
        if experiment is None:
            raise ValueError("Patch Mission repair skill has no running sandbox experiment")
        manifest = json.loads(str(experiment["manifest_json"]))
        if manifest.get("skill_id") != skill_id:
            raise ValueError("skill experiment manifest belongs to a different skill")
        artifact_dir = Path(str(experiment["artifact_path"])).resolve(strict=True)
        sandbox_root = self.config.sandbox_path.resolve(strict=False)
        if not self.policy._contained(artifact_dir, sandbox_root):
            raise PermissionError("skill experiment artifact path must stay inside sandbox")
        manifest_path = artifact_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("skill sandbox manifest artifact is missing")
        manifest_artifact = json.loads(manifest_path.read_text(encoding="utf-8"))
        if digest_json(manifest_artifact) != digest_json(manifest):
            raise ValueError("skill sandbox manifest artifact digest mismatch")
        candidate_id = str(manifest.get("source_candidate_id") or "")
        candidate_row = self.db.query_one(
            "SELECT status,result_json FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if candidate_row is None or candidate_row["status"] != CandidateStatus.VALIDATED.value:
            raise ValueError("source candidate must still be VALIDATED")
        candidate_validation = json.loads(str(candidate_row["result_json"] or "{}"))
        if (
            candidate_validation.get("receipt_digest")
            != manifest.get("source_validation_digest")
        ):
            raise ValueError("source candidate validation digest mismatch")
        cases = manifest.get("candidate_cases", [])
        if not isinstance(cases, list) or not cases:
            raise ValueError("skill sandbox manifest has no candidate cases")
        case_results: list[dict[str, Any]] = []
        for case in cases:
            if not isinstance(case, dict):
                continue
            passed = (
                case.get("candidate_id") == candidate_id
                and case.get("validation_digest")
                == candidate_validation.get("receipt_digest")
                and case.get("result_sha256")
                == candidate_validation.get("result_sha256")
                and candidate_validation.get("validated_result", {}).get("passed")
                is True
            )
            case_results.append(
                {
                    "case_id": case.get("case_id"),
                    "candidate_id": case.get("candidate_id"),
                    "passed": passed,
                    "validation_digest": case.get("validation_digest"),
                    "result_sha256": case.get("result_sha256"),
                }
            )
        passed_cases = sum(1 for item in case_results if item.get("passed") is True)
        candidate_cases = len(case_results)
        result = {
            "passed": candidate_cases > 0 and passed_cases == candidate_cases,
            "candidate_cases": candidate_cases,
            "passed_cases": passed_cases,
            "regressions": 0,
            "case_results": case_results,
            "source_candidate_id": candidate_id,
            "source_validation_digest": candidate_validation.get("receipt_digest"),
            "definition_sha256": manifest.get("definition_sha256"),
            "reason": reason,
            "active_skill_created": False,
            "approval_executed": False,
            "promotion_executed": False,
            "command_executed": False,
            "external_action_executed": False,
            "canonical_repo_write_executed": False,
            "created_at": utc_now(),
        }
        result_sha256 = digest_json(result)
        result_path = artifact_dir / "result.json"
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE skill_experiments SET status=?,result_json=?,finished_at=? WHERE experiment_id=?",
                (
                    "PASSED" if result["passed"] else "FAILED",
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    experiment["experiment_id"],
                ),
            )
            self.ledger.append(
                "patch_mission_repair_skill_sandbox_completed",
                {
                    "experiment_id": experiment["experiment_id"],
                    "skill_id": skill_id,
                    "result_sha256": result_sha256,
                    "passed": result["passed"],
                },
                connection,
            )
        if not result["passed"]:
            raise ValueError("Patch Mission repair skill sandbox did not pass")
        self.skills.transition(
            skill_id,
            CandidateStatus.VALIDATED,
            {
                "experiment_id": experiment["experiment_id"],
                "result_sha256": result_sha256,
            },
        )
        receipt = {
            "receipt_type": "PATCH_MISSION_REPAIR_SKILL_VALIDATION",
            "status": "SKILL_VALIDATED",
            "skill_id": skill_id,
            "skill_status_before": row["status"],
            "skill_status_after": CandidateStatus.VALIDATED.value,
            "candidate_id": candidate_id,
            "experiment_id": experiment["experiment_id"],
            "result_path": str(result_path),
            "result_sha256": result_sha256,
            "candidate_cases": candidate_cases,
            "passed_cases": passed_cases,
            "regressions": 0,
            "candidate_only": True,
            "active_skill_created": False,
            "approval_executed": False,
            "promotion_executed": False,
            "command_executed": False,
            "external_action_executed": False,
            "canonical_repo_write_executed": False,
            "claim_ceiling": (
                "skill validation only; no approval, promotion, active use, "
                "command, repo write, push, or PR"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.skill_sandbox_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("skill_sandbox_receipts", updated, connection)
            self.ledger.append(
                "patch_mission_repair_skill_validated",
                receipt,
                connection,
            )
        return receipt

    def approve_patch_mission_repair_skill(
        self,
        *,
        skill_id: str,
        reason: str = "",
        human_approved: bool = False,
    ) -> dict[str, Any]:
        if not human_approved:
            raise PermissionError("Patch Mission repair skill approval requires human approval")
        row = self.db.query_one(
            "SELECT skill_id,name,version,status,definition_json FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        if row is None:
            raise KeyError(f"unknown skill: {skill_id}")
        if row["name"] != "patch_mission_repair_pytest_literal_mismatch":
            raise ValueError("skill is not a Patch Mission repair proposal")
        if row["status"] != CandidateStatus.VALIDATED.value:
            raise ValueError(
                f"Patch Mission repair skill approval requires VALIDATED status, got {row['status']}"
            )
        definition = json.loads(str(row["definition_json"]))
        experiment = self.db.query_one(
            """
            SELECT * FROM skill_experiments
            WHERE skill_id=? AND status='PASSED' AND result_json IS NOT NULL
            ORDER BY finished_at DESC, started_at DESC LIMIT 1
            """,
            (skill_id,),
        )
        if experiment is None:
            raise ValueError("Patch Mission repair skill has no passed sandbox result")
        result = json.loads(str(experiment["result_json"]))
        result_sha256 = digest_json(result)
        if not result.get("passed"):
            raise ValueError("Patch Mission repair skill result did not pass")
        if int(result.get("regressions", 1)) != 0:
            raise ValueError("Patch Mission repair skill result has regressions")
        if int(result.get("passed_cases", 0)) < int(result.get("candidate_cases", 1)):
            raise ValueError("Patch Mission repair skill did not pass every candidate case")

        validation_transition: dict[str, Any] | None = None
        for transition in reversed(definition.get("transition_evidence", [])):
            if isinstance(transition, dict) and transition.get("target") == CandidateStatus.VALIDATED.value:
                validation_transition = transition
                break
        if validation_transition is None:
            raise ValueError("Patch Mission repair skill has no validation transition evidence")
        validation_evidence = validation_transition.get("evidence", {})
        if not isinstance(validation_evidence, dict):
            raise ValueError("Patch Mission repair skill validation evidence is malformed")
        if validation_evidence.get("experiment_id") != experiment["experiment_id"]:
            raise ValueError("Patch Mission repair skill validation experiment mismatch")
        if validation_evidence.get("result_sha256") != result_sha256:
            raise ValueError("Patch Mission repair skill validation result digest mismatch")

        approval_evidence = {
            "experiment_id": experiment["experiment_id"],
            "result_sha256": result_sha256,
            "validation_transition_sha256": digest_json(validation_transition),
            "reason": reason,
            "human_approved": True,
            "active_skill_matchable": True,
            "promotion_executed": False,
            "command_executed": False,
            "external_action_executed": False,
            "canonical_repo_write_executed": False,
        }
        self.skills.transition(
            skill_id,
            CandidateStatus.APPROVED,
            approval_evidence,
            human_approved=True,
        )
        receipt = {
            "receipt_type": "PATCH_MISSION_REPAIR_SKILL_APPROVAL",
            "status": "SKILL_APPROVED",
            "skill_id": skill_id,
            "skill_status_before": row["status"],
            "skill_status_after": CandidateStatus.APPROVED.value,
            "experiment_id": experiment["experiment_id"],
            "result_sha256": result_sha256,
            "validation_transition_sha256": approval_evidence[
                "validation_transition_sha256"
            ],
            "reason": reason,
            "human_approved": True,
            "active_skill_matchable": True,
            "active_skill_created": False,
            "approval_executed": True,
            "promotion_executed": False,
            "command_executed": False,
            "external_action_executed": False,
            "canonical_repo_write_executed": False,
            "claim_ceiling": (
                "owner approval only; skill may be matched as advisory context, "
                "but no promotion, command, repo write, push, or PR was executed"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.skill_sandbox_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("skill_sandbox_receipts", updated, connection)
            self.ledger.append(
                "patch_mission_repair_skill_approved",
                receipt,
                connection,
            )
        return receipt

    def _patch_mission_candidate_success_records(
        self, source_id_set: set[str]
    ) -> list[dict[str, Any]]:
        records = self.db.get_runtime("patch_mission_successful_repair_learning", [])
        if not isinstance(records, list):
            return []
        selected: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            evidence_id = str(record.get("success_evidence_id") or "")
            memory_id = str(record.get("memory_id") or "")
            if evidence_id in source_id_set or memory_id in source_id_set:
                selected.append(record)
        return selected

    def _patch_mission_repair_candidate_latest_replay(
        self, candidate_id: str
    ) -> dict[str, Any] | None:
        receipts = self.db.get_runtime(
            "patch_mission_repair_skill_candidate_reviews", []
        )
        if not isinstance(receipts, list):
            return None
        for item in receipts:
            if (
                isinstance(item, dict)
                and item.get("candidate_id") == candidate_id
                and item.get("status") == "REPLAY_PASSED"
            ):
                return item
        return None

    def _patch_mission_literal_pair_from_sample(
        self, sample: dict[str, Any]
    ) -> tuple[str, str] | None:
        memory_id = str(sample.get("memory_id") or "")
        if not memory_id:
            return None
        row = self.db.query_one(
            "SELECT content_json FROM memories WHERE memory_id=?",
            (memory_id,),
        )
        if row is None:
            return None
        try:
            content = json.loads(str(row["content_json"]))
        except json.JSONDecodeError:
            return None
        cause = str(content.get("cause") or "")
        match = re.search(
            r"observed value (?P<actual>['\"])(?P<actual_value>.*?)\1 did not match expected (?P<expected>['\"])(?P<expected_value>.*?)\3",
            cause,
        )
        if not match:
            return None
        actual = match.group("actual_value")
        expected = match.group("expected_value")
        if not actual or not expected or actual == expected:
            return None
        return actual, expected

    def _run_patch_mission_sandbox_pytest(
        self, *, repo_root: Path, pytest_target: str
    ) -> dict[str, Any]:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", pytest_target],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        return {
            "command": [sys.executable, "-m", "pytest", pytest_target],
            "cwd": str(repo_root),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
        }

    def _patch_mission_replay_success_record(
        self, record: dict[str, Any], source_id_set: set[str]
    ) -> dict[str, Any]:
        checks: dict[str, bool] = {}
        errors: list[str] = []
        evidence_id = str(record.get("success_evidence_id") or "")
        evidence_row = self.db.query_one(
            "SELECT event_type,payload_json FROM evidence WHERE evidence_id=?",
            (evidence_id,),
        )
        checks["success_evidence_bound"] = bool(
            evidence_id and evidence_id in source_id_set and evidence_row is not None
        )
        if evidence_row is None:
            errors.append("success evidence is missing")
        else:
            try:
                payload = json.loads(evidence_row["payload_json"])
                checks["success_evidence_signature_matches"] = (
                    payload.get("signature") == record.get("signature")
                    and evidence_row["event_type"] == "patch_mission_repair_success_recorded"
                )
            except json.JSONDecodeError:
                checks["success_evidence_signature_matches"] = False
        memory_id = str(record.get("memory_id") or "")
        memory_row = self.db.query_one(
            "SELECT memory_type,content_json FROM memories WHERE memory_id=?",
            (memory_id,),
        )
        checks["memory_bound"] = bool(
            memory_id and memory_id in source_id_set and memory_row is not None
        )
        if memory_row is None:
            errors.append("failure-learning memory is missing")
            memory_content: dict[str, Any] = {}
        else:
            try:
                memory_content = json.loads(memory_row["content_json"])
            except json.JSONDecodeError:
                memory_content = {}
                errors.append("failure-learning memory is malformed")
        checks["memory_matches_repair"] = (
            memory_row is not None
            and memory_row["memory_type"] == "procedural"
            and memory_content.get("kind") == "patch_mission_failure_learning"
            and memory_content.get("synthesis_status") == "synthesized_literal_diff"
            and memory_content.get("patch_target") == record.get("patch_target")
        )
        failing_output = self._patch_mission_replay_action_output(
            str(record.get("failing_test_action_id") or ""),
            expected_tool="run_command",
            errors=errors,
        )
        passing_output = self._patch_mission_replay_action_output(
            str(record.get("passing_test_action_id") or ""),
            expected_tool="run_command",
            errors=errors,
        )
        checks["failing_test_failed"] = int(failing_output.get("returncode", 0)) != 0
        checks["passing_test_passed"] = int(passing_output.get("returncode", -1)) == 0
        checks["pytest_target_stable"] = (
            record.get("pytest_target")
            == self._patch_mission_pytest_target_from_command(
                failing_output.get("command")
            )
        )
        draft_action_id = str(record.get("draft_action_id") or "")
        apply_action_id = str(record.get("apply_action_id") or "")
        draft_row = self._patch_mission_replay_action_row(
            draft_action_id, expected_tool="write_file", errors=errors
        )
        apply_row = self._patch_mission_replay_action_row(
            apply_action_id, expected_tool="write_file", errors=errors
        )
        if draft_row:
            try:
                draft_text = self._patch_mission_write_file_content(draft_action_id)
                diff_text = self._extract_unified_diff_from_patch_mission_draft(
                    draft_text
                )
                checks["draft_diff_targets_patch_file"] = (
                    record.get("patch_target")
                    in self._patch_mission_diff_changed_files(diff_text)
                )
            except Exception as exc:
                checks["draft_diff_targets_patch_file"] = False
                errors.append(f"draft diff replay failed: {exc}")
        else:
            checks["draft_diff_targets_patch_file"] = False
        if apply_row:
            try:
                apply_args = json.loads(str(apply_row["arguments_json"]))
                checks["apply_wrote_patch_target"] = str(
                    apply_args.get("path") or ""
                ).replace("\\", "/").endswith(str(record.get("patch_target") or ""))
            except json.JSONDecodeError:
                checks["apply_wrote_patch_target"] = False
        else:
            checks["apply_wrote_patch_target"] = False
        passed = bool(checks) and all(checks.values())
        return {
            "status": "SAMPLE_PASSED" if passed else "SAMPLE_FAILED",
            "mission_id": record.get("mission_id"),
            "memory_id": memory_id,
            "success_evidence_id": evidence_id,
            "failing_test_action_id": record.get("failing_test_action_id"),
            "passing_test_action_id": record.get("passing_test_action_id"),
            "apply_action_id": apply_action_id,
            "draft_action_id": draft_action_id,
            "pytest_target": record.get("pytest_target"),
            "patch_target": record.get("patch_target"),
            "checks": checks,
            "errors": errors[:10],
        }

    def _patch_mission_replay_action_output(
        self, action_id: str, *, expected_tool: str, errors: list[str]
    ) -> dict[str, Any]:
        row = self._patch_mission_replay_action_row(
            action_id, expected_tool=expected_tool, errors=errors
        )
        if row is None:
            return {}
        try:
            return self._patch_mission_action_result_output(action_id)
        except Exception as exc:
            errors.append(f"action output unavailable for {action_id}: {exc}")
            return {}

    def _patch_mission_replay_action_row(
        self, action_id: str, *, expected_tool: str, errors: list[str]
    ) -> Any | None:
        if not action_id:
            errors.append(f"missing {expected_tool} action id")
            return None
        row = self.db.query_one(
            "SELECT tool,status,arguments_json,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            errors.append(f"action is missing: {action_id}")
            return None
        if row["tool"] != expected_tool:
            errors.append(f"action {action_id} has tool {row['tool']}, expected {expected_tool}")
            return None
        if row["status"] != ActionStatus.SUCCEEDED.value:
            errors.append(f"action {action_id} status is {row['status']}")
            return None
        return row

    def _patch_mission_repair_skill_candidate_context(
        self, limit: int = 2
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime(
            "patch_mission_repair_skill_candidate_reviews", []
        )
        if not isinstance(receipts, list):
            return []
        selected = [
            item
            for item in receipts
            if isinstance(item, dict)
            and item.get("status") == "REPLAY_PASSED"
            and item.get("candidate_type") == "patch_mission_repair_skill"
        ]
        return selected[:limit]

    @staticmethod
    def _patch_mission_repair_skill_candidate_lines(
        receipts: list[dict[str, Any]]
    ) -> list[str]:
        if not receipts:
            return ["- (none reviewed)"]
        lines: list[str] = []
        for receipt in receipts:
            lines.append(
                "- "
                f"`{receipt.get('candidate_id')}` replay `{receipt.get('status')}`; "
                f"samples `{receipt.get('passed_sample_count')}/{receipt.get('sample_count')}`; "
                "advisory only, no approval or promotion."
            )
        return lines

    def _patch_mission_approved_repair_skill_context(
        self,
        *,
        mission_record: dict[str, Any],
        query_text: str,
        selected_file: str | None,
        pytest_target: str | None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        query_parts = [
            "patch mission pytest literal mismatch ci github repair",
            str(mission_record.get("mission") or ""),
            selected_file or "",
            pytest_target or "",
            query_text[:1000],
        ]
        matches = self.skills.match(
            " ".join(part for part in query_parts if part),
            limit=limit * 2,
        )
        selected: list[dict[str, Any]] = []
        usable_statuses = {
            CandidateStatus.APPROVED.value,
            CandidateStatus.PROMOTED.value,
        }
        for skill in matches:
            if skill.get("name") != "patch_mission_repair_pytest_literal_mismatch":
                continue
            status = str(skill.get("status") or "")
            if status not in usable_statuses:
                continue
            outcome_confidence = (
                self._patch_mission_promoted_repair_skill_outcome_confidence(
                    str(skill.get("skill_id") or "")
                )
                if status == CandidateStatus.PROMOTED.value
                else None
            )
            selected.append(
                {
                    **skill,
                    "skill_lifecycle_status": status,
                    "promoted_skill_matched": status == CandidateStatus.PROMOTED.value,
                    "advisory_confidence": "promoted"
                    if status == CandidateStatus.PROMOTED.value
                    else "approved",
                    "promoted_outcome_confidence": outcome_confidence,
                }
            )
        selected.sort(
            key=lambda item: (
                item.get("status") == CandidateStatus.PROMOTED.value,
                float(item.get("match_score", 0.0)),
            ),
            reverse=True,
        )
        return selected[:limit]

    @staticmethod
    def _patch_mission_approved_repair_skill_lines(
        skills: list[dict[str, Any]]
    ) -> list[str]:
        if not skills:
            return ["- (none approved)"]
        lines: list[str] = []
        for skill in skills:
            score = float(skill.get("match_score", 0.0))
            status = str(skill.get("status") or "")
            source_ids = skill.get("source_episode_ids", [])
            if not isinstance(source_ids, list):
                source_ids = []
            confidence = (
                "promoted advisory confidence"
                if status == CandidateStatus.PROMOTED.value
                else "approved advisory confidence"
            )
            outcome_confidence = skill.get("promoted_outcome_confidence")
            outcome_text = ""
            if isinstance(outcome_confidence, dict):
                outcome_text = (
                    f" promoted outcome confidence `{outcome_confidence.get('level')}`; "
                    f"successes `{outcome_confidence.get('success_count')}`; "
                    f"failed_or_rejected `{outcome_confidence.get('failed_or_rejected_count')}`;"
                )
            lines.append(
                "- "
                f"`{skill.get('skill_id')}` status `{status}`; "
                f"match `{score:.2f}`; "
                f"sources `{len(source_ids)}`; "
                f"{confidence}; "
                f"{outcome_text} "
                "advisory only, no skill execution, promotion, repo write, push, or PR."
            )
        return lines

    @staticmethod
    def _patch_mission_approved_repair_skill_rationale(
        skills: list[dict[str, Any]],
        *,
        default_reason: str,
        action_phrase: str,
    ) -> str:
        if not skills:
            return default_reason
        skill = skills[0]
        score = float(skill.get("match_score", 0.0))
        status = str(skill.get("status") or "")
        status_word = (
            "promoted" if status == CandidateStatus.PROMOTED.value else "approved"
        )
        confidence_note = (
            "higher-confidence promoted rationale"
            if status == CandidateStatus.PROMOTED.value
            else "approved advisory rationale"
        )
        outcome_confidence = skill.get("promoted_outcome_confidence")
        outcome_note = ""
        if isinstance(outcome_confidence, dict):
            outcome_note = (
                f" Promoted outcome history: level `{outcome_confidence.get('level')}`, "
                f"successes `{outcome_confidence.get('success_count')}`, "
                f"failed_or_rejected `{outcome_confidence.get('failed_or_rejected_count')}`, "
                f"recovery_successes `{outcome_confidence.get('recovery_count', 0)}`; "
                f"{outcome_confidence.get('reason')}."
            )
        return (
            f"Matched {status_word} repair skill `{skill.get('skill_id')}` "
            f"({skill.get('name')}, score {score:.2f}); it supports {action_phrase} "
            f"for this repeated Patch Mission repair pattern as {confidence_note}. "
            f"Selected step reason: {default_reason}. "
            f"{outcome_note} "
            "Advisory influence only: "
            "no skill execution, promotion, repo write, push, or PR is performed."
        )

    def _patch_mission_promoted_repair_skill_outcome_confidence(
        self, skill_id: str
    ) -> dict[str, Any]:
        outcomes = self.db.get_runtime("patch_mission_promoted_skill_outcomes", [])
        if not isinstance(outcomes, list):
            outcomes = []
        successes = [
            item
            for item in outcomes
            if isinstance(item, dict)
            and item.get("skill_id") == skill_id
            and item.get("success") is True
            and item.get("approval_valid") is True
        ]
        failed_or_rejected = self._patch_mission_promoted_skill_failed_action_count(
            skill_id
        )
        recoveries = self._patch_mission_promoted_skill_recovery_outcomes(skill_id)
        recovery_count = len(recoveries)
        unrecovered_failed_or_rejected = max(0, failed_or_rejected - recovery_count)
        if successes and failed_or_rejected == 0:
            level = "outcome_supported"
            delta = 0.15
            reason = "owner-approved promoted-skill actions have succeeded with no later failed or rejected candidates"
        elif successes and failed_or_rejected > 0:
            if recovery_count >= failed_or_rejected:
                level = "recovered_outcome_supported"
                delta = 0.12
                reason = (
                    "owner-approved fallback-restored repair successes offset the "
                    "recorded failed or rejected promoted candidates while preserving "
                    "their evidence"
                )
            elif recovery_count > 0:
                level = "recovering_mixed_outcome"
                delta = 0.08
                reason = (
                    "successful promoted-skill outcomes and fallback-restored repair "
                    "successes exist, but some failed or rejected candidates remain "
                    "unrecovered"
                )
            else:
                level = "mixed_outcome"
                delta = 0.05
                reason = "successful promoted-skill outcomes exist, but later failed or rejected candidates reduce confidence"
        elif failed_or_rejected > 0:
            level = "confidence_withheld"
            delta = -0.1
            reason = "failed or rejected promoted-skill candidates exist without successful promoted outcomes"
        else:
            level = "no_promoted_outcome_evidence"
            delta = 0.0
            reason = "no owner-approved successful promoted-skill action outcome has been recorded yet"
        latest = successes[0] if successes else None
        return {
            "level": level,
            "confidence_delta": delta,
            "success_count": len(successes),
            "failed_or_rejected_count": failed_or_rejected,
            "recovery_count": recovery_count,
            "unrecovered_failed_or_rejected_count": unrecovered_failed_or_rejected,
            "latest_success_action_id": latest.get("action_id") if latest else None,
            "latest_success_receipt_digest": latest.get("receipt_digest")
            if latest
            else None,
            "reason": reason,
            "authority": {
                "planning_metadata_only": True,
                "approval_bypass_allowed": False,
                "executes_actions": False,
            },
        }

    def _patch_mission_promoted_skill_failed_action_count(self, skill_id: str) -> int:
        rows = self.db.query_all(
            """
            SELECT arguments_json,status FROM actions
            WHERE status IN ('FAILED','REJECTED') AND arguments_json LIKE ?
            """,
            (f"%{skill_id}%",),
        )
        count = 0
        for row in rows:
            try:
                arguments = json.loads(str(row["arguments_json"]))
            except json.JSONDecodeError:
                continue
            metadata = arguments.get("patch_mission_approved_skill_candidate")
            if not isinstance(metadata, dict):
                continue
            if metadata.get("skill_id") != skill_id:
                continue
            if metadata.get("skill_status") != CandidateStatus.PROMOTED.value:
                continue
            count += 1
        return count

    def _patch_mission_promoted_skill_recovery_outcomes(
        self, skill_id: str
    ) -> list[dict[str, Any]]:
        records = self.db.get_runtime(
            "patch_mission_promoted_skill_recovery_outcomes", []
        )
        if not isinstance(records, list):
            return []
        unique: dict[str, dict[str, Any]] = {}
        for item in records:
            if not isinstance(item, dict):
                continue
            if item.get("skill_id") != skill_id:
                continue
            if item.get("success") is not True or item.get("approval_valid") is not True:
                continue
            signature = str(item.get("signature") or item.get("receipt_digest") or "")
            if not signature:
                continue
            unique.setdefault(signature, item)
        return list(unique.values())

    @staticmethod
    def _patch_mission_confidence_adjusted_ci_next_step(
        *,
        next_mode: str,
        next_target: str | None,
        next_reason: str,
        candidate_files: list[str],
        approved_repair_skills: list[dict[str, Any]],
    ) -> tuple[str, str | None, str]:
        if next_mode != "test":
            return next_mode, next_target, next_reason
        if not approved_repair_skills:
            return next_mode, next_target, next_reason
        confidence = approved_repair_skills[0].get("promoted_outcome_confidence")
        if not isinstance(confidence, dict):
            return next_mode, next_target, next_reason
        level = str(confidence.get("level") or "")
        if level not in {
            "mixed_outcome",
            "recovering_mixed_outcome",
            "confidence_withheld",
        }:
            return next_mode, next_target, next_reason
        inspect_target = None
        if next_target:
            inspect_target = str(next_target).split("::", 1)[0]
        if not inspect_target and candidate_files:
            inspect_target = candidate_files[0]
        if not inspect_target:
            return next_mode, next_target, next_reason
        return (
            "inspect-file",
            inspect_target,
            (
                f"Promoted repair skill confidence is `{level}` "
                f"({confidence.get('reason')}); inspect `{inspect_target}` "
                "before running or drafting another repair action."
            ),
        )

    @staticmethod
    def _patch_mission_promoted_confidence_action_note(
        *,
        original_mode: str,
        selected_mode: str,
        approved_repair_skills: list[dict[str, Any]],
    ) -> str:
        if not approved_repair_skills:
            return "(none)"
        confidence = approved_repair_skills[0].get("promoted_outcome_confidence")
        if not isinstance(confidence, dict):
            return "(none)"
        level = str(confidence.get("level") or "")
        if level in {"outcome_supported", "recovered_outcome_supported"} and original_mode == selected_mode:
            return (
                f"direct path preserved by `{level}` promoted history; "
                "policy and owner approval still control command execution"
            )
        if level in {"mixed_outcome", "recovering_mixed_outcome", "confidence_withheld"} and original_mode != selected_mode:
            return (
                f"safer local verification selected because promoted confidence is `{level}`"
            )
        return (
            f"no action selection change for promoted confidence `{level or 'unknown'}`"
        )

    def _patch_mission_approved_repair_skill_id_from_text(
        self, text: str
    ) -> str | None:
        matches = [
            *re.finditer(
                r"Matched (?:approved|promoted) repair skill `(?P<skill_id>skill_[^`]+)`",
                text,
            ),
            *re.finditer(
                r"`(?P<skill_id>skill_[^`]+)` status `(?:APPROVED|PROMOTED)`",
                text,
            ),
        ]
        if not matches:
            return None
        for match in matches:
            skill_id = match.group("skill_id")
            row = self.db.query_one(
                "SELECT name,status FROM skills WHERE skill_id=?",
                (skill_id,),
            )
            if row is None:
                continue
            if row["name"] != "patch_mission_repair_pytest_literal_mismatch":
                continue
            if row["status"] not in {
                CandidateStatus.APPROVED.value,
                CandidateStatus.PROMOTED.value,
            }:
                continue
            return skill_id
        return None

    def _patch_mission_annotate_approved_skill_action_candidate(
        self,
        *,
        action: ActionSpec,
        skill_id: str | None,
        source_action_id: str,
        source_mode: str,
    ) -> ActionSpec:
        if not skill_id:
            return action
        row = self.db.query_one(
            "SELECT status FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        skill_status = str(row["status"]) if row is not None else ""
        promoted_match = skill_status == CandidateStatus.PROMOTED.value
        confidence = (
            "promoted_repair_skill"
            if promoted_match
            else "approved_repair_skill"
        )
        approval_required_by_policy = action.risk != RiskLevel.READ
        metadata: dict[str, Any] = {
            "candidate_type": "promoted_repair_skill_action_candidate"
            if promoted_match
            else "approved_repair_skill_action_candidate",
            "skill_id": skill_id,
            "skill_name": "patch_mission_repair_pytest_literal_mismatch",
            "skill_status": skill_status or CandidateStatus.APPROVED.value,
            "promoted_skill_matched": promoted_match,
            "advisory_confidence": confidence,
            "source_action_id": source_action_id,
            "source_mode": source_mode,
            "owner_gated": approval_required_by_policy,
            "approval_still_required": approval_required_by_policy,
            "executes_now": not approval_required_by_policy,
            "skill_execution_recorded": False,
            "promotion_executed": False,
            "repo_write_executed": False,
            "push_or_pr_executed": False,
            "influence_scope": (
                "prefill existing Patch Mission follow-up action rationale; "
                "existing policy and owner approval still decide execution"
            ),
        }
        if promoted_match:
            metadata["promoted_outcome_confidence"] = (
                self._patch_mission_promoted_repair_skill_outcome_confidence(skill_id)
            )
        status_label = "promoted" if promoted_match else "approved"
        action.arguments = {
            **action.arguments,
            "patch_mission_approved_skill_candidate": metadata,
        }
        action.purpose = (
            action.purpose
            + f" guided by {status_label} repair skill {skill_id} as owner-gated advisory context"
        )
        action.expected_result = (
            action.expected_result
            + f"; {status_label} skill influence remains candidate-only until the owner approves the action"
        )
        action.idempotency_key = digest_json(
            {
                "tool": action.tool,
                "arguments": action.arguments,
                "purpose": action.purpose,
            }
        )
        return action

    @staticmethod
    def _patch_mission_with_skill_action_candidate_metadata(
        candidate: dict[str, Any], action: ActionSpec
    ) -> dict[str, Any]:
        metadata = action.arguments.get("patch_mission_approved_skill_candidate")
        if not isinstance(metadata, dict):
            return candidate
        promoted_match = metadata.get("skill_status") == CandidateStatus.PROMOTED.value
        approval_required = bool(
            candidate.get("requires_owner_approval")
            or candidate.get("approval_required")
        )
        executes_now = bool(candidate.get("executes_now")) and not approval_required
        authority = dict(candidate.get("authority") or {})
        authority.update(
            {
                "approved_skill_candidate_only": True,
                "promoted_skill_candidate_only": promoted_match,
                "skill_execution_recorded": False,
                "promotion_executed": False,
                "repo_write_executed": False,
                "push_or_pr_executed": False,
                "policy_and_approval_still_required": True,
                "policy_requires_approval": approval_required,
            }
        )
        return {
            **candidate,
            "source": "patch_mission_promoted_skill_advisory"
            if promoted_match
            else "patch_mission_approved_skill_advisory",
            "patch_mission_skill_candidate": metadata,
            "requires_owner_approval": approval_required,
            "approval_required": approval_required,
            "executes_now": executes_now,
            "authority": authority,
        }

    def _record_repair_skill_action_outcome(
        self,
        *,
        action: ActionSpec,
        approval_id: str | None,
        approval_valid: bool,
        action_completed_evidence_id: str,
        result: Any,
        final_success: bool,
    ) -> dict[str, Any] | None:
        metadata = action.arguments.get("patch_mission_approved_skill_candidate")
        if not isinstance(metadata, dict):
            return None
        if not approval_id or not approval_valid or not final_success:
            return None
        skill_id = str(metadata.get("skill_id") or "")
        if not skill_id:
            return None
        row = self.db.query_one(
            "SELECT name,status,use_count FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        if row is None:
            return None
        if row["name"] != "patch_mission_repair_pytest_literal_mismatch":
            return None
        skill_status = str(row["status"])
        if skill_status not in {
            CandidateStatus.APPROVED.value,
            CandidateStatus.PROMOTED.value,
        }:
            return None
        promoted_outcome = skill_status == CandidateStatus.PROMOTED.value
        status_label = "PROMOTED" if promoted_outcome else "APPROVED"
        runtime_key = (
            "patch_mission_promoted_skill_outcomes"
            if promoted_outcome
            else "patch_mission_approved_skill_outcomes"
        )
        event_type = (
            "patch_mission_promoted_repair_skill_action_outcome_recorded"
            if promoted_outcome
            else "patch_mission_approved_repair_skill_action_outcome_recorded"
        )
        output = result.output if hasattr(result, "output") else {}
        receipt = {
            "receipt_type": f"PATCH_MISSION_{status_label}_REPAIR_SKILL_ACTION_OUTCOME",
            "status": "RECORDED",
            "skill_id": skill_id,
            "skill_status": skill_status,
            "skill_use_count_before": int(row["use_count"]),
            "action_id": action.action_id,
            "tool": action.tool,
            "approval_id": approval_id,
            "approval_valid": True,
            "action_completed_evidence_id": action_completed_evidence_id,
            "candidate_type": metadata.get("candidate_type"),
            "source_action_id": metadata.get("source_action_id"),
            "source_mode": metadata.get("source_mode"),
            "success": True,
            "returncode": output.get("returncode") if isinstance(output, dict) else None,
            "promoted_skill_outcome": promoted_outcome,
            "promotion_executed": False,
            "repo_write_executed": action.tool == "write_file",
            "push_or_pr_executed": action.tool
            in {"git_push", "create_github_pull_request"},
            "claim_ceiling": (
                "owner-approved action outcome for repair skill use only; no promotion, "
                "no autonomous execution, no approval bypass, and later writes/GitHub "
                "actions still require approval"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self.skills.record_use(skill_id, True)
        updated_row = self.db.query_one(
            "SELECT use_count,success_rate FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        receipt["skill_use_count_after"] = (
            int(updated_row["use_count"]) if updated_row is not None else None
        )
        receipt["skill_success_rate_after"] = (
            float(updated_row["success_rate"]) if updated_row is not None else None
        )
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.db.get_runtime(runtime_key, [])
        if not isinstance(current, list):
            current = []
        with self.db.transaction() as connection:
            self.db.set_runtime(
                runtime_key,
                [receipt, *current][:100],
                connection,
            )
            self.ledger.append(event_type, receipt, connection)
        if not promoted_outcome:
            promotion_review = self._record_patch_mission_repair_skill_promotion_review_candidate(
                skill_id=skill_id,
                outcomes=[receipt, *current],
            )
            if promotion_review is not None:
                receipt["promotion_review_candidate"] = promotion_review
        return receipt

    def _record_patch_mission_repair_skill_promotion_review_candidate(
        self, *, skill_id: str, outcomes: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        relevant = [
            item
            for item in outcomes
            if isinstance(item, dict)
            and item.get("skill_id") == skill_id
            and item.get("success") is True
            and item.get("approval_valid") is True
            and item.get("promotion_executed") is False
        ]
        unique_by_action: dict[str, dict[str, Any]] = {}
        for item in relevant:
            action_id = str(item.get("action_id") or "")
            if action_id and action_id not in unique_by_action:
                unique_by_action[action_id] = item
        successes = list(unique_by_action.values())
        if len(successes) < 2:
            return {
                "available": False,
                "reason": "fewer than two approved successful skill-derived actions",
                "approved_success_count": len(successes),
                "promotion_executed": False,
            }
        existing = self.db.query_one(
            """
            SELECT candidate_id,status FROM evolution_candidates
            WHERE candidate_type=? AND status NOT IN ('REJECTED','ROLLED_BACK')
            ORDER BY created_at DESC
            """,
            ("patch_mission_repair_skill_promotion_review",),
        )
        if existing is not None:
            row = self.db.query_one(
                "SELECT proposal_json FROM evolution_candidates WHERE candidate_id=?",
                (existing["candidate_id"],),
            )
            if row is not None:
                try:
                    existing_proposal = json.loads(str(row["proposal_json"]))
                except json.JSONDecodeError:
                    existing_proposal = {}
                proposal = existing_proposal.get("proposal", {})
                if isinstance(proposal, dict) and proposal.get("skill_id") == skill_id:
                    return {
                        "available": True,
                        "status": "EXISTING_PROMOTION_REVIEW_CANDIDATE",
                        "candidate_id": existing["candidate_id"],
                        "candidate_status": existing["status"],
                        "approved_success_count": len(successes),
                        "promotion_executed": False,
                    }
        skill_row = self.db.query_one(
            "SELECT name,status,use_count,success_rate,definition_json FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        if skill_row is None or skill_row["status"] != CandidateStatus.APPROVED.value:
            return None
        source_ids: list[str] = [skill_id]
        for item in successes[:10]:
            for key in ("receipt_digest", "action_id", "approval_id", "action_completed_evidence_id"):
                value = item.get(key)
                if value:
                    source_ids.append(str(value))
        source_ids = list(dict.fromkeys(source_ids))
        proposal = {
            "schema_version": 1,
            "candidate_only": True,
            "candidate_type": "patch_mission_repair_skill_promotion_review",
            "skill_id": skill_id,
            "skill_name": skill_row["name"],
            "skill_status": skill_row["status"],
            "approved_success_count": len(successes),
            "minimum_approved_success_count": 2,
            "skill_use_count": int(skill_row["use_count"]),
            "skill_success_rate": float(skill_row["success_rate"]),
            "recommended_review": (
                "Owner may review whether this approved Patch Mission repair skill is ready "
                "for a separate human-approved promotion step."
            ),
            "promotion_executed": False,
            "human_approval_required_for_promotion": True,
            "approval_bypass_allowed": False,
            "command_executed": False,
            "repo_write_executed": False,
            "push_or_pr_executed": False,
            "claim_ceiling": (
                "promotion review candidate only; not a PROMOTED skill and not execution authority"
            ),
            "outcome_receipts": [
                {
                    "action_id": item.get("action_id"),
                    "approval_id": item.get("approval_id"),
                    "receipt_digest": item.get("receipt_digest"),
                    "action_completed_evidence_id": item.get(
                        "action_completed_evidence_id"
                    ),
                }
                for item in successes[:10]
            ],
        }
        candidate_id = new_id("candidate")
        now = utc_now()
        payload = {"type": proposal["candidate_type"], "proposal": proposal}
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO evolution_candidates(
                    candidate_id,candidate_type,title,proposal_json,source_ids_json,
                    baseline_json,experiment_json,result_json,status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    proposal["candidate_type"],
                    "Review approved Patch Mission repair skill for possible promotion",
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(source_ids, ensure_ascii=False),
                    json.dumps(
                        {
                            "skill_id": skill_id,
                            "status_before": skill_row["status"],
                            "promotion_executed": False,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    None,
                    None,
                    CandidateStatus.PROPOSED.value,
                    now,
                    now,
                ),
            )
            self.ledger.append(
                "patch_mission_repair_skill_promotion_review_candidate_created",
                {
                    "candidate_id": candidate_id,
                    "skill_id": skill_id,
                    "approved_success_count": len(successes),
                    "promotion_executed": False,
                    "source_ids": source_ids,
                },
                connection,
            )
        return {
            "available": True,
            "status": "PROMOTION_REVIEW_CANDIDATE_CREATED",
            "candidate_id": candidate_id,
            "candidate_status": CandidateStatus.PROPOSED.value,
            "approved_success_count": len(successes),
            "promotion_executed": False,
        }

    def approve_patch_mission_repair_skill_promotion_review(
        self,
        *,
        candidate_id: str,
        reason: str = "",
        human_approved: bool = False,
    ) -> dict[str, Any]:
        if not human_approved:
            raise PermissionError(
                "Patch Mission repair skill promotion review requires human approval"
            )
        row = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if row is None:
            raise KeyError(f"unknown promotion review candidate: {candidate_id}")
        if row["candidate_type"] != "patch_mission_repair_skill_promotion_review":
            raise ValueError("candidate is not a Patch Mission repair skill promotion review")
        if row["status"] != CandidateStatus.PROPOSED.value:
            raise ValueError(
                "Patch Mission repair skill promotion review approval requires "
                f"PROPOSED status, got {row['status']}"
            )
        payload = json.loads(str(row["proposal_json"]))
        proposal = payload.get("proposal", {})
        if not isinstance(proposal, dict):
            raise ValueError("promotion review proposal is malformed")
        skill_id = str(proposal.get("skill_id") or "")
        skill_row = self.db.query_one(
            "SELECT status,use_count,success_rate FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        if skill_row is None:
            raise ValueError("promotion review source skill is missing")
        if skill_row["status"] != CandidateStatus.APPROVED.value:
            raise ValueError("promotion review source skill must still be APPROVED")
        outcomes = self.db.get_runtime("patch_mission_approved_skill_outcomes", [])
        if not isinstance(outcomes, list):
            outcomes = []
        unique_success_actions = {
            str(item.get("action_id"))
            for item in outcomes
            if isinstance(item, dict)
            and item.get("skill_id") == skill_id
            and item.get("success") is True
            and item.get("approval_valid") is True
            and item.get("action_id")
        }
        minimum = int(proposal.get("minimum_approved_success_count", 2) or 2)
        if len(unique_success_actions) < minimum:
            raise ValueError("promotion review no longer has enough approved outcomes")
        receipt = {
            "receipt_type": "PATCH_MISSION_REPAIR_SKILL_PROMOTION_REVIEW_APPROVAL",
            "status": "PROMOTION_REVIEW_APPROVED",
            "candidate_id": candidate_id,
            "candidate_status_before": row["status"],
            "candidate_status_after": CandidateStatus.APPROVED.value,
            "skill_id": skill_id,
            "skill_status_before": skill_row["status"],
            "skill_status_after": skill_row["status"],
            "approved_success_count": len(unique_success_actions),
            "minimum_approved_success_count": minimum,
            "human_approved": True,
            "reason": reason,
            "promotion_request_authorized": True,
            "promotion_executed": False,
            "command_executed": False,
            "repo_write_executed": False,
            "push_or_pr_executed": False,
            "approval_bypass_allowed": False,
            "claim_ceiling": (
                "promotion review decision only; skill remains APPROVED and any "
                "PROMOTED transition requires a separate explicit human-approved step"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE evolution_candidates SET status=?,result_json=?,updated_at=? WHERE candidate_id=?",
                (
                    CandidateStatus.APPROVED.value,
                    json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    candidate_id,
                ),
            )
            self.ledger.append(
                "patch_mission_repair_skill_promotion_review_approved",
                receipt,
                connection,
            )
        return receipt

    def promote_patch_mission_repair_skill_from_review(
        self,
        *,
        candidate_id: str,
        reason: str = "",
        human_approved: bool = False,
    ) -> dict[str, Any]:
        if not human_approved:
            raise PermissionError("Patch Mission repair skill promotion requires human approval")
        row = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if row is None:
            raise KeyError(f"unknown promotion review candidate: {candidate_id}")
        if row["candidate_type"] != "patch_mission_repair_skill_promotion_review":
            raise ValueError("candidate is not a Patch Mission repair skill promotion review")
        if row["status"] != CandidateStatus.APPROVED.value:
            raise ValueError(
                "Patch Mission repair skill promotion requires an APPROVED "
                f"promotion-review candidate, got {row['status']}"
            )
        review_receipt = json.loads(str(row["result_json"] or "{}"))
        if review_receipt.get("status") != "PROMOTION_REVIEW_APPROVED":
            raise ValueError("promotion review candidate lacks approved review evidence")
        payload = json.loads(str(row["proposal_json"]))
        proposal = payload.get("proposal", {})
        if not isinstance(proposal, dict):
            raise ValueError("promotion review proposal is malformed")
        skill_id = str(proposal.get("skill_id") or "")
        skill_row = self.db.query_one(
            "SELECT status,use_count,success_rate FROM skills WHERE skill_id=?",
            (skill_id,),
        )
        if skill_row is None:
            raise ValueError("promotion review source skill is missing")
        if skill_row["status"] != CandidateStatus.APPROVED.value:
            raise ValueError("promotion source skill must still be APPROVED")
        minimum = int(proposal.get("minimum_approved_success_count", 2) or 2)
        approved_count = int(proposal.get("approved_success_count", 0) or 0)
        if approved_count < minimum:
            raise ValueError("promotion review candidate has insufficient approved outcomes")
        evidence = {
            "candidate_id": candidate_id,
            "review_receipt_digest": review_receipt.get("receipt_digest"),
            "approved_success_count": approved_count,
            "minimum_approved_success_count": minimum,
            "skill_use_count": int(skill_row["use_count"]),
            "skill_success_rate": float(skill_row["success_rate"]),
            "reason": reason,
            "human_approved": True,
            "command_executed": False,
            "repo_write_executed": False,
            "push_or_pr_executed": False,
            "approval_bypass_allowed": False,
        }
        self.skills.transition(
            skill_id,
            CandidateStatus.PROMOTED,
            evidence,
            human_approved=True,
        )
        receipt = {
            "receipt_type": "PATCH_MISSION_REPAIR_SKILL_PROMOTION",
            "status": "SKILL_PROMOTED",
            "candidate_id": candidate_id,
            "candidate_status_before": row["status"],
            "candidate_status_after": CandidateStatus.PROMOTED.value,
            "skill_id": skill_id,
            "skill_status_before": skill_row["status"],
            "skill_status_after": CandidateStatus.PROMOTED.value,
            "approved_success_count": approved_count,
            "minimum_approved_success_count": minimum,
            "human_approved": True,
            "reason": reason,
            "promotion_executed": True,
            "command_executed": False,
            "repo_write_executed": False,
            "push_or_pr_executed": False,
            "approval_bypass_allowed": False,
            "claim_ceiling": (
                "skill lifecycle promotion only; no command, repo write, push, PR, "
                "or future action approval is executed by this step"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE evolution_candidates SET status=?,result_json=?,updated_at=? WHERE candidate_id=?",
                (
                    CandidateStatus.PROMOTED.value,
                    json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    candidate_id,
                ),
            )
            self.ledger.append(
                "patch_mission_repair_skill_promoted",
                receipt,
                connection,
            )
        return receipt

    def _patch_mission_failure_learning_context(
        self,
        *,
        mission_record: dict[str, Any],
        query_text: str,
        selected_file: str | None,
        pytest_target: str | None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        query_parts = [
            "patch_mission_failure_learning",
            str(mission_record.get("mission") or ""),
            selected_file or "",
            pytest_target or "",
            query_text[:1000],
        ]
        memories = self.memories.retrieve(
            " ".join(part for part in query_parts if part),
            limit=limit * 3,
            memory_types=["procedural"],
        )
        selected: list[dict[str, Any]] = []
        current_mission_id = str(mission_record.get("mission_id") or "")
        for item in memories:
            content = item.get("content")
            if not isinstance(content, dict):
                continue
            if content.get("kind") != "patch_mission_failure_learning":
                continue
            score = float(item.get("score", 0.0))
            if current_mission_id and content.get("mission_id") == current_mission_id:
                score += 0.2
            if selected_file and content.get("selected_file") == selected_file:
                score += 0.2
            if pytest_target and content.get("pytest_target") == pytest_target:
                score += 0.3
            selected.append({**item, "score": score})
        selected.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return selected[:limit]

    @staticmethod
    def _patch_mission_failure_learning_lines(
        memories: list[dict[str, Any]]
    ) -> list[str]:
        if not memories:
            return ["- (none found)"]
        lines: list[str] = []
        for item in memories:
            content = item.get("content") if isinstance(item, dict) else {}
            if not isinstance(content, dict):
                continue
            memory_id = str(item.get("memory_id") or "")
            cause = str(content.get("cause") or "")[:240]
            fix = str(content.get("fix") or "")[:240]
            regression = str(content.get("regression") or "")[:240]
            lines.extend(
                [
                    f"- Memory: {memory_id}",
                    f"  Cause: {cause or '(not captured)'}",
                    f"  Fix: {fix or '(not captured)'}",
                    f"  Regression: {regression or '(not captured)'}",
                ]
            )
        return lines or ["- (none found)"]

    @staticmethod
    def _patch_mission_pytest_target_from_command(command: Any) -> str | None:
        if not isinstance(command, list) or command[:3] != ["python", "-m", "pytest"]:
            return None
        if len(command) >= 4 and isinstance(command[3], str):
            return command[3]
        return None

    @staticmethod
    def _patch_mission_failure_cause_note(
        *,
        output_text: str,
        selected_file: str,
        pytest_target: str | None,
        synthesis: dict[str, Any],
    ) -> str:
        actual = synthesis.get("actual")
        expected = synthesis.get("expected")
        target = pytest_target or selected_file
        if actual is not None and expected is not None:
            return (
                f"{target} failed because observed value {actual!r} did not match expected {expected!r}."
            )
        first_line = next(
            (line.strip() for line in output_text.splitlines() if line.strip()),
            "test failure did not expose a concise first line",
        )
        return f"{target} failed; nearest clue: {first_line[:240]}"

    @staticmethod
    def _patch_mission_failure_fix_note(synthesis: dict[str, Any]) -> str:
        status = str(synthesis.get("status") or "")
        target_file = synthesis.get("target_file")
        if status == "synthesized_literal_diff" and target_file:
            return f"Apply the synthesized literal diff to {target_file}, then rerun the failing target and affected tests."
        return "No safe automatic patch was synthesized; inspect the selected failure file before proposing a patch."

    @staticmethod
    def _patch_mission_failure_regression_note(
        *, command: Any, pytest_target: str | None
    ) -> str:
        if isinstance(command, list) and command[:3] == ["python", "-m", "pytest"]:
            if pytest_target:
                return f"Rerun `python -m pytest {pytest_target}` first, then broaden to the affected suite."
            return "Rerun `python -m pytest` after the patch to detect regressions."
        return "Rerun the approved verification command after the patch."

    @staticmethod
    def _patch_mission_pytest_literal_mismatch(
        output_text: str,
    ) -> tuple[str, str] | None:
        match = re.search(
            r"AssertionError:\s+assert\s+(['\"])(?P<actual>.*?)\1\s*==\s*(['\"])(?P<expected>.*?)\3",
            output_text,
            flags=re.DOTALL,
        )
        if not match:
            return None
        actual = match.group("actual")
        expected = match.group("expected")
        if not actual or not expected or actual == expected:
            return None
        if len(actual) > 200 or len(expected) > 200:
            return None
        return actual, expected

    def _patch_mission_adjacent_source_candidates(
        self,
        *,
        repo_root: Path,
        repo_map: Any,
        selected_file: str,
    ) -> list[str]:
        candidates: list[str] = []
        selected_path = self._patch_mission_repo_file(repo_root, selected_file)
        try:
            selected_text = self._read_patch_mission_text(selected_path)
        except UnicodeError:
            selected_text = ""
        for module in re.findall(
            r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\s+",
            selected_text,
            re.MULTILINE,
        ):
            candidates.extend(self._module_candidates(module))
        for module in re.findall(
            r"^\s*import\s+([A-Za-z_][\w.]*)",
            selected_text,
            re.MULTILINE,
        ):
            candidates.extend(self._module_candidates(module))
        selected = selected_file.replace("\\", "/")
        if selected.startswith("tests/test_"):
            base = selected.removeprefix("tests/test_")
            candidates.extend([base, f"src/{base}"])
        elif selected.startswith("test_"):
            candidates.append(selected.removeprefix("test_"))
        for item in getattr(repo_map, "files", []):
            path = str(item.path).replace("\\", "/")
            if path.endswith(".py") and not path.startswith("tests/"):
                candidates.append(path)
        seen: set[str] = set()
        existing: list[str] = []
        for candidate in candidates:
            normalized = str(candidate).replace("\\", "/").lstrip("./")
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                self._patch_mission_repo_file(repo_root, normalized)
            except (OSError, ValueError, PermissionError):
                continue
            existing.append(normalized)
        return existing

    @staticmethod
    def _module_candidates(module: str) -> list[str]:
        path = module.replace(".", "/")
        return [f"{path}.py", f"src/{path}.py", f"{path}/__init__.py"]

    @staticmethod
    def _read_patch_mission_text(path: Path, max_bytes: int = 262144) -> str:
        data = path.read_bytes()
        if len(data) > max_bytes:
            raise ValueError("patch mission source exceeds max_bytes")
        return data.decode("utf-8")

    @staticmethod
    def _unified_diff_for_patch_mission(
        relative_path: str, original: str, revised: str
    ) -> str:
        return "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                revised.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
                lineterm="\n",
            )
        )

    def _patch_mission_test_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        test_action_ids = self._patch_mission_test_step_action_ids(mission_record)
        if action_id:
            candidate = str(action_id)
            if candidate not in test_action_ids:
                raise ValueError("test result action is not part of this patch mission")
            return candidate
        if not test_action_ids:
            raise ValueError("from-test-result mode requires a prior test step")
        return test_action_ids[0]

    def _patch_mission_action_result_output(self, action_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT tool,status,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "run_command":
            raise ValueError("source action must be a run_command test action")
        if row["status"] not in {
            ActionStatus.SUCCEEDED.value,
            ActionStatus.FAILED.value,
        }:
            raise ValueError(f"test action has not produced a result: {row['status']}")
        if not row["result_json"]:
            raise ValueError("test action has no result_json")
        payload = json.loads(row["result_json"])
        result = payload.get("result") if isinstance(payload, dict) else None
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            raise ValueError("test action result has no output object")
        return output

    def _patch_mission_apply_patch_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        draft_action_id = self._patch_mission_draft_action_id(
            mission_record, action_id
        )
        draft_text = self._patch_mission_outbox_draft_text(draft_action_id)
        diff_text = self._extract_unified_diff_from_patch_mission_draft(draft_text)
        target_file, revised = self._apply_single_file_unified_diff(
            repo_root=repo_root,
            diff_text=diff_text,
        )
        self._allow_explicit_patch_mission_write_root(repo_root)
        return ActionSpec(
            tool="write_file",
            arguments={
                "path": str(repo_root / target_file),
                "content": revised,
                "max_bytes": 2 * 1024 * 1024,
            },
            purpose=(
                "Apply an owner-reviewed Patch Mission unified diff to the canonical repo file"
            ),
            expected_result=(
                "Exact canonical repo file is updated from the approved outbox diff for test rerun"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            acceptance=["output contains path"],
        )

    def _patch_mission_pr_summary_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        passing_test_id = self._patch_mission_passing_test_action_id(
            mission_record, action_id
        )
        passing_output = self._patch_mission_action_result_output(passing_test_id)
        if int(passing_output.get("returncode", -1)) != 0:
            raise ValueError("PR summary requires a passing verification test action")
        failing_test_id = self._patch_mission_failing_test_action_id(
            mission_record, passing_test_id
        )
        failing_output = self._patch_mission_action_result_output(failing_test_id)
        ci_source = self._patch_mission_ci_source_context_for_test_action(
            mission_record, failing_test_id
        )
        apply_step = self._patch_mission_latest_step(mission_record, "apply-patch")
        apply_action_id = str(apply_step.get("action_id") or "")
        self._require_patch_mission_action_status(
            apply_action_id, ActionStatus.SUCCEEDED.value
        )
        draft_action_id = str(apply_step.get("source_action_id") or "")
        draft_text = self._patch_mission_outbox_draft_text(draft_action_id)
        diff_text = self._extract_unified_diff_from_patch_mission_draft(draft_text)
        changed_files = self._patch_mission_diff_changed_files(diff_text)
        title = self._patch_mission_pr_title(mission_record, changed_files)
        repair_learning = self._record_patch_mission_successful_repair_pattern(
            mission_record=mission_record,
            failing_test_id=failing_test_id,
            failing_output=failing_output,
            passing_test_id=passing_test_id,
            passing_output=passing_output,
            apply_action_id=apply_action_id,
            draft_action_id=draft_action_id,
            ci_source=ci_source,
        )
        summary = self._patch_mission_pr_summary_text(
            mission_record=mission_record,
            title=title,
            changed_files=changed_files,
            diff_text=diff_text,
            failing_test_id=failing_test_id,
            failing_output=failing_output,
            passing_test_id=passing_test_id,
            passing_output=passing_output,
            apply_action_id=apply_action_id,
            draft_action_id=draft_action_id,
            ci_source=ci_source,
            repair_learning=repair_learning,
        )
        outbox = (
            self.config.outbox_path
            / "patch-missions"
            / str(mission_record["mission_id"])
            / "pr-summary.md"
        )
        return ActionSpec(
            tool="write_file",
            arguments={
                "path": str(outbox),
                "content": summary,
                "max_bytes": 2 * 1024 * 1024,
            },
            purpose=(
                "Write a verified Patch Mission PR summary to WLS outbox only"
            ),
            expected_result=(
                "Owner-reviewable PR summary cites applied diff and test evidence without pushing"
            ),
            risk=RiskLevel.REVERSIBLE_WRITE,
            goal_id=goal_id,
            acceptance=["output contains path"],
        )

    def _patch_mission_git_metadata_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        repo_map: Any,
        goal_id: str | None,
    ) -> ActionSpec:
        repo_state_digest = self._patch_mission_repo_state_digest(
            repo_root, repo_map
        )
        git_config_digest = self._patch_mission_git_config_digest(repo_root)
        return ActionSpec(
            tool="inspect_git_worktree",
            arguments={
                "path": str(repo_root),
                "max_output_bytes": 256 * 1024,
            },
            purpose=(
                "Inspect local git branch, status, and diff metadata for Patch Mission commit prep"
            ),
            expected_result=(
                "Bounded read-only git metadata for preparing a commit checklist"
            ),
            risk=RiskLevel.READ,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "inspect_git_worktree",
                    "path": str(repo_root),
                    "repo_state_digest": repo_state_digest,
                    "git_config_digest": git_config_digest,
                    "mission_id": mission_record.get("mission_id"),
                }
            ),
            acceptance=["output contains branch_status"],
        )

    def _patch_mission_git_prep_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        metadata_action_id = self._patch_mission_git_metadata_action_id(
            mission_record, action_id
        )
        metadata = self._patch_mission_git_metadata_output(
            metadata_action_id, repo_root
        )
        changed_files = self._patch_mission_git_changed_files(metadata)
        checklist = self._patch_mission_git_prep_text(
            mission_record=mission_record,
            metadata_action_id=metadata_action_id,
            metadata=metadata,
            changed_files=changed_files,
        )
        outbox = (
            self.config.outbox_path
            / "patch-missions"
            / str(mission_record["mission_id"])
            / "commit-ready-checklist.md"
        )
        return ActionSpec(
            tool="write_file",
            arguments={
                "path": str(outbox),
                "content": checklist,
                "max_bytes": 2 * 1024 * 1024,
            },
            purpose=(
                "Write local git commit-preparation checklist to WLS outbox only"
            ),
            expected_result=(
                "Owner-reviewable commit checklist cites git metadata without committing or pushing"
            ),
            risk=RiskLevel.REVERSIBLE_WRITE,
            goal_id=goal_id,
            acceptance=["output contains path"],
        )

    def _patch_mission_commit_draft_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        prep_action_id = self._patch_mission_git_prep_action_id(
            mission_record, action_id
        )
        self._require_patch_mission_action_status(
            prep_action_id, ActionStatus.SUCCEEDED.value
        )
        prep_text = self._patch_mission_outbox_draft_text(prep_action_id)
        summary_action_id = self._patch_mission_latest_succeeded_write_step_action_id(
            mission_record, "pr-summary"
        )
        summary_text = self._patch_mission_outbox_draft_text(summary_action_id)
        changed_files = self._patch_mission_changed_files_from_checklist(prep_text)
        if not changed_files:
            raise ValueError("commit-draft requires changed files in git prep checklist")
        subject, body = self._patch_mission_commit_message_from_evidence(
            mission_record=mission_record,
            prep_text=prep_text,
            summary_text=summary_text,
            changed_files=changed_files,
        )
        return ActionSpec(
            tool="run_command",
            arguments={
                "command": [
                    "git",
                    "-c",
                    "user.name=WLS",
                    "-c",
                    "user.email=wls@example.invalid",
                    "commit",
                    "-am",
                    subject,
                    "-m",
                    body,
                ],
                "cwd": str(repo_root),
                "timeout": 120,
                "max_output_bytes": 256 * 1024,
            },
            purpose=(
                "Create a local git commit from owner-approved Patch Mission checklist and summary"
            ),
            expected_result=(
                "Local commit is created only after exact owner approval; no push or PR is created"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "run_command",
                    "command": "git commit -am",
                    "cwd": str(repo_root),
                    "prep_action_id": prep_action_id,
                    "summary_action_id": summary_action_id,
                    "changed_files": changed_files,
                }
            ),
            acceptance=["output contains returncode"],
        )

    def _patch_mission_remote_summary_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        metadata_action_id = self._patch_mission_git_metadata_action_id(
            mission_record, action_id
        )
        metadata = self._patch_mission_git_metadata_output(
            metadata_action_id, repo_root
        )
        summary = self._patch_mission_remote_summary_text(
            mission_record=mission_record,
            metadata_action_id=metadata_action_id,
            metadata=metadata,
        )
        outbox = (
            self.config.outbox_path
            / "patch-missions"
            / str(mission_record["mission_id"])
            / "remote-readiness-summary.md"
        )
        return ActionSpec(
            tool="write_file",
            arguments={
                "path": str(outbox),
                "content": summary,
                "max_bytes": 2 * 1024 * 1024,
            },
            purpose=(
                "Write read-only Patch Mission remote/branch metadata summary to WLS outbox"
            ),
            expected_result=(
                "Owner-reviewable remote summary cites local git metadata without fetching, pushing, or opening PRs"
            ),
            risk=RiskLevel.REVERSIBLE_WRITE,
            goal_id=goal_id,
            acceptance=["output contains path"],
        )

    def _patch_mission_branch_draft_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        remote_summary_id = self._patch_mission_remote_summary_action_id(
            mission_record, action_id
        )
        self._require_patch_mission_action_status(
            remote_summary_id, ActionStatus.SUCCEEDED.value
        )
        remote_summary = self._patch_mission_outbox_draft_text(remote_summary_id)
        commit_action_id = self._patch_mission_latest_succeeded_run_step_action_id(
            mission_record, "commit-draft"
        )
        commit_output = self._patch_mission_run_command_output(commit_action_id)
        if int(commit_output.get("returncode", -1)) != 0:
            raise ValueError("branch-draft requires a successful local commit action")
        branch_name = self._patch_mission_branch_name(
            mission_record=mission_record,
            remote_summary=remote_summary,
        )
        return ActionSpec(
            tool="run_command",
            arguments={
                "command": ["git", "checkout", "-b", branch_name],
                "cwd": str(repo_root),
                "timeout": 120,
                "max_output_bytes": 256 * 1024,
            },
            purpose=(
                "Create a local Patch Mission branch from verified local commit and remote readiness evidence"
            ),
            expected_result=(
                "Local branch is created only after exact owner approval; no push or PR is created"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "run_command",
                    "command": "git checkout -b",
                    "cwd": str(repo_root),
                    "remote_summary_id": remote_summary_id,
                    "commit_action_id": commit_action_id,
                    "branch_name": branch_name,
                }
            ),
            acceptance=["output contains returncode"],
        )

    def _patch_mission_remote_live_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        goal_id: str | None,
    ) -> ActionSpec:
        return ActionSpec(
            tool="inspect_git_remote_live",
            arguments={
                "path": str(repo_root),
                "remote": "origin",
                "max_output_bytes": 256 * 1024,
            },
            purpose=(
                "Read live remote branch and public GitHub metadata before any Patch Mission push"
            ),
            expected_result=(
                "Bounded read-only remote refs and optional public GitHub metadata"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "inspect_git_remote_live",
                    "path": str(repo_root),
                    "mission_id": mission_record.get("mission_id"),
                    "requested_at": utc_now(),
                }
            ),
            acceptance=["output contains remote_heads"],
        )

    def _patch_mission_remote_live_summary_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        live_action_id = self._patch_mission_remote_live_action_id(
            mission_record, action_id
        )
        live_output = self._patch_mission_remote_live_output(live_action_id, repo_root)
        summary = self._patch_mission_remote_live_summary_text(
            mission_record=mission_record,
            live_action_id=live_action_id,
            live_output=live_output,
        )
        outbox = (
            self.config.outbox_path
            / "patch-missions"
            / str(mission_record["mission_id"])
            / "remote-live-summary.md"
        )
        return ActionSpec(
            tool="write_file",
            arguments={
                "path": str(outbox),
                "content": summary,
                "max_bytes": 2 * 1024 * 1024,
            },
            purpose=(
                "Write read-only live remote/GitHub inspection summary to WLS outbox"
            ),
            expected_result=(
                "Owner-reviewable live remote summary cites branch refs and public metadata without pushing"
            ),
            risk=RiskLevel.REVERSIBLE_WRITE,
            goal_id=goal_id,
            acceptance=["output contains path"],
        )

    def _patch_mission_push_draft_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        live_summary_id = self._patch_mission_remote_live_summary_action_id(
            mission_record, action_id
        )
        self._require_patch_mission_action_status(
            live_summary_id, ActionStatus.SUCCEEDED.value
        )
        self._patch_mission_outbox_draft_text(live_summary_id)
        branch_action_id = self._patch_mission_latest_succeeded_run_step_action_id(
            mission_record, "branch-draft"
        )
        branch_output = self._patch_mission_run_command_output(branch_action_id)
        if int(branch_output.get("returncode", -1)) != 0:
            raise ValueError("push-draft requires a successful local branch action")
        branch_name = self._patch_mission_branch_name_from_action(branch_action_id)
        if not branch_name.startswith("wls/"):
            raise ValueError("push-draft only supports prepared wls/... branches")
        return ActionSpec(
            tool="run_command",
            arguments={
                "command": ["git", "push", "-u", "origin", branch_name],
                "cwd": str(repo_root),
                "timeout": 120,
                "max_output_bytes": 256 * 1024,
            },
            purpose=(
                "Push the prepared local Patch Mission branch to the configured origin remote"
            ),
            expected_result=(
                "Prepared branch is pushed only after exact owner approval; no PR is created"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "run_command",
                    "command": "git push -u origin",
                    "cwd": str(repo_root),
                    "live_summary_id": live_summary_id,
                    "branch_action_id": branch_action_id,
                    "branch_name": branch_name,
                }
            ),
            acceptance=["output contains returncode"],
        )

    def _patch_mission_pr_create_draft_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        push_action_id = self._patch_mission_push_action_id(
            mission_record, action_id
        )
        push_output = self._patch_mission_run_command_output(push_action_id)
        if int(push_output.get("returncode", -1)) != 0:
            raise ValueError("pr-create-draft requires a successful branch push action")
        branch_name = self._patch_mission_pushed_branch_name_from_action(push_action_id)
        if not branch_name.startswith("wls/"):
            raise ValueError("pr-create-draft only supports prepared wls/... branches")
        summary_action_id = self._patch_mission_latest_succeeded_write_step_action_id(
            mission_record, "pr-summary"
        )
        summary_text = self._patch_mission_outbox_draft_text(summary_action_id)
        owner, repo, remote_evidence_id, remote_evidence = (
            self._patch_mission_github_repo_from_remote_evidence(mission_record)
        )
        title, body = self._patch_mission_pr_create_payload(
            mission_record=mission_record,
            summary_text=summary_text,
            push_action_id=push_action_id,
            branch_name=branch_name,
            remote_evidence_id=remote_evidence_id,
        )
        base = self._patch_mission_default_base_branch(remote_evidence)
        return ActionSpec(
            tool="create_github_pull_request",
            arguments={
                "owner": owner,
                "repo": repo,
                "title": title,
                "body": body,
                "base": base,
                "head": branch_name,
                "draft": True,
                "token_env": "GITHUB_TOKEN",
                "api_url": "https://api.github.com",
            },
            purpose=(
                "Create a draft GitHub pull request from verified Patch Mission summary and pushed branch evidence"
            ),
            expected_result=(
                "Draft PR is created only after exact owner approval and a GitHub token; no merge is attempted"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "create_github_pull_request",
                    "owner": owner,
                    "repo": repo,
                    "base": base,
                    "head": branch_name,
                    "summary_action_id": summary_action_id,
                    "push_action_id": push_action_id,
                    "remote_evidence_id": remote_evidence_id,
                }
            ),
            acceptance=["output contains url", "output contains number"],
        )

    def _patch_mission_pr_status_action(
        self,
        *,
        mission_record: dict[str, Any],
        target: str | None,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        if target:
            owner, repo, number = self._patch_mission_pr_ref_from_url(target)
            source = {"kind": "explicit_pr_url", "target": target}
        else:
            pr_action_id = self._patch_mission_pr_create_action_id(
                mission_record, action_id
            )
            owner, repo, number = self._patch_mission_pr_ref_from_create_action(
                pr_action_id
            )
            source = {"kind": "created_pr_action", "action_id": pr_action_id}
        return ActionSpec(
            tool="inspect_github_pr_status",
            arguments={
                "owner": owner,
                "repo": repo,
                "number": number,
                "token_env": "GITHUB_TOKEN",
                "api_url": "https://api.github.com",
            },
            purpose=(
                "Read GitHub PR status, mergeability metadata, and CI/check summaries for Patch Mission follow-up"
            ),
            expected_result=(
                "Read-only PR/CI status evidence is captured without commenting, merging, pushing, or changing GitHub state"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "inspect_github_pr_status",
                    "owner": owner,
                    "repo": repo,
                    "number": number,
                    "source": source,
                    "requested_at": utc_now(),
                }
            ),
            acceptance=["output contains pull_request", "output contains check_runs"],
        )

    def _patch_mission_ci_fix_plan_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        repo_map: Any,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        log_action_id = self._patch_mission_ci_log_evidence_action_id_if_present(
            mission_record, action_id
        )
        status_action_id = (
            self._patch_mission_ci_log_evidence_status_action_id(
                mission_record, log_action_id
            )
            if log_action_id
            else self._patch_mission_pr_status_action_id(mission_record, action_id)
        )
        status_output = self._patch_mission_pr_status_output(status_action_id)
        log_output = (
            self._patch_mission_ci_log_evidence_output(log_action_id)
            if log_action_id
            else None
        )
        plan_text = self._patch_mission_ci_fix_plan_text(
            mission_record=mission_record,
            repo_root=repo_root,
            repo_map=repo_map,
            status_action_id=status_action_id,
            status_output=status_output,
            log_action_id=log_action_id,
            log_output=log_output,
        )
        outbox = (
            self.config.outbox_path
            / "patch-missions"
            / str(mission_record["mission_id"])
            / "ci-fix-plan.md"
        )
        return ActionSpec(
            tool="write_file",
            arguments={
                "path": str(outbox),
                "content": plan_text,
                "max_bytes": 2 * 1024 * 1024,
            },
            purpose=(
                "Write a bounded local repair plan from approved GitHub PR/CI status evidence"
            ),
            expected_result=(
                "Owner-reviewable CI repair plan identifies failure clues and the next local Patch Mission step without changing repo or GitHub state"
            ),
            risk=RiskLevel.REVERSIBLE_WRITE,
            goal_id=goal_id,
            acceptance=["output contains path"],
        )

    def _patch_mission_ci_log_evidence_action(
        self,
        *,
        mission_record: dict[str, Any],
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        status_action_id = self._patch_mission_pr_status_action_id(
            mission_record, action_id
        )
        status_output = self._patch_mission_pr_status_output(status_action_id)
        workflow_run_ids = self._patch_mission_failed_workflow_run_ids(status_output)
        owner = str(status_output.get("owner") or "").strip()
        repo = str(status_output.get("repo") or "").strip()
        if not owner or not repo:
            owner, repo, _number = self._patch_mission_pr_ref_from_status_action(
                status_action_id
            )
        return ActionSpec(
            tool="inspect_github_ci_logs",
            arguments={
                "owner": owner,
                "repo": repo,
                "workflow_run_ids": workflow_run_ids,
                "token_env": "GITHUB_TOKEN",
                "api_url": "https://api.github.com",
                "max_runs": 3,
                "max_jobs": 5,
                "max_log_bytes": 64 * 1024,
            },
            purpose=(
                "Read bounded failed GitHub Actions job logs for Patch Mission repair evidence"
            ),
            expected_result=(
                "Read-only CI log excerpts and failure clues are captured without writing to GitHub"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "inspect_github_ci_logs",
                    "owner": owner,
                    "repo": repo,
                    "status_action_id": status_action_id,
                    "workflow_run_ids": workflow_run_ids,
                }
            ),
            acceptance=["output contains logs", "output contains failure_clues"],
        )

    def _patch_mission_pr_update_push_draft_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        status_action_id = self._patch_mission_pr_status_action_id(
            mission_record, action_id
        )
        status_output = self._patch_mission_pr_status_output(status_action_id)
        head_ref = str(status_output.get("head_ref") or "").strip()
        if not head_ref:
            raise ValueError("pr-update-push-draft requires PR head_ref evidence")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", head_ref):
            raise ValueError("PR head_ref contains unsupported characters")
        if not head_ref.startswith("wls/"):
            raise ValueError("pr-update-push-draft only supports prepared wls/... PR branches")
        summary_action_id = self._patch_mission_latest_succeeded_write_step_action_id(
            mission_record, "pr-summary"
        )
        commit_action_id = self._patch_mission_latest_succeeded_run_step_action_id(
            mission_record, "commit-draft"
        )
        commit_output = self._patch_mission_run_command_output(commit_action_id)
        if int(commit_output.get("returncode", -1)) != 0:
            raise ValueError("pr-update-push-draft requires a successful local commit")
        return ActionSpec(
            tool="run_command",
            arguments={
                "command": ["git", "push", "origin", f"HEAD:{head_ref}"],
                "cwd": str(repo_root),
                "timeout": 120,
                "max_output_bytes": 256 * 1024,
            },
            purpose=(
                "Push a verified CI-sourced local repair commit to the existing Patch Mission PR branch"
            ),
            expected_result=(
                "Existing PR branch is updated only after exact owner approval; no force push, comment, review, merge, or new PR is created"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "run_command",
                    "command": "git push origin HEAD:<pr_head_ref>",
                    "cwd": str(repo_root),
                    "status_action_id": status_action_id,
                    "summary_action_id": summary_action_id,
                    "commit_action_id": commit_action_id,
                    "head_ref": head_ref,
                }
            ),
            acceptance=["output contains returncode"],
        )

    def _patch_mission_pr_update_status_action(
        self,
        *,
        mission_record: dict[str, Any],
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        push_action_id = self._patch_mission_pr_update_push_action_id(
            mission_record, action_id
        )
        push_output = self._patch_mission_run_command_output(push_action_id)
        if int(push_output.get("returncode", -1)) != 0:
            raise ValueError("pr-update-status requires a successful PR update push")
        pre_status_id = self._patch_mission_pr_update_pre_status_id_from_push(
            mission_record, push_action_id
        )
        owner, repo, number = self._patch_mission_pr_ref_from_status_action(
            pre_status_id
        )
        return ActionSpec(
            tool="inspect_github_pr_status",
            arguments={
                "owner": owner,
                "repo": repo,
                "number": number,
                "token_env": "GITHUB_TOKEN",
                "api_url": "https://api.github.com",
            },
            purpose=(
                "Recheck GitHub PR status after an owner-approved Patch Mission PR branch update push"
            ),
            expected_result=(
                "Read-only post-update PR/CI status evidence is captured without commenting, merging, pushing, or changing GitHub state"
            ),
            risk=RiskLevel.HIGH,
            goal_id=goal_id,
            idempotency_key=digest_json(
                {
                    "tool": "inspect_github_pr_status",
                    "owner": owner,
                    "repo": repo,
                    "number": number,
                    "source": {
                        "kind": "post_update_push_action",
                        "action_id": push_action_id,
                        "pre_status_action_id": pre_status_id,
                    },
                    "requested_at": utc_now(),
                }
            ),
            acceptance=["output contains pull_request", "output contains check_runs"],
        )

    def _patch_mission_pr_update_verify_action(
        self,
        *,
        mission_record: dict[str, Any],
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        post_status_id = self._patch_mission_pr_update_status_action_id(
            mission_record, action_id
        )
        post_output = self._patch_mission_pr_status_output(post_status_id)
        push_action_id = self._patch_mission_pr_update_push_id_from_status(
            mission_record, post_status_id
        )
        pre_status_id = self._patch_mission_pr_update_pre_status_id_from_push(
            mission_record, push_action_id
        )
        pre_output = self._patch_mission_pr_status_output(pre_status_id)
        summary = self._patch_mission_pr_update_verify_text(
            mission_record=mission_record,
            pre_status_id=pre_status_id,
            pre_output=pre_output,
            push_action_id=push_action_id,
            post_status_id=post_status_id,
            post_output=post_output,
        )
        outbox = (
            self.config.outbox_path
            / "patch-missions"
            / str(mission_record["mission_id"])
            / "pr-update-verify.md"
        )
        return ActionSpec(
            tool="write_file",
            arguments={
                "path": str(outbox),
                "content": summary,
                "max_bytes": 2 * 1024 * 1024,
            },
            purpose=(
                "Write an outbox-only comparison of PR/CI status before and after the Patch Mission branch update"
            ),
            expected_result=(
                "Owner-reviewable comparison cites pre-update status, update push, and post-update status without changing GitHub state"
            ),
            risk=RiskLevel.REVERSIBLE_WRITE,
            goal_id=goal_id,
            acceptance=["output contains path"],
        )

    def _patch_mission_pr_update_next_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        repo_map: Any,
        action_id: str | None,
        goal_id: str | None,
    ) -> ActionSpec:
        verify_action_id = self._patch_mission_pr_update_verify_action_id(
            mission_record, action_id
        )
        verify_text = self._patch_mission_write_file_content(verify_action_id)
        post_status_id = self._patch_mission_plan_line(
            verify_text, "Post-update PR status action"
        )
        if not post_status_id:
            raise ValueError("PR update verification has no post-update status action")
        post_failures = self._patch_mission_pr_update_verify_after_failure_count(
            verify_text
        )
        if post_failures > 0:
            return self._patch_mission_ci_fix_plan_action(
                mission_record=mission_record,
                repo_root=repo_root,
                repo_map=repo_map,
                action_id=post_status_id,
                goal_id=goal_id,
            )
        outbox = (
            self.config.outbox_path
            / "patch-missions"
            / str(mission_record["mission_id"])
            / "pr-update-next.md"
        )
        conclusion = self._patch_mission_pr_update_next_text(
            mission_record=mission_record,
            verify_action_id=verify_action_id,
            verify_text=verify_text,
            post_failures=post_failures,
        )
        return ActionSpec(
            tool="write_file",
            arguments={
                "path": str(outbox),
                "content": conclusion,
                "max_bytes": 2 * 1024 * 1024,
            },
            purpose=(
                "Write a safe next-step note after post-update PR/CI verification shows no immediate failures"
            ),
            expected_result=(
                "Owner-reviewable wait or completion note is written without changing repo or GitHub state"
            ),
            risk=RiskLevel.REVERSIBLE_WRITE,
            goal_id=goal_id,
            acceptance=["output contains path"],
        )

    def _patch_mission_ci_next_action(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        repo_map: Any,
        action_id: str | None,
    ) -> ActionSpec:
        fix_plan_action_id = self._patch_mission_ci_fix_plan_action_id(
            mission_record, action_id
        )
        plan_text = self._patch_mission_write_file_content(fix_plan_action_id)
        next_mode, next_target = self._patch_mission_ci_fix_plan_next_step(plan_text)
        skill_id = self._patch_mission_approved_repair_skill_id_from_text(plan_text)
        if not skill_id:
            approved_skills = self._patch_mission_approved_repair_skill_context(
                mission_record=mission_record,
                query_text=plan_text,
                selected_file=next_target,
                pytest_target=next_target if next_mode == "test" else None,
                limit=1,
            )
            if approved_skills:
                skill_id = str(approved_skills[0].get("skill_id") or "") or None
        action = self._patch_mission_followup_action(
            mission_record=mission_record,
            repo_root=repo_root,
            repo_map=repo_map,
            mode=next_mode,
            target=next_target,
            draft=None,
            action_id=None,
        )
        return self._patch_mission_annotate_approved_skill_action_candidate(
            action=action,
            skill_id=skill_id,
            source_action_id=fix_plan_action_id,
            source_mode="ci-fix-plan",
        )

    def _patch_mission_git_prep_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        prep_action_ids = self._patch_mission_step_action_ids(
            mission_record, "git-prep"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in prep_action_ids:
                raise ValueError("git prep action is not part of this patch mission")
            return candidate
        if not prep_action_ids:
            raise ValueError("commit-draft mode requires a prior git-prep step")
        return prep_action_ids[0]

    def _patch_mission_remote_summary_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        summary_action_ids = self._patch_mission_step_action_ids(
            mission_record, "remote-summary"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in summary_action_ids:
                raise ValueError("remote summary action is not part of this patch mission")
            return candidate
        if not summary_action_ids:
            raise ValueError("branch-draft mode requires a prior remote-summary step")
        return summary_action_ids[0]

    def _patch_mission_remote_live_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        live_action_ids = self._patch_mission_step_action_ids(
            mission_record, "remote-live"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in live_action_ids:
                raise ValueError("remote live action is not part of this patch mission")
            return candidate
        if not live_action_ids:
            raise ValueError(
                "remote-live-summary mode requires a prior remote-live step"
            )
        return live_action_ids[0]

    def _patch_mission_remote_live_summary_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        summary_action_ids = self._patch_mission_step_action_ids(
            mission_record, "remote-live-summary"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in summary_action_ids:
                raise ValueError(
                    "remote live summary action is not part of this patch mission"
                )
            return candidate
        if not summary_action_ids:
            raise ValueError(
                "push-draft mode requires a prior remote-live-summary step"
            )
        return summary_action_ids[0]

    def _patch_mission_push_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        push_action_ids = self._patch_mission_step_action_ids(
            mission_record, "push-draft"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in push_action_ids:
                raise ValueError("push action is not part of this patch mission")
            return candidate
        if not push_action_ids:
            raise ValueError("pr-create-draft mode requires a prior push-draft step")
        return push_action_ids[0]

    def _patch_mission_pr_create_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        pr_action_ids = self._patch_mission_step_action_ids(
            mission_record, "pr-create-draft"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in pr_action_ids:
                raise ValueError("PR creation action is not part of this patch mission")
            return candidate
        if not pr_action_ids:
            raise ValueError(
                "pr-status mode requires a prior pr-create-draft step or --target PR URL"
            )
        return pr_action_ids[0]

    def _patch_mission_pr_status_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        status_action_ids = self._patch_mission_pr_status_like_action_ids(
            mission_record
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in status_action_ids:
                raise ValueError("PR status action is not part of this patch mission")
            return candidate
        if not status_action_ids:
            raise ValueError("ci-fix-plan mode requires a prior pr-status step")
        return status_action_ids[0]

    def _patch_mission_pr_status_like_action_ids(
        self, mission_record: dict[str, Any]
    ) -> list[str]:
        ids: list[str] = []
        for mode in ("pr-status", "pr-update-status"):
            ids.extend(self._patch_mission_step_action_ids(mission_record, mode))
        return ids

    def _patch_mission_pr_update_push_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        push_action_ids = self._patch_mission_step_action_ids(
            mission_record, "pr-update-push-draft"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in push_action_ids:
                raise ValueError("PR update push action is not part of this patch mission")
            return candidate
        if not push_action_ids:
            raise ValueError("pr-update-status mode requires a prior pr-update-push-draft step")
        return push_action_ids[0]

    def _patch_mission_pr_update_status_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        status_action_ids = self._patch_mission_step_action_ids(
            mission_record, "pr-update-status"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in status_action_ids:
                raise ValueError("PR update status action is not part of this patch mission")
            self._require_patch_mission_action_status(
                candidate, ActionStatus.SUCCEEDED.value
            )
            return candidate
        for candidate in status_action_ids:
            try:
                self._require_patch_mission_action_status(
                    candidate, ActionStatus.SUCCEEDED.value
                )
                return candidate
            except (KeyError, ValueError):
                continue
        raise ValueError("pr-update-verify mode requires a succeeded pr-update-status step")

    def _patch_mission_pr_update_verify_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        verify_action_ids = self._patch_mission_step_action_ids(
            mission_record, "pr-update-verify"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in verify_action_ids:
                raise ValueError("PR update verification action is not part of this patch mission")
            self._require_patch_mission_action_status(
                candidate, ActionStatus.SUCCEEDED.value
            )
            return candidate
        for candidate in verify_action_ids:
            try:
                self._require_patch_mission_action_status(
                    candidate, ActionStatus.SUCCEEDED.value
                )
                return candidate
            except (KeyError, ValueError):
                continue
        raise ValueError("pr-update-next mode requires an approved pr-update-verify step")

    def _patch_mission_ci_fix_plan_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        plan_action_ids = self._patch_mission_ci_fix_plan_action_ids(mission_record)
        if action_id:
            candidate = str(action_id)
            if candidate not in plan_action_ids:
                raise ValueError("CI fix plan action is not part of this patch mission")
            self._require_patch_mission_action_status(
                candidate, ActionStatus.SUCCEEDED.value
            )
            return candidate
        for candidate in plan_action_ids:
            try:
                self._require_patch_mission_action_status(
                    candidate, ActionStatus.SUCCEEDED.value
                )
                return candidate
            except (KeyError, ValueError):
                continue
        raise ValueError("ci-next-action mode requires an approved ci-fix-plan step")

    def _patch_mission_ci_log_evidence_action_id_if_present(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str | None:
        log_action_ids = self._patch_mission_step_action_ids(
            mission_record, "ci-log-evidence"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in log_action_ids:
                return None
            self._require_patch_mission_action_status(
                candidate, ActionStatus.SUCCEEDED.value
            )
            return candidate
        for candidate in log_action_ids:
            try:
                self._require_patch_mission_action_status(
                    candidate, ActionStatus.SUCCEEDED.value
                )
                return candidate
            except (KeyError, ValueError):
                continue
        return None

    def _patch_mission_ci_log_evidence_status_action_id(
        self, mission_record: dict[str, Any], log_action_id: str
    ) -> str:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            raise ValueError("CI log evidence action is not part of this patch mission")
        for item in followups:
            if (
                isinstance(item, dict)
                and str(item.get("mode", "")).lower() == "ci-log-evidence"
                and str(item.get("action_id", "")) == str(log_action_id)
            ):
                status_id = str(item.get("source_action_id") or "")
                if not status_id:
                    raise ValueError("CI log evidence step has no source PR status action")
                self._require_patch_mission_action_status(
                    status_id, ActionStatus.SUCCEEDED.value
                )
                return status_id
        raise ValueError("CI log evidence action is not part of this patch mission")

    def _patch_mission_ci_log_evidence_output(self, action_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT tool,status,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "inspect_github_ci_logs":
            raise ValueError("source action must be an inspect_github_ci_logs action")
        if row["status"] != ActionStatus.SUCCEEDED.value:
            raise ValueError(f"CI log evidence action has not succeeded: {row['status']}")
        payload = json.loads(row["result_json"])
        result = payload.get("result") if isinstance(payload, dict) else None
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            raise ValueError("CI log evidence result has no output object")
        return output

    def _patch_mission_ci_fix_plan_action_ids(
        self, mission_record: dict[str, Any]
    ) -> list[str]:
        action_ids = self._patch_mission_step_action_ids(mission_record, "ci-fix-plan")
        for action_id in self._patch_mission_step_action_ids(
            mission_record, "pr-update-next"
        ):
            text = self._patch_mission_optional_write_content(action_id)
            if "# Patch Mission CI Fix Plan" in text:
                action_ids.append(action_id)
        return list(dict.fromkeys(action_ids))

    def _patch_mission_latest_succeeded_write_step_action_id(
        self, mission_record: dict[str, Any], mode: str
    ) -> str:
        for action_id in self._patch_mission_step_action_ids(mission_record, mode):
            try:
                self._require_patch_mission_action_status(
                    action_id, ActionStatus.SUCCEEDED.value
                )
                return action_id
            except (KeyError, ValueError):
                continue
        raise ValueError(f"commit-draft requires a succeeded {mode} action")

    def _patch_mission_latest_succeeded_run_step_action_id(
        self, mission_record: dict[str, Any], mode: str
    ) -> str:
        for action_id in self._patch_mission_step_action_ids(mission_record, mode):
            try:
                self._require_patch_mission_action_status(
                    action_id, ActionStatus.SUCCEEDED.value
                )
                return action_id
            except (KeyError, ValueError):
                continue
        raise ValueError(f"branch-draft requires a succeeded {mode} action")

    def _patch_mission_run_command_output(self, action_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT tool,status,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "run_command":
            raise ValueError("source action must be a run_command action")
        if row["status"] != ActionStatus.SUCCEEDED.value:
            raise ValueError(f"run command action has not succeeded: {row['status']}")
        payload = json.loads(row["result_json"])
        result = payload.get("result") if isinstance(payload, dict) else None
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            raise ValueError("run command result has no output object")
        return output

    def _patch_mission_branch_name_from_action(self, action_id: str) -> str:
        row = self.db.query_one(
            "SELECT tool,arguments_json FROM actions WHERE action_id=?", (action_id,)
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "run_command":
            raise ValueError("branch action must be a run_command action")
        arguments = json.loads(row["arguments_json"])
        command = arguments.get("command")
        if (
            not isinstance(command, list)
            or len(command) != 4
            or command[:3] != ["git", "checkout", "-b"]
            or not isinstance(command[3], str)
        ):
            raise ValueError("branch action is not a git checkout -b command")
        return command[3]

    def _patch_mission_pushed_branch_name_from_action(self, action_id: str) -> str:
        row = self.db.query_one(
            "SELECT tool,arguments_json FROM actions WHERE action_id=?", (action_id,)
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "run_command":
            raise ValueError("push action must be a run_command action")
        arguments = json.loads(row["arguments_json"])
        command = arguments.get("command")
        if (
            not isinstance(command, list)
            or len(command) != 5
            or command[:4] != ["git", "push", "-u", "origin"]
            or not isinstance(command[4], str)
        ):
            raise ValueError("push action is not a git push -u origin command")
        return command[4]

    def _patch_mission_remote_live_output(
        self, action_id: str, repo_root: Path
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT tool,status,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "inspect_git_remote_live":
            raise ValueError("source action must be an inspect_git_remote_live action")
        if row["status"] != ActionStatus.SUCCEEDED.value:
            raise ValueError(f"remote live action has not succeeded: {row['status']}")
        payload = json.loads(row["result_json"])
        result = payload.get("result") if isinstance(payload, dict) else None
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            raise ValueError("remote live result has no output object")
        output_path = Path(str(output.get("path", ""))).expanduser().resolve(strict=True)
        if output_path != repo_root:
            raise ValueError("remote live action belongs to a different repo")
        return output

    def _patch_mission_pr_status_output(self, action_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT tool,status,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "inspect_github_pr_status":
            raise ValueError("source action must be an inspect_github_pr_status action")
        if row["status"] != ActionStatus.SUCCEEDED.value:
            raise ValueError(f"PR status action has not succeeded: {row['status']}")
        payload = json.loads(row["result_json"])
        result = payload.get("result") if isinstance(payload, dict) else None
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            raise ValueError("PR status result has no output object")
        return output

    @staticmethod
    def _patch_mission_failed_workflow_run_ids(
        status_output: dict[str, Any]
    ) -> list[int]:
        ids: list[int] = []
        workflow_runs = status_output.get("workflow_runs", {})
        body = workflow_runs.get("body") if isinstance(workflow_runs, dict) else {}
        if isinstance(body, dict):
            for item in body.get("workflow_runs", []) or []:
                if not isinstance(item, dict):
                    continue
                conclusion = str(item.get("conclusion") or "").lower()
                if conclusion not in {
                    "failure",
                    "cancelled",
                    "timed_out",
                    "action_required",
                }:
                    continue
                raw_id = item.get("id")
                if raw_id is None:
                    continue
                try:
                    run_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if run_id > 0:
                    ids.append(run_id)
        for item in status_output.get("failure_summary", []) or []:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("workflow_run_id") or item.get("run_id")
            if raw_id is None:
                continue
            try:
                run_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if run_id > 0:
                ids.append(run_id)
        return list(dict.fromkeys(ids))[:5]

    def _patch_mission_github_repo_from_remote_evidence(
        self, mission_record: dict[str, Any]
    ) -> tuple[str, str, str, str]:
        for mode in ("remote-live-summary", "remote-summary"):
            for evidence_id in self._patch_mission_step_action_ids(
                mission_record, mode
            ):
                try:
                    self._require_patch_mission_action_status(
                        evidence_id, ActionStatus.SUCCEEDED.value
                    )
                    evidence = self._patch_mission_write_file_content(evidence_id)
                except (KeyError, ValueError):
                    continue
                repo = self._patch_mission_github_repo_from_text(evidence)
                if repo is not None:
                    owner, name = repo
                    return owner, name, evidence_id, evidence
        raise ValueError(
            "pr-create-draft requires approved remote evidence containing a GitHub repo URL"
        )

    def _patch_mission_has_github_remote_evidence(
        self, mission_record: dict[str, Any]
    ) -> bool:
        try:
            self._patch_mission_github_repo_from_remote_evidence(mission_record)
            return True
        except ValueError:
            return False

    def _patch_mission_pr_ref_from_create_action(
        self, action_id: str
    ) -> tuple[str, str, int]:
        row = self.db.query_one(
            "SELECT tool,status,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "create_github_pull_request":
            raise ValueError("source action must be a create_github_pull_request action")
        if row["status"] != ActionStatus.SUCCEEDED.value:
            raise ValueError(f"PR creation action has not succeeded: {row['status']}")
        payload = json.loads(row["result_json"])
        result = payload.get("result") if isinstance(payload, dict) else None
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            raise ValueError("PR creation result has no output object")
        owner = str(output.get("owner") or "").strip()
        repo = str(output.get("repo") or "").strip()
        raw_number = output.get("number")
        if not owner or not repo or raw_number is None:
            url = str(output.get("url") or "")
            parsed = self._patch_mission_pr_ref_from_url(url)
            if owner and repo:
                return owner, repo, parsed[2]
            return parsed
        number = int(raw_number)
        if number <= 0:
            raise ValueError("PR creation result has invalid PR number")
        return owner, repo, number

    def _patch_mission_pr_ref_from_status_action(
        self, action_id: str
    ) -> tuple[str, str, int]:
        output = self._patch_mission_pr_status_output(action_id)
        owner = str(output.get("owner") or "").strip()
        repo = str(output.get("repo") or "").strip()
        number = int(output.get("number") or 0)
        if not owner or not repo or number <= 0:
            return self._patch_mission_pr_ref_from_url(str(output.get("url") or ""))
        return owner, repo, number

    def _patch_mission_pr_update_pre_status_id_from_push(
        self, mission_record: dict[str, Any], push_action_id: str
    ) -> str:
        step = self._patch_mission_step_for_action(
            mission_record, push_action_id, "pr-update-push-draft"
        )
        pre_status_id = str(step.get("source_action_id") or "")
        if not pre_status_id:
            raise ValueError("PR update push step has no source PR status action")
        self._require_patch_mission_action_status(
            pre_status_id, ActionStatus.SUCCEEDED.value
        )
        return pre_status_id

    def _patch_mission_pr_update_push_id_from_status(
        self, mission_record: dict[str, Any], status_action_id: str
    ) -> str:
        step = self._patch_mission_step_for_action(
            mission_record, status_action_id, "pr-update-status"
        )
        push_action_id = str(step.get("source_action_id") or "")
        if not push_action_id:
            raise ValueError("PR update status step has no source push action")
        self._require_patch_mission_action_status(
            push_action_id, ActionStatus.SUCCEEDED.value
        )
        return push_action_id

    @staticmethod
    def _patch_mission_step_for_action(
        mission_record: dict[str, Any], action_id: str, mode: str
    ) -> dict[str, Any]:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            raise ValueError(f"patch mission has no {mode} step")
        for item in followups:
            if (
                isinstance(item, dict)
                and str(item.get("mode", "")).lower() == mode
                and str(item.get("action_id", "")) == str(action_id)
            ):
                return dict(item)
        raise ValueError(f"action is not a {mode} step in this patch mission")

    @staticmethod
    def _patch_mission_pr_ref_from_url(url: str) -> tuple[str, str, int]:
        match = re.search(
            r"github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>[0-9]+)",
            str(url).strip(),
        )
        if not match:
            raise ValueError("PR URL must look like https://github.com/owner/repo/pull/123")
        number = int(match.group("number"))
        if number <= 0:
            raise ValueError("PR number must be positive")
        return match.group("owner"), match.group("repo"), number

    @classmethod
    def _patch_mission_ci_fix_plan_next_step(
        cls, plan_text: str
    ) -> tuple[str, str | None]:
        mode_match = re.search(r"^- Mode:\s*`(?P<mode>[^`]+)`", plan_text, re.MULTILINE)
        target_match = re.search(
            r"^- Target:\s*`(?P<target>[^`]+)`", plan_text, re.MULTILINE
        )
        if not mode_match:
            raise ValueError("CI fix plan does not declare a next mode")
        mode = mode_match.group("mode").strip()
        if mode not in {"test", "inspect-file", "git-metadata"}:
            raise ValueError(f"CI fix plan selected unsupported mode: {mode}")
        target = None
        if target_match:
            raw_target = target_match.group("target").strip()
            if raw_target and raw_target != "(none)":
                target = raw_target
        if mode == "inspect-file" and not target:
            raise ValueError("CI fix plan inspect-file step requires a target")
        if mode == "test" and target:
            cls._patch_mission_validate_pytest_target_text(target)
        if mode not in {"inspect-file", "test"}:
            target = None
        return mode, target

    @classmethod
    def _patch_mission_safe_pytest_target(cls, target: str, repo_map: Any) -> str:
        cls._patch_mission_validate_pytest_target_text(target)
        file_part = target.split("::", 1)[0]
        known_files = {str(item.path).replace("\\", "/") for item in repo_map.files}
        if file_part not in known_files:
            raise ValueError("pytest target is not a known repository file")
        return target

    @staticmethod
    def _patch_mission_validate_pytest_target_text(target: str) -> None:
        if not target or len(target) > 240:
            raise ValueError("pytest target is empty or too long")
        if "\\" in target or target.startswith(("/", ".", "-")) or ".." in target:
            raise ValueError("pytest target contains unsupported path syntax")
        if not re.fullmatch(
            r"[A-Za-z0-9_./-]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*){0,3}",
            target,
        ):
            raise ValueError("pytest target contains unsupported characters")

    def _patch_mission_ci_source_context_for_test_action(
        self, mission_record: dict[str, Any], test_action_id: str
    ) -> str:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            return ""
        ci_step = next(
            (
                item
                for item in followups
                if isinstance(item, dict)
                and str(item.get("mode", "")).lower() == "ci-next-action"
                and str(item.get("action_id", "")) == str(test_action_id)
            ),
            None,
        )
        resume_context = None
        direct_confidence_decision = None
        if isinstance(ci_step, dict):
            direct_confidence_decision = self._patch_mission_step_confidence_decision(
                ci_step
            )
        if not isinstance(ci_step, dict):
            test_step = next(
                (
                    item
                    for item in followups
                    if isinstance(item, dict)
                    and str(item.get("mode", "")).lower() == "test"
                    and str(item.get("action_id", "")) == str(test_action_id)
                ),
                None,
            )
            if not isinstance(test_step, dict):
                return ""
            resume_next = test_step.get("resume_next")
            if not isinstance(resume_next, dict):
                return ""
            confidence_decision = resume_next.get("confidence_decision")
            target_decision = resume_next.get("target_decision")
            if not isinstance(confidence_decision, dict):
                return ""
            ci_step = {
                "action_id": test_action_id,
                "source_action_id": confidence_decision.get("source_action_id"),
            }
            resume_context = {
                "from_action_id": resume_next.get("from_action_id"),
                "confidence_decision": confidence_decision,
                "target_decision": target_decision
                if isinstance(target_decision, dict)
                else None,
                "target": resume_next.get("target"),
            }
        fix_plan_action_id = str(ci_step.get("source_action_id") or "")
        if not fix_plan_action_id:
            return f"ci-next-action `{test_action_id}`"
        try:
            plan_text = self._patch_mission_write_file_content(fix_plan_action_id)
        except (KeyError, ValueError, PermissionError):
            return f"ci-next-action `{test_action_id}` from ci-fix-plan `{fix_plan_action_id}`"
        status_action = self._patch_mission_plan_line(plan_text, "PR status action")
        log_action = self._patch_mission_plan_line(plan_text, "CI log evidence action")
        pr_url = self._patch_mission_plan_line(plan_text, "PR URL")
        head_ref = self._patch_mission_plan_line(plan_text, "Head ref")
        head_sha = self._patch_mission_plan_line(plan_text, "Head sha")
        failures = self._patch_mission_plan_bullet(plan_text, "Failure count")
        files = self._patch_mission_plan_bullet(plan_text, "Candidate repo files")
        parts = [
            f"ci-next-action `{test_action_id}`",
            f"ci-fix-plan `{fix_plan_action_id}`",
        ]
        if isinstance(resume_context, dict):
            confidence_decision = resume_context["confidence_decision"]
            target_decision = resume_context.get("target_decision")
            parts[0] = f"resume-next test `{test_action_id}`"
            if resume_context.get("from_action_id"):
                parts.append(
                    f"fallback inspect action `{resume_context.get('from_action_id')}`"
                )
            parts.append(
                "promoted confidence "
                f"`{confidence_decision.get('level')}` via "
                f"`{confidence_decision.get('decision')}`"
            )
            if isinstance(target_decision, dict):
                parts.append(
                    "verification target "
                    f"`{resume_context.get('target')}` via "
                    f"`{target_decision.get('decision')}`"
                )
                if target_decision.get("reason"):
                    parts.append(f"target reason {target_decision.get('reason')}")
        elif isinstance(direct_confidence_decision, dict):
            parts.append(
                "promoted confidence "
                f"`{direct_confidence_decision.get('level')}` via "
                f"`{direct_confidence_decision.get('decision')}`"
            )
            if direct_confidence_decision.get("recovery_count") is not None:
                parts.append(
                    "recovery successes "
                    f"`{direct_confidence_decision.get('recovery_count')}`"
                )
            if (
                direct_confidence_decision.get(
                    "unrecovered_failed_or_rejected_count"
                )
                is not None
            ):
                parts.append(
                    "unrecovered failed_or_rejected "
                    f"`{direct_confidence_decision.get('unrecovered_failed_or_rejected_count')}`"
                )
        if status_action:
            parts.append(f"pr-status `{status_action}`")
        if log_action and log_action != "(not captured)":
            parts.append(f"ci-log-evidence `{log_action}`")
        if pr_url and pr_url != "(not captured)":
            parts.append(f"PR {pr_url}")
        if head_ref and head_ref != "(not captured)":
            parts.append(f"head ref `{head_ref}`")
        if head_sha and head_sha != "(not captured)":
            parts.append(f"head sha `{head_sha}`")
        if failures:
            parts.append(f"failures {failures}")
        if files and files != "(none detected)":
            parts.append(f"files {files}")
        return "; ".join(parts)

    @staticmethod
    def _patch_mission_plan_line(plan_text: str, label: str) -> str:
        match = re.search(
            rf"^{re.escape(label)}:\s*(?P<value>.+)$",
            plan_text,
            re.MULTILINE,
        )
        return match.group("value").strip() if match else ""

    @staticmethod
    def _patch_mission_plan_bullet(plan_text: str, label: str) -> str:
        match = re.search(
            rf"^- {re.escape(label)}:\s*(?P<value>.+)$",
            plan_text,
            re.MULTILINE,
        )
        return match.group("value").strip() if match else ""

    def _patch_mission_write_file_content(self, action_id: str) -> str:
        row = self.db.query_one(
            "SELECT tool,status,arguments_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "write_file":
            raise ValueError("source action must be a write_file action")
        if row["status"] != ActionStatus.SUCCEEDED.value:
            raise ValueError(f"write_file action has not succeeded: {row['status']}")
        arguments = json.loads(row["arguments_json"])
        path = Path(str(arguments.get("path", ""))).expanduser().resolve(strict=False)
        if not self.policy._contained(path, self.config.outbox_path):
            raise PermissionError("write_file evidence must come from the WLS outbox")
        return str(arguments.get("content") or "")

    @staticmethod
    def _patch_mission_github_repo_from_text(text: str) -> tuple[str, str] | None:
        match = re.search(
            r"github\.com[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)(?:\.git)?",
            text,
        )
        if not match:
            return None
        return match.group("owner"), match.group("repo").removesuffix(".git")

    @staticmethod
    def _patch_mission_default_base_branch(remote_evidence: str) -> str:
        default = re.search(r'"default_branch"\s*:\s*"(?P<branch>[^"]+)"', remote_evidence)
        if default:
            branch = default.group("branch").strip()
            if re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
                return branch
        if "refs/heads/master" in remote_evidence or "origin/master" in remote_evidence:
            return "master"
        return "main"

    @staticmethod
    def _patch_mission_pr_create_payload(
        *,
        mission_record: dict[str, Any],
        summary_text: str,
        push_action_id: str,
        branch_name: str,
        remote_evidence_id: str,
    ) -> tuple[str, str]:
        title_match = re.search(r"^Title:\s*(?P<title>.+)$", summary_text, re.MULTILINE)
        if title_match:
            title = title_match.group("title").strip()
        else:
            title = str(mission_record.get("mission") or "Patch Mission update").strip()
        title = title[:256].rstrip() or "Patch Mission update"
        body = "\n".join(
            [
                summary_text.strip(),
                "",
                "## WLS Push Evidence",
                f"- Mission ID: {mission_record['mission_id']}",
                f"- Push action: {push_action_id}",
                f"- Pushed branch: {branch_name}",
                f"- Remote evidence action: {remote_evidence_id}",
                "",
                "## Authority",
                "- This PR creation action requires exact owner approval and GITHUB_TOKEN.",
                "- This action creates a draft PR only; it does not merge, approve, or alter branch protections.",
            ]
        ).strip()
        while len(body.encode("utf-8")) > 65536:
            body = body[: int(len(body) * 0.9)].rstrip()
        return title, body

    @staticmethod
    def _patch_mission_branch_name(
        *, mission_record: dict[str, Any], remote_summary: str
    ) -> str:
        mission = str(mission_record.get("mission") or "patch-mission").lower()
        slug = re.sub(r"[^a-z0-9]+", "-", mission).strip("-")
        if not slug:
            slug = "patch-mission"
        mission_id = str(mission_record.get("mission_id") or "")
        suffix = re.sub(r"[^a-z0-9]+", "", mission_id.lower())[-8:] or "local"
        branch = f"wls/{slug[:48].strip('-')}-{suffix}"
        if "origin/" in remote_summary and not branch.startswith("wls/"):
            branch = f"wls/{branch}"
        return branch[:96].rstrip("-")

    @staticmethod
    def _patch_mission_changed_files_from_checklist(prep_text: str) -> list[str]:
        match = re.search(
            r"## Changed Files\s+- (?P<files>.+?)\n\n",
            prep_text,
            flags=re.DOTALL,
        )
        if not match:
            return []
        raw = match.group("files").strip()
        if raw == "(none detected)":
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    @staticmethod
    def _patch_mission_commit_message_from_evidence(
        *,
        mission_record: dict[str, Any],
        prep_text: str,
        summary_text: str,
        changed_files: list[str],
    ) -> tuple[str, str]:
        suggested = re.search(
            r"## Suggested Commit Message\s+```text\s*(?P<message>.+?)```",
            prep_text,
            flags=re.DOTALL,
        )
        if suggested:
            lines = [line.rstrip() for line in suggested.group("message").splitlines()]
            subject = next((line for line in lines if line.strip()), "")
        else:
            subject = str(mission_record.get("mission") or "Patch mission").strip()
        subject = subject[:72].rstrip() or "Patch mission update"
        summary_title = re.search(r"^Title:\s*(?P<title>.+)$", summary_text, re.MULTILINE)
        body_lines = [
            "Patch Mission local commit.",
            f"Mission ID: {mission_record['mission_id']}",
            f"Changed files: {', '.join(changed_files[:10])}",
        ]
        if summary_title:
            body_lines.append(f"PR summary title: {summary_title.group('title').strip()}")
        body_lines.append("No push or pull request was created by this action.")
        return subject, "\n".join(body_lines)

    def _patch_mission_git_metadata_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        metadata_action_ids = self._patch_mission_step_action_ids(
            mission_record, "git-metadata"
        )
        if action_id:
            candidate = str(action_id)
            if candidate not in metadata_action_ids:
                raise ValueError("git metadata action is not part of this patch mission")
            return candidate
        if not metadata_action_ids:
            raise ValueError("git-prep mode requires a prior git-metadata step")
        return metadata_action_ids[0]

    def _patch_mission_git_metadata_output(
        self, action_id: str, repo_root: Path
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT tool,status,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "inspect_git_worktree":
            raise ValueError("source action must be an inspect_git_worktree action")
        if row["status"] != ActionStatus.SUCCEEDED.value:
            raise ValueError(f"git metadata action has not succeeded: {row['status']}")
        payload = json.loads(row["result_json"])
        result = payload.get("result") if isinstance(payload, dict) else None
        output = result.get("output") if isinstance(result, dict) else None
        if not isinstance(output, dict):
            raise ValueError("git metadata action result has no output object")
        output_path = Path(str(output.get("path", ""))).expanduser().resolve(strict=True)
        if output_path != repo_root:
            raise ValueError("git metadata action belongs to a different repo")
        for key in ("branch_status", "diff_stat", "diff_names"):
            section = output.get(key)
            if not isinstance(section, dict):
                raise ValueError(f"git metadata missing {key}")
            if int(section.get("returncode", -1)) != 0:
                raise ValueError(f"git metadata command failed: {key}")
        return output

    @staticmethod
    def _patch_mission_git_config_digest(repo_root: Path) -> str:
        config_path = repo_root / ".git" / "config"
        if not config_path.exists() or not config_path.is_file():
            return "missing"
        try:
            data = config_path.read_bytes()
        except OSError:
            return "unreadable"
        return hashlib.sha256(data).hexdigest()

    def _patch_mission_remote_summary_text(
        self,
        *,
        mission_record: dict[str, Any],
        metadata_action_id: str,
        metadata: dict[str, Any],
    ) -> str:
        remotes = self._metadata_stdout(metadata, "remotes")
        branches = self._metadata_stdout(metadata, "branches")
        last_commit = self._metadata_stdout(metadata, "last_commit")
        branch_status = self._metadata_stdout(metadata, "branch_status")
        diff_names = self._metadata_stdout(metadata, "diff_names")
        return "\n".join(
            [
                "# Patch Mission Remote Readiness Summary",
                "",
                f"Mission ID: {mission_record['mission_id']}",
                f"Mission: {mission_record.get('mission', '')}",
                f"Git metadata action: {metadata_action_id}",
                "",
                "## Remotes",
                "```text",
                remotes or "(no remotes configured)",
                "```",
                "",
                "## Branches",
                "```text",
                branches or "(no branch metadata captured)",
                "```",
                "",
                "## Last Commit",
                "```text",
                last_commit or "(no commit metadata captured)",
                "```",
                "",
                "## Working Tree",
                "```text",
                branch_status or "(no status output captured)",
                "```",
                "",
                "## Changed Files Not Yet Reflected Remotely",
                "```text",
                diff_names or "(none detected)",
                "```",
                "",
                "## Owner Checklist",
                "- Confirm the local commit is correct before any branch or push action.",
                "- Confirm the target remote and branch manually.",
                "- Approve future branch, push, or PR actions only as exact separate actions.",
                "",
                "## Authority",
                "- This summary uses local read-only git metadata only.",
                "- No fetch, branch creation, commit, push, or pull request is created by this step.",
            ]
        )

    def _patch_mission_remote_live_summary_text(
        self,
        *,
        mission_record: dict[str, Any],
        live_action_id: str,
        live_output: dict[str, Any],
    ) -> str:
        remote_url = self._metadata_stdout(live_output, "remote_url")
        remote_head = self._metadata_stdout(live_output, "remote_head")
        remote_heads = self._metadata_stdout(live_output, "remote_heads")
        remote_tags = self._metadata_stdout(live_output, "remote_tags")
        github = live_output.get("github", {})
        github_text = json.dumps(github, ensure_ascii=False, sort_keys=True, indent=2)
        return "\n".join(
            [
                "# Patch Mission Live Remote Inspection",
                "",
                f"Mission ID: {mission_record['mission_id']}",
                f"Mission: {mission_record.get('mission', '')}",
                f"Remote live action: {live_action_id}",
                "",
                "## Remote URL",
                "```text",
                remote_url or "(no remote URL captured)",
                "```",
                "",
                "## Remote HEAD",
                "```text",
                remote_head or "(no remote HEAD captured)",
                "```",
                "",
                "## Remote Branch Refs",
                "```text",
                remote_heads or "(no remote branches captured)",
                "```",
                "",
                "## Remote Tags",
                "```text",
                remote_tags or "(no remote tags captured)",
                "```",
                "",
                "## Public GitHub Metadata",
                "```json",
                github_text,
                "```",
                "",
                "## Owner Checklist",
                "- Confirm the prepared local branch should be pushed.",
                "- Confirm live remote branches do not conflict with the intended branch name.",
                "- Approve any future push or PR creation only as exact separate actions.",
                "",
                "## Authority",
                "- This summary is read-only remote/GitHub inspection evidence.",
                "- No fetch, branch creation, commit, push, or pull request is created by this step.",
            ]
        )

    def _patch_mission_ci_fix_plan_text(
        self,
        *,
        mission_record: dict[str, Any],
        repo_root: Path,
        repo_map: Any,
        status_action_id: str,
        status_output: dict[str, Any],
        log_action_id: str | None = None,
        log_output: dict[str, Any] | None = None,
    ) -> str:
        failure_summary = status_output.get("failure_summary", [])
        failure_text = json.dumps(
            failure_summary,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        log_evidence_text = (
            json.dumps(log_output, ensure_ascii=False, sort_keys=True, indent=2)
            if isinstance(log_output, dict)
            else ""
        )
        combined_failure_text = "\n".join(
            part for part in (failure_text, log_evidence_text) if part
        )
        candidate_files = self._patch_mission_failure_file_candidates(
            combined_failure_text, repo_map
        )
        pytest_target = self._patch_mission_pytest_target_from_failure_text(
            combined_failure_text, candidate_files
        )
        prior_learning = self._patch_mission_failure_learning_context(
            mission_record=mission_record,
            query_text=combined_failure_text,
            selected_file=candidate_files[0] if candidate_files else None,
            pytest_target=pytest_target,
        )
        repair_candidates = self._patch_mission_repair_skill_candidate_context()
        approved_repair_skills = self._patch_mission_approved_repair_skill_context(
            mission_record=mission_record,
            query_text=combined_failure_text,
            selected_file=candidate_files[0] if candidate_files else None,
            pytest_target=pytest_target,
        )
        next_mode, next_target, next_reason = self._patch_mission_ci_next_step(
            failure_text=combined_failure_text,
            candidate_files=candidate_files,
        )
        original_next_mode = next_mode
        original_next_target = next_target
        original_next_reason = next_reason
        next_mode, next_target, next_reason = (
            self._patch_mission_confidence_adjusted_ci_next_step(
                next_mode=next_mode,
                next_target=next_target,
                next_reason=next_reason,
                candidate_files=candidate_files,
                approved_repair_skills=approved_repair_skills,
            )
        )
        next_reason = self._patch_mission_approved_repair_skill_rationale(
            approved_repair_skills,
            default_reason=next_reason,
            action_phrase=f"the `{next_mode}` next-step rationale",
        )
        confidence_action_note = self._patch_mission_promoted_confidence_action_note(
            original_mode=original_next_mode,
            selected_mode=next_mode,
            approved_repair_skills=approved_repair_skills,
        )
        command = "wls patch-mission-step --mode " + next_mode
        if next_target:
            command += f" --target {next_target}"
        pr_url = str(status_output.get("url") or "").strip()
        head_sha = str(status_output.get("head_sha") or "").strip()
        head_ref = str(status_output.get("head_ref") or "").strip()
        failures = failure_summary if isinstance(failure_summary, list) else []
        failure_excerpt = failure_text[:4000] if failure_text else "[]"
        log_excerpt = (
            log_evidence_text[:4000] if log_evidence_text else "(not captured)"
        )
        files = ", ".join(candidate_files) if candidate_files else "(none detected)"
        return "\n".join(
            [
                "# Patch Mission CI Fix Plan",
                "",
                f"Mission ID: {mission_record['mission_id']}",
                f"Mission: {mission_record.get('mission', '')}",
                f"Repo: {repo_root}",
                f"PR status action: {status_action_id}",
                f"CI log evidence action: {log_action_id or '(not captured)'}",
                f"PR URL: {pr_url or '(not captured)'}",
                f"Head ref: {head_ref or '(not captured)'}",
                f"Head sha: {head_sha or '(not captured)'}",
                "",
                "## Failure Clues",
                f"- Failure count: {len(failures)}",
                f"- Candidate repo files: {files}",
                "",
                "## Suggested Next Local Step",
                f"- Mode: `{next_mode}`",
                f"- Target: `{next_target or '(none)'}`",
                f"- Reason: {next_reason}",
                f"- Original CI-selected mode: `{original_next_mode}`",
                f"- Original CI-selected target: `{original_next_target or '(none)'}`",
                f"- Original CI-selected reason: {original_next_reason}",
                f"- Promoted confidence action adjustment: {confidence_action_note}",
                "```text",
                command,
                "```",
                "",
                "## CI Failure Evidence",
                "```json",
                failure_excerpt,
                "```",
                "",
                "## CI Log Evidence",
                "```json",
                log_excerpt,
                "```",
                "",
                "## Prior Failure-Learning Memory",
                *self._patch_mission_failure_learning_lines(prior_learning),
                "",
                "## Reviewed Repair Skill Candidate",
                *self._patch_mission_repair_skill_candidate_lines(repair_candidates),
                "",
                "## Approved Repair Skill Advisory Context",
                *self._patch_mission_approved_repair_skill_lines(
                    approved_repair_skills
                ),
                "",
                "## Authority",
                "- This plan is written to the WLS outbox only.",
                "- It consumes approved read-only GitHub PR/CI status and log evidence.",
                "- Prior learning, reviewed candidates, and approved skills are advisory only and do not bypass owner approval.",
                "- It does not modify the repo, push, comment, review, merge, or change GitHub state.",
                "- The next repair action must be created as a separate Patch Mission step.",
            ]
        )

    def _patch_mission_pr_update_verify_text(
        self,
        *,
        mission_record: dict[str, Any],
        pre_status_id: str,
        pre_output: dict[str, Any],
        push_action_id: str,
        post_status_id: str,
        post_output: dict[str, Any],
    ) -> str:
        pre_failures = pre_output.get("failure_summary", [])
        post_failures = post_output.get("failure_summary", [])
        if not isinstance(pre_failures, list):
            pre_failures = []
        if not isinstance(post_failures, list):
            post_failures = []
        pre_sha = str(pre_output.get("head_sha") or "").strip()
        post_sha = str(post_output.get("head_sha") or "").strip()
        changed = bool(pre_sha and post_sha and pre_sha != post_sha)
        pre_json = json.dumps(pre_failures[:10], ensure_ascii=False, sort_keys=True, indent=2)
        post_json = json.dumps(post_failures[:10], ensure_ascii=False, sort_keys=True, indent=2)
        return "\n".join(
            [
                "# Patch Mission PR Update Verification",
                "",
                f"Mission ID: {mission_record['mission_id']}",
                f"Mission: {mission_record.get('mission', '')}",
                f"PR URL: {post_output.get('url') or pre_output.get('url') or '(not captured)'}",
                f"Pre-update PR status action: {pre_status_id}",
                f"Update push action: {push_action_id}",
                f"Post-update PR status action: {post_status_id}",
                "",
                "## Head SHA",
                f"- Before: {pre_sha or '(not captured)'}",
                f"- After: {post_sha or '(not captured)'}",
                f"- Changed: {changed}",
                "",
                "## Failure Summary Counts",
                f"- Before: {len(pre_failures)}",
                f"- After: {len(post_failures)}",
                "",
                "## Pre-update Failure Summary",
                "```json",
                pre_json,
                "```",
                "",
                "## Post-update Failure Summary",
                "```json",
                post_json,
                "```",
                "",
                "## Suggested Next Local Step",
                "- If failures remain, run `patch-mission-step --mode ci-fix-plan --action-id <post_update_pr_status_action>`.",
                "- If checks are pending, run another read-only `pr-status` later instead of writing to GitHub.",
                "",
                "## Authority",
                "- This verification note is written to the WLS outbox only.",
                "- It compares approved read-only PR/CI status evidence before and after an approved branch update push.",
                "- It does not comment, review, merge, force push, or change GitHub state.",
            ]
        )

    @staticmethod
    def _patch_mission_pr_update_verify_after_failure_count(verify_text: str) -> int:
        match = re.search(
            r"## Failure Summary Counts\s+- Before:\s*[0-9]+\s+- After:\s*(?P<count>[0-9]+)",
            verify_text,
            re.DOTALL,
        )
        if not match:
            raise ValueError("PR update verification does not include post-update failure count")
        return int(match.group("count"))

    def _patch_mission_pr_update_next_text(
        self,
        *,
        mission_record: dict[str, Any],
        verify_action_id: str,
        verify_text: str,
        post_failures: int,
    ) -> str:
        pr_url = self._patch_mission_plan_line(verify_text, "PR URL")
        post_status = self._patch_mission_plan_line(
            verify_text, "Post-update PR status action"
        )
        return "\n".join(
            [
                "# Patch Mission PR Update Next Step",
                "",
                f"Mission ID: {mission_record['mission_id']}",
                f"Mission: {mission_record.get('mission', '')}",
                f"Verification action: {verify_action_id}",
                f"Post-update PR status action: {post_status or '(not captured)'}",
                f"PR URL: {pr_url or '(not captured)'}",
                "",
                "## Decision",
                f"- Post-update failure count: {post_failures}",
                "- No immediate failing check summary was captured in the post-update status evidence.",
                "- If checks are still pending, run another owner-approved read-only status check later.",
                "- If all checks are green, the Patch Mission can be treated as externally updated and awaiting owner review.",
                "",
                "## Authority",
                "- This note is written to the WLS outbox only.",
                "- It does not comment, review, merge, force push, or change GitHub state.",
                "- Any future GitHub write remains a separate owner-approved action.",
            ]
        )

    @staticmethod
    def _patch_mission_failure_file_candidates(
        failure_text: str, repo_map: Any
    ) -> list[str]:
        candidates: list[str] = []
        pattern = re.compile(
            r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:py|js|jsx|ts|tsx|json|toml|yaml|yml|md|txt|cfg|ini))(?:[:#][0-9]+)?"
        )
        known_files = {
            str(getattr(item, "path", "")).replace("\\", "/")
            for item in getattr(repo_map, "files", [])
            if getattr(item, "path", "")
        }
        for match in pattern.finditer(failure_text):
            raw = match.group("path").replace("\\", "/").strip("/")
            if not raw:
                continue
            if raw in known_files:
                candidates.append(raw)
                continue
            suffix_matches = [
                path for path in known_files if path.endswith("/" + raw) or path.endswith(raw)
            ]
            candidates.extend(suffix_matches[:3])
        return list(dict.fromkeys(candidates))[:10]

    @staticmethod
    def _patch_mission_ci_next_step(
        *, failure_text: str, candidate_files: list[str]
    ) -> tuple[str, str | None, str]:
        lowered = failure_text.lower()
        pytest_target = LivingSystem._patch_mission_pytest_target_from_failure_text(
            failure_text, candidate_files
        )
        if pytest_target:
            return (
                "test",
                pytest_target,
                f"CI log names pytest node `{pytest_target}`; reproduce that narrow failure locally first.",
            )
        test_file = next(
            (
                path
                for path in candidate_files
                if path.startswith("tests/")
                or "/tests/" in path
                or Path(path).name.startswith("test_")
            ),
            None,
        )
        if test_file:
            return (
                "test",
                test_file,
                f"CI names `{test_file}`; reproduce the failing suite locally before changing code.",
            )
        if candidate_files:
            return (
                "inspect-file",
                candidate_files[0],
                f"CI points at `{candidate_files[0]}`; inspect that local file before drafting a patch.",
            )
        if any(token in lowered for token in ("pytest", "test", "failed", "failure")):
            return (
                "test",
                None,
                "CI reports a test failure but no repo file was matched; run the local test probe first.",
            )
        return (
            "git-metadata",
            None,
            "CI evidence did not expose a file or test clue; refresh local git state before choosing a repair action.",
        )

    @classmethod
    def _patch_mission_pytest_target_from_failure_text(
        cls, failure_text: str, candidate_files: list[str]
    ) -> str | None:
        known_test_files = [
            path
            for path in candidate_files
            if path.startswith("tests/")
            or "/tests/" in path
            or Path(path).name.startswith("test_")
        ]
        patterns = [
            r"(?P<target>[A-Za-z0-9_./-]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*){1,3})",
            r"FAILED\s+(?P<target>[A-Za-z0-9_./-]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*){0,3})",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, failure_text):
                target = match.group("target")
                try:
                    cls._patch_mission_validate_pytest_target_text(target)
                except ValueError:
                    continue
                file_part = target.split("::", 1)[0]
                if file_part in known_test_files or not known_test_files:
                    return target
        return None

    @staticmethod
    def _metadata_stdout(metadata: dict[str, Any], key: str) -> str:
        section = metadata.get(key)
        if not isinstance(section, dict):
            return ""
        return str(section.get("stdout") or "").strip()

    @staticmethod
    def _patch_mission_git_changed_files(metadata: dict[str, Any]) -> list[str]:
        names = str(metadata["diff_names"].get("stdout") or "")
        files = [line.strip() for line in names.splitlines() if line.strip()]
        if files:
            return files
        status = str(metadata["branch_status"].get("stdout") or "")
        parsed: list[str] = []
        for line in status.splitlines():
            if not line or line.startswith("##"):
                continue
            path = line[3:].strip() if len(line) > 3 else line.strip()
            if " -> " in path:
                path = path.rsplit(" -> ", 1)[1]
            if path:
                parsed.append(path)
        return list(dict.fromkeys(parsed))

    def _patch_mission_git_prep_text(
        self,
        *,
        mission_record: dict[str, Any],
        metadata_action_id: str,
        metadata: dict[str, Any],
        changed_files: list[str],
    ) -> str:
        branch_status = str(metadata["branch_status"].get("stdout") or "").strip()
        diff_stat = str(metadata["diff_stat"].get("stdout") or "").strip()
        changed = ", ".join(changed_files) if changed_files else "(none detected)"
        message = self._patch_mission_commit_message(mission_record, changed_files)
        return "\n".join(
            [
                "# Patch Mission Commit-Ready Checklist",
                "",
                f"Mission ID: {mission_record['mission_id']}",
                f"Mission: {mission_record.get('mission', '')}",
                f"Git metadata action: {metadata_action_id}",
                "",
                "## Local Git State",
                "```text",
                branch_status or "(no git status output)",
                "```",
                "",
                "## Diff Stat",
                "```text",
                diff_stat or "(no diff stat output)",
                "```",
                "",
                "## Changed Files",
                f"- {changed}",
                "",
                "## Suggested Commit Message",
                "```text",
                message,
                "```",
                "",
                "## Owner Checklist",
                "- Review the outbox PR summary and this git metadata.",
                "- Confirm changed files match the intended Patch Mission diff.",
                "- Confirm tests passed after the applied patch.",
                "- Approve a future exact local commit action only if the checklist is correct.",
                "",
                "## Authority",
                "- This file is written to the WLS outbox only.",
                "- No commit, branch creation, push, or pull request is created by this step.",
            ]
        )

    @staticmethod
    def _patch_mission_commit_message(
        mission_record: dict[str, Any], changed_files: list[str]
    ) -> str:
        mission = str(mission_record.get("mission") or "Patch mission").strip()
        if len(mission) > 72:
            mission = mission[:69].rstrip() + "..."
        if changed_files:
            return f"{mission}\n\nChanged files: {', '.join(changed_files[:10])}"
        return mission

    def _patch_mission_passing_test_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        test_action_ids = self._patch_mission_test_step_action_ids(mission_record)
        if action_id:
            candidate = str(action_id)
            if candidate not in test_action_ids:
                raise ValueError("passing test action is not part of this patch mission")
            return candidate
        for candidate in test_action_ids:
            try:
                output = self._patch_mission_action_result_output(candidate)
            except (KeyError, ValueError):
                continue
            if int(output.get("returncode", -1)) == 0:
                return candidate
        raise ValueError("PR summary requires a passing test action")

    def _patch_mission_failing_test_action_id(
        self, mission_record: dict[str, Any], passing_test_id: str
    ) -> str:
        for candidate in self._patch_mission_test_step_action_ids(mission_record):
            if candidate == passing_test_id:
                continue
            try:
                output = self._patch_mission_action_result_output(candidate)
            except (KeyError, ValueError):
                continue
            if int(output.get("returncode", 0)) != 0:
                return candidate
        raise ValueError("PR summary requires earlier failing test evidence")

    def _patch_mission_test_step_action_ids(
        self, mission_record: dict[str, Any]
    ) -> list[str]:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            return []
        ids: list[str] = []
        for item in followups:
            if not isinstance(item, dict) or not item.get("action_id"):
                continue
            mode = str(item.get("mode", "")).lower()
            if mode == "test":
                ids.append(str(item["action_id"]))
            elif mode == "ci-next-action":
                action_id = str(item["action_id"])
                if self._patch_mission_action_is_pytest(action_id):
                    ids.append(action_id)
        return ids

    def _patch_mission_action_is_pytest(self, action_id: str) -> bool:
        row = self.db.query_one(
            "SELECT tool,arguments_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None or row["tool"] != "run_command":
            return False
        try:
            arguments = json.loads(row["arguments_json"])
        except json.JSONDecodeError:
            return False
        command = arguments.get("command")
        return isinstance(command, list) and command[:3] == ["python", "-m", "pytest"]

    def _patch_mission_step_action_ids(
        self, mission_record: dict[str, Any], mode: str
    ) -> list[str]:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            return []
        return [
            str(item.get("action_id"))
            for item in followups
            if isinstance(item, dict)
            and str(item.get("mode", "")).lower() == mode
            and item.get("action_id")
        ]

    def _patch_mission_latest_step(
        self, mission_record: dict[str, Any], mode: str
    ) -> dict[str, Any]:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            raise ValueError(f"patch mission has no {mode} step")
        for item in followups:
            if isinstance(item, dict) and str(item.get("mode", "")).lower() == mode:
                return dict(item)
        raise ValueError(f"patch mission has no {mode} step")

    def _patch_mission_latest_succeeded_action_id(
        self, mission_record: dict[str, Any], mode: str
    ) -> str | None:
        for action_id in self._patch_mission_step_action_ids(mission_record, mode):
            try:
                self._require_patch_mission_action_status(
                    action_id, ActionStatus.SUCCEEDED.value
                )
                return action_id
            except (KeyError, ValueError):
                continue
        return None

    def _patch_mission_latest_succeeded_action_id_after(
        self,
        mission_record: dict[str, Any],
        mode: str,
        *,
        after_action_id: str,
    ) -> str | None:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            return None
        for item in followups:
            if not isinstance(item, dict) or not item.get("action_id"):
                continue
            candidate = str(item["action_id"])
            if candidate == after_action_id:
                return None
            if str(item.get("mode", "")).lower() != mode:
                continue
            try:
                self._require_patch_mission_action_status(
                    candidate, ActionStatus.SUCCEEDED.value
                )
                return candidate
            except (KeyError, ValueError):
                continue
        return None

    def _patch_mission_latest_failed_pr_status_action_id(
        self, mission_record: dict[str, Any]
    ) -> str | None:
        for action_id in self._patch_mission_step_action_ids(
            mission_record, "pr-status"
        ):
            try:
                self._require_patch_mission_action_status(
                    action_id, ActionStatus.SUCCEEDED.value
                )
                output = self._patch_mission_pr_status_output(action_id)
            except (KeyError, ValueError):
                continue
            failures = output.get("failure_summary", [])
            if isinstance(failures, list) and failures:
                return action_id
        return None

    def _require_patch_mission_action_status(
        self, action_id: str, expected_status: str
    ) -> None:
        row = self.db.query_one(
            "SELECT status FROM actions WHERE action_id=?", (action_id,)
        )
        if row is None:
            raise KeyError(action_id)
        if row["status"] != expected_status:
            raise ValueError(
                f"patch mission action {action_id} is {row['status']}, expected {expected_status}"
            )

    @staticmethod
    def _patch_mission_diff_changed_files(diff_text: str) -> list[str]:
        files: list[str] = []
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                files.append(line.removeprefix("+++ b/").strip())
        return list(dict.fromkeys(files))

    @staticmethod
    def _patch_mission_pr_title(
        mission_record: dict[str, Any], changed_files: list[str]
    ) -> str:
        mission = str(mission_record.get("mission") or "Patch Mission").strip()
        if len(mission) > 80:
            mission = mission[:77].rstrip() + "..."
        if changed_files:
            return f"{mission} ({', '.join(changed_files[:3])})"
        return mission

    def _patch_mission_pr_summary_text(
        self,
        *,
        mission_record: dict[str, Any],
        title: str,
        changed_files: list[str],
        diff_text: str,
        failing_test_id: str,
        failing_output: dict[str, Any],
        passing_test_id: str,
        passing_output: dict[str, Any],
        apply_action_id: str,
        draft_action_id: str,
        ci_source: str,
        repair_learning: dict[str, Any] | None = None,
    ) -> str:
        failing_excerpt = self._bounded_test_excerpt(
            "\n".join(
                part
                for part in (
                    str(failing_output.get("stdout") or ""),
                    str(failing_output.get("stderr") or ""),
                )
                if part
            ),
            limit=1600,
        )
        passing_excerpt = self._bounded_test_excerpt(
            "\n".join(
                part
                for part in (
                    str(passing_output.get("stdout") or ""),
                    str(passing_output.get("stderr") or ""),
                )
                if part
            ),
            limit=1200,
        )
        changed = ", ".join(changed_files) if changed_files else "(unknown)"
        skill_candidate = (
            repair_learning.get("skill_candidate")
            if isinstance(repair_learning, dict)
            else None
        )
        skill_candidate_lines: list[str] = []
        if isinstance(skill_candidate, dict) and skill_candidate.get("available"):
            status = "created" if skill_candidate.get("created") else "already proposed"
            skill_candidate_lines = [
                "",
                "## Skill Candidate",
                f"- Candidate: `{skill_candidate.get('candidate_id')}` ({status}).",
                f"- Repeat count: `{skill_candidate.get('repeat_count')}` successful literal-mismatch repairs.",
                "- Status: proposed candidate only; owner review is required before any sandbox, validation, approval, promotion, or use as an active skill.",
            ]
        return "\n".join(
            [
                "# Patch Mission PR Summary",
                "",
                f"Title: {title}",
                f"Mission ID: {mission_record['mission_id']}",
                f"Mission: {mission_record.get('mission', '')}",
                "",
                "## Summary",
                f"- Changed files: {changed}",
                "- Applied the owner-reviewed Patch Mission diff to the local canonical repo.",
                "- Generated this summary from persisted WLS action evidence.",
                "",
                "## Verification",
                f"- Failing baseline: action `{failing_test_id}` returned `{failing_output.get('returncode')}`.",
                f"- Applied patch: action `{apply_action_id}` succeeded from draft `{draft_action_id}`.",
                f"- Passing rerun: action `{passing_test_id}` returned `{passing_output.get('returncode')}`.",
                f"- PR/CI source: {ci_source or '(not a CI-triggered local test action)'}",
                *skill_candidate_lines,
                "",
                "## Diff",
                "```diff",
                diff_text.strip(),
                "```",
                "",
                "## Failing Test Evidence",
                "```text",
                failing_excerpt or "(no failing test output captured)",
                "```",
                "",
                "## Passing Test Evidence",
                "```text",
                passing_excerpt or "(no passing test output captured)",
                "```",
                "",
                "## Authority",
                "- This file is written to the WLS outbox only.",
                "- No branch, commit, push, or pull request is created by this step.",
                "- External GitHub actions remain unavailable until the owner explicitly approves them.",
            ]
        )

    def _patch_mission_draft_action_id(
        self, mission_record: dict[str, Any], action_id: str | None
    ) -> str:
        followups = mission_record.get("followups", [])
        if not isinstance(followups, list):
            followups = []
        draft_action_ids = [
            str(item.get("action_id"))
            for item in followups
            if isinstance(item, dict)
            and str(item.get("mode", "")).lower()
            in {"from-test-result", "draft-patch"}
            and item.get("action_id")
        ]
        if action_id:
            candidate = str(action_id)
            if candidate not in draft_action_ids:
                raise ValueError("patch draft action is not part of this patch mission")
            return candidate
        if not draft_action_ids:
            raise ValueError("apply-patch mode requires a prior patch draft step")
        return draft_action_ids[0]

    def _patch_mission_outbox_draft_text(self, action_id: str) -> str:
        row = self.db.query_one(
            "SELECT tool,status,arguments_json,result_json FROM actions WHERE action_id=?",
            (action_id,),
        )
        if row is None:
            raise KeyError(action_id)
        if row["tool"] != "write_file":
            raise ValueError("source action must be an outbox write_file draft action")
        if row["status"] != ActionStatus.SUCCEEDED.value:
            raise ValueError(
                f"patch draft action must be approved and resumed first: {row['status']}"
            )
        arguments = json.loads(row["arguments_json"])
        draft_path = Path(str(arguments["path"])).expanduser().resolve(strict=True)
        if not self.policy._contained(draft_path, self.config.outbox_path):
            raise PermissionError("patch draft must come from the WLS outbox")
        return self._read_patch_mission_text(draft_path, max_bytes=2 * 1024 * 1024)

    @staticmethod
    def _extract_unified_diff_from_patch_mission_draft(draft_text: str) -> str:
        fenced = re.search(
            r"```diff\s*(?P<diff>---\s+a/.+?)```",
            draft_text,
            flags=re.DOTALL,
        )
        if fenced:
            return fenced.group("diff").strip() + "\n"
        marker = draft_text.find("--- a/")
        if marker >= 0:
            return draft_text[marker:].strip() + "\n"
        raise ValueError("patch draft does not contain a unified diff")

    def _apply_single_file_unified_diff(
        self, *, repo_root: Path, diff_text: str
    ) -> tuple[str, str]:
        lines = diff_text.splitlines()
        old_headers = [line for line in lines if line.startswith("--- ")]
        new_headers = [line for line in lines if line.startswith("+++ ")]
        if len(old_headers) != 1 or len(new_headers) != 1:
            raise ValueError("apply-patch supports exactly one file diff")
        target = new_headers[0].removeprefix("+++ ").strip()
        if not target.startswith("b/") or target == "b/dev/null":
            raise ValueError("patch target must be a repo file")
        relative = target.removeprefix("b/").replace("\\", "/")
        target_path = self._patch_mission_repo_file(repo_root, relative)
        original = self._read_patch_mission_text(
            target_path, max_bytes=2 * 1024 * 1024
        )
        original_lines = original.splitlines(keepends=True)
        revised_lines: list[str] = []
        original_index = 0
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.startswith("@@ "):
                index += 1
                continue
            match = re.match(r"@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@", line)
            if not match:
                raise ValueError(f"invalid unified diff hunk header: {line}")
            hunk_start = int(match.group("old_start")) - 1
            if hunk_start < original_index:
                raise ValueError("overlapping unified diff hunks are not supported")
            revised_lines.extend(original_lines[original_index:hunk_start])
            original_index = hunk_start
            index += 1
            while index < len(lines) and not lines[index].startswith("@@ "):
                hunk_line = lines[index]
                if hunk_line.startswith(" "):
                    expected = hunk_line[1:]
                    self._verify_patch_source_line(original_lines, original_index, expected)
                    revised_lines.append(original_lines[original_index])
                    original_index += 1
                elif hunk_line.startswith("-"):
                    expected = hunk_line[1:]
                    self._verify_patch_source_line(original_lines, original_index, expected)
                    original_index += 1
                elif hunk_line.startswith("+"):
                    revised_lines.append(self._with_original_newline(hunk_line[1:], original_lines, original_index))
                elif hunk_line.startswith("\\"):
                    pass
                elif hunk_line.startswith("--- ") or hunk_line.startswith("+++ "):
                    raise ValueError("nested file header inside hunk")
                else:
                    raise ValueError(f"unsupported unified diff line: {hunk_line}")
                index += 1
        revised_lines.extend(original_lines[original_index:])
        if not revised_lines:
            raise ValueError("unified diff produced an empty result")
        return relative, "".join(revised_lines)

    @staticmethod
    def _verify_patch_source_line(
        original_lines: list[str], original_index: int, expected: str
    ) -> None:
        if original_index >= len(original_lines):
            raise ValueError("unified diff reads past end of file")
        if original_lines[original_index].rstrip("\r\n") != expected:
            raise ValueError("unified diff context does not match current file")

    @staticmethod
    def _with_original_newline(
        text: str, original_lines: list[str], original_index: int
    ) -> str:
        if original_index < len(original_lines):
            source = original_lines[original_index]
            if source.endswith("\r\n"):
                return text + "\r\n"
            if source.endswith("\n"):
                return text + "\n"
        return text + "\n"

    def _patch_mission_failed_file(
        self, *, repo_root: Path, repo_map: Any, output_text: str
    ) -> str:
        candidates: list[str] = []
        patterns = [
            r"FAILED\s+([^\s:]+\.py)(?:::|\s|:)",
            r"([A-Za-z0-9_./\\-]+\.py):\d+:",
            r"([A-Za-z0-9_./\\-]+\.py)::[A-Za-z0-9_]+",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, output_text):
                candidates.append(match.group(1))
        candidates.extend(self._patch_mission_test_hints(repo_map))
        candidates.append(self._patch_mission_default_target(repo_map))
        for candidate in candidates:
            normalized = str(candidate).replace("\\", "/").lstrip("./")
            if normalized.startswith(str(repo_root).replace("\\", "/")):
                try:
                    normalized = str(
                        Path(normalized).resolve(strict=True).relative_to(repo_root)
                    ).replace("\\", "/")
                except (OSError, ValueError):
                    continue
            try:
                path = self._patch_mission_repo_file(repo_root, normalized)
            except (OSError, ValueError, PermissionError):
                continue
            return str(path.relative_to(repo_root)).replace("\\", "/")
        raise ValueError("could not identify a repo file from test output")

    @staticmethod
    def _bounded_test_excerpt(output_text: str, limit: int = 4000) -> str:
        text = output_text.strip()
        if len(text) <= limit:
            return text
        head = text[: limit // 2].rstrip()
        tail = text[-limit // 2 :].lstrip()
        return f"{head}\n\n... [truncated] ...\n\n{tail}"

    def _patch_mission_repo_file(self, repo_root: Path, target: str) -> Path:
        target_path = (repo_root / target).resolve(strict=True)
        if not target_path.is_file():
            raise ValueError(f"target is not a file: {target}")
        if not self.policy._contained(target_path, repo_root):
            raise PermissionError("patch mission target must stay inside repo root")
        return target_path

    @staticmethod
    def _patch_mission_default_target(repo_map: Any) -> str:
        for item in repo_map.files:
            normalized = str(item.path).replace("\\", "/").lower()
            if normalized.startswith("tests/"):
                return str(item.path)
        for item in repo_map.files:
            if str(item.path).lower().endswith((".py", ".js", ".ts", ".md")):
                return str(item.path)
        raise ValueError("repository has no supported target file to inspect")

    def _allow_explicit_patch_mission_read_root(self, repo_root: Path) -> None:
        policy = self.config.tool_policy
        roots = [
            Path(item).expanduser().resolve(strict=False)
            for item in policy.get("allowed_read_roots", [])
        ]
        if any(self.policy._contained(repo_root, root) for root in roots):
            return
        allowed = list(policy.get("allowed_read_roots", []))
        allowed.append(str(repo_root))
        policy["allowed_read_roots"] = allowed
        persisted = self.db.get_runtime("patch_mission_read_roots", [])
        if not isinstance(persisted, list):
            persisted = []
        updated = list(dict.fromkeys([str(repo_root), *[str(item) for item in persisted]]))
        self.db.set_runtime("patch_mission_read_roots", updated[:100])

    def _restore_patch_mission_read_roots(self) -> None:
        persisted = self.db.get_runtime("patch_mission_read_roots", [])
        if not isinstance(persisted, list):
            return
        policy = self.config.tool_policy
        allowed = list(policy.get("allowed_read_roots", []))
        roots = [Path(item).expanduser().resolve(strict=False) for item in allowed]
        for item in persisted:
            try:
                path = Path(str(item)).expanduser().resolve(strict=False)
            except OSError:
                continue
            if not any(self.policy._contained(path, root) for root in roots):
                allowed.append(str(path))
                roots.append(path)
        policy["allowed_read_roots"] = allowed

    def _allow_explicit_patch_mission_write_root(self, repo_root: Path) -> None:
        policy = self.config.tool_policy
        roots = [
            Path(item).expanduser().resolve(strict=False)
            for item in policy.get("allowed_write_roots", [])
        ]
        if any(self.policy._contained(repo_root, root) for root in roots):
            return
        allowed = list(policy.get("allowed_write_roots", []))
        allowed.append(str(repo_root))
        policy["allowed_write_roots"] = allowed
        persisted = self.db.get_runtime("patch_mission_write_roots", [])
        if not isinstance(persisted, list):
            persisted = []
        updated = list(
            dict.fromkeys([str(repo_root), *[str(item) for item in persisted]])
        )
        self.db.set_runtime("patch_mission_write_roots", updated[:100])

    def _restore_patch_mission_write_roots(self) -> None:
        persisted = self.db.get_runtime("patch_mission_write_roots", [])
        if not isinstance(persisted, list):
            return
        policy = self.config.tool_policy
        allowed = list(policy.get("allowed_write_roots", []))
        roots = [Path(item).expanduser().resolve(strict=False) for item in allowed]
        for item in persisted:
            try:
                path = Path(str(item)).expanduser().resolve(strict=False)
            except OSError:
                continue
            if not any(self.policy._contained(path, root) for root in roots):
                allowed.append(str(path))
                roots.append(path)
        policy["allowed_write_roots"] = allowed

    @staticmethod
    def _patch_mission_test_hints(repo_map: Any) -> list[str]:
        hints: list[str] = []
        paths = {str(item.path) for item in repo_map.files}
        for marker in ("pyproject.toml", "pytest.ini", "tox.ini", "package.json"):
            if marker in paths:
                hints.append(marker)
        for path in sorted(paths):
            normalized = path.replace("\\", "/").lower()
            if normalized.startswith("tests/") or "/tests/" in normalized:
                hints.append(path)
                if len(hints) >= 10:
                    break
        return list(dict.fromkeys(hints))[:10]

    @staticmethod
    def _patch_mission_repo_state_digest(repo_root: Path, repo_map: Any) -> str:
        files: list[dict[str, Any]] = []
        total_bytes = 0
        for item in sorted(getattr(repo_map, "files", []), key=lambda file: str(file.path))[
            :500
        ]:
            relative = str(item.path).replace("\\", "/")
            path = (repo_root / relative).resolve(strict=False)
            try:
                stat = path.stat()
            except OSError:
                continue
            digest = ""
            if stat.st_size <= 1024 * 1024 and total_bytes < 5 * 1024 * 1024:
                try:
                    data = path.read_bytes()
                    total_bytes += len(data)
                    digest = hashlib.sha256(data).hexdigest()
                except OSError:
                    digest = ""
            files.append(
                {
                    "path": relative,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": digest,
                }
            )
        return digest_json({"files": files})

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

    def external_product_audit_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("external_product_audit_receipts", [])
        if not isinstance(receipts, list):
            return []
        return [dict(item) for item in receipts[:limit] if isinstance(item, dict)]

    def m7_self_check_audit_receipts(
        self, limit: int = 20
    ) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("m7_self_check_audit_receipts", [])
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
        destination = sqlite3.connect(str(backup_db))
        try:
            destination.execute("PRAGMA journal_mode=WAL")
            source.backup(destination)
        finally:
            destination.close()
            self.db._untrack_and_close(source)
        backup_seconds = round(time.monotonic() - backup_started, 4)
        shutil.copy2(backup_db, restore_check_db)
        restore_connection = sqlite3.connect(str(restore_check_db))
        try:
            restore_connection.execute("PRAGMA journal_mode=WAL")
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
        return self._find_doc("COMMERCIAL_READINESS_GATES.md")

    def record_external_product_audit(
        self,
        *,
        reason: str,
        wheel_path: str | Path | None = None,
        exclude_multi_user: bool = True,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("external product audit reason is required")
        health = self.health_snapshot()
        commercial = health.get("commercial_readiness")
        upgrade = health.get("upgrade_drill")
        retention = health.get("retention_audit")
        performance = health.get("performance_budget")

        docs = {
            "commercial_readiness_gates": self._find_doc(
                "COMMERCIAL_READINESS_GATES.md"
            ),
            "external_delivery_runbook": self._find_doc(
                "EXTERNAL_SINGLE_USER_DELIVERY.md"
            ),
            "support_runbook": self._find_doc("SUPPORT_AND_DIAGNOSTICS.md"),
            "security_boundary": self._find_doc("SECURITY_BOUNDARY.md"),
            "recovery_runbook": self._find_doc("RECOVERY_RUNBOOK.md"),
        }
        wheel = self._resolve_external_product_wheel(wheel_path)
        wheel_evidence = self._wheel_evidence(wheel)
        wheel_version_matches_runtime = (
            isinstance(wheel_evidence, dict)
            and wheel_evidence.get("version") == __version__
        )

        def status_is(item: Any, allowed: set[str]) -> bool:
            return isinstance(item, dict) and str(item.get("status")) in allowed

        gates = {
            "rc_passed": status_is(commercial, {"RC_PASSED"}),
            "health_ok": health.get("status") == "OK",
            "wheel_present": wheel is not None,
            "wheel_version_matches_runtime": wheel_version_matches_runtime,
            "performance_budget_passed": status_is(
                performance, {"PERFORMANCE_BUDGET_PASSED", "PASSED"}
            ),
            "retention_audit_passed": status_is(
                retention, {"RETENTION_AUDIT_PASSED", "PASSED"}
            ),
            "rollback_drill_passed": status_is(
                upgrade, {"UPGRADE_DRILL_PASSED", "PASSED"}
            ),
            "disposable_clone_rollback_verified": (
                isinstance(upgrade, dict)
                and upgrade.get("disposable_clone_executed") is True
                and (
                    upgrade.get("disposable_clone_rollback", {}).get("passed")
                    is True
                )
            ),
            "docs_complete": all(path is not None for path in docs.values()),
            "support_diagnostics_documented": docs["support_runbook"] is not None,
            "security_boundary_documented": docs["security_boundary"] is not None,
            "recovery_documented": docs["recovery_runbook"] is not None,
            "multi_user_scope_excluded": bool(exclude_multi_user),
        }
        missing = [key for key, passed in gates.items() if not passed]
        receipt = {
            "receipt_type": "EXTERNAL_SINGLE_USER_PRODUCT_AUDIT",
            "audit_id": new_id("external_product_audit"),
            "status": "EXTERNAL_SINGLE_USER_READY" if not missing else "EXTERNAL_SINGLE_USER_BLOCKED",
            "reason": reason,
            "scope": {
                "external_user_delivery": True,
                "single_user_only": True,
                "multi_user_or_regional_tenant_module": "EXCLUDED_BY_OWNER"
                if exclude_multi_user
                else "REQUIRED_AND_NOT_IMPLEMENTED",
            },
            "gates": gates,
            "missing": missing,
            "evidence": {
                "health_status": health.get("status"),
                "commercial_readiness_id": commercial.get("audit_id")
                if isinstance(commercial, dict)
                else None,
                "performance_budget_id": performance.get("audit_id")
                if isinstance(performance, dict)
                else None,
                "retention_audit_id": retention.get("audit_id")
                if isinstance(retention, dict)
                else None,
                "upgrade_drill_id": upgrade.get("drill_id")
                if isinstance(upgrade, dict)
                else None,
                "wheel": wheel_evidence,
                "docs": {key: str(path) if path else None for key, path in docs.items()},
            },
            "live_install_modified": False,
            "cleanup_executed": False,
            "claim_ceiling": (
                "external single-user delivery audit only; multi-user, regional "
                "tenanting, managed support operations, and third-party compliance "
                "certification remain explicitly out of scope"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.external_product_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "external_product_audit_receipts", updated, connection
            )
            self.db.set_runtime("external_product_audit_last", receipt, connection)
            self.ledger.append("external_product_audit_recorded", receipt, connection)
        return receipt

    def record_m7_self_check_audit(
        self,
        *,
        reason: str,
        m7_root: str | Path | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("M7 self-check reason is required")
        health = self.health_snapshot()
        integrity = self.verify_integrity(full=True)
        m7_reference = self._m7_reference_evidence(m7_root)
        commercial = health.get("commercial_readiness")
        external = health.get("external_product")
        longitudinal = health.get("longitudinal_report")
        upgrade = health.get("upgrade_drill")
        performance = health.get("performance_budget")
        retention = health.get("retention_audit")
        garbage = health.get("garbage_audit")
        wheel = (
            external.get("evidence", {}).get("wheel")
            if isinstance(external, dict)
            else None
        )
        docs = (
            external.get("evidence", {}).get("docs")
            if isinstance(external, dict)
            else None
        )
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

        def status_is(item: Any, allowed: set[str]) -> bool:
            return isinstance(item, dict) and str(item.get("status")) in allowed

        source_evidence = {
            "permission_boundary_tests": self._source_contains(
                {
                    "source/tests/test_agentic_harness.py": [
                        "test_high_risk_node_waits_for_approval_without_lease",
                        "test_worker_registry_rejects_unknown_and_overrisk_workers",
                        "policy approval missing",
                    ],
                    "source/tests/test_garbage_audit.py": [
                        "test_garbage_cleanup_requires_approval_reference",
                        "test_cli_garbage_clear_quarantine_requires_approval_reference",
                    ],
                    "source/tests/test_keyfiles.py": [
                        "test_approval_key_round_trips_binary_bytes_across_restart"
                    ],
                }
            ),
            "risk_gate_code": self._source_contains(
                {
                    "source/src/wls/task_admission.py": [
                        "owner_gate",
                        "RiskLevel.IRREVERSIBLE",
                    ],
                    "source/src/wls/task_classifier.py": [
                        "effective_risk",
                        "SideEffectClass.IRREVERSIBLE",
                    ],
                    "source/src/wls/runtime.py": [
                        "requires_approval",
                        "UNKNOWN_SIDE_EFFECT",
                    ],
                }
            ),
            "conflict_lock_tests": self._source_contains(
                {
                    "source/tests/test_life_campaign_30.py": [
                        "test_campaign_lock_is_exclusive"
                    ],
                    "source/src/wls/db.py": ["idx_actions_idempotency_success"],
                    "source/src/wls/runtime.py": ["idempotency_key"],
                }
            ),
            "stop_freeze_code": self._source_contains(
                {
                    "source/src/wls/runtime.py": [
                        "def pause",
                        "def kill",
                        "def freeze_holdout_epoch",
                    ],
                    "source/tests/test_life_campaign_30.py": [
                        "PAUSED",
                        "KILLED",
                    ],
                    "source/tests/test_living_agent_os_capabilities.py": [
                        "freeze_holdout_epoch"
                    ],
                }
            ),
            "diagnostic_confidence_code": self._source_contains(
                {
                    "source/src/wls/merge_node.py": ["UNRESOLVED"],
                    "source/src/wls/reviewer.py": ["confidence"],
                    "source/src/wls/cognition.py": ["minimum_confidence"],
                }
            ),
            "supply_chain_license": self._source_contains(
                {
                    "pyproject.toml": [
                        'license = "MIT"',
                        "dependencies = []",
                        "wls-ui =",
                    ]
                }
            ),
        }

        rollback_verified = (
            isinstance(upgrade, dict)
            and upgrade.get("status") == "UPGRADE_DRILL_PASSED"
            and upgrade.get("disposable_clone_executed") is True
            and upgrade.get("disposable_clone_rollback", {}).get("passed") is True
        )
        external_ready = status_is(external, {"EXTERNAL_SINGLE_USER_READY"})
        commercial_ready = status_is(commercial, {"RC_PASSED"})
        longitudinal_ready = status_is(longitudinal, {"LONGITUDINAL_REPORT_PASSED"})
        evidence_chain_ok = bool(integrity.get("ok"))
        docs_complete = isinstance(docs, dict) and all(docs.values())
        wheel_matches = isinstance(wheel, dict) and wheel.get(
            "version_matches_runtime"
        ) is True

        hard_gates = {
            "m7_reference_found": bool(m7_reference["found"]),
            "rollback_capability_verified": rollback_verified,
            "risk_gate_present": source_evidence["risk_gate_code"],
            "real_use_evidence": longitudinal_ready
            and qualified_measurement_count >= 8
            and len(task_classes) >= 4,
            "evidence_chain_verified": evidence_chain_ok,
            "permission_boundary_verified": source_evidence[
                "permission_boundary_tests"
            ],
            "stop_freeze_rule_present": source_evidence["stop_freeze_code"],
            "external_single_user_ready": external_ready,
            "commercial_rc_passed": commercial_ready,
            "docs_complete": docs_complete,
            "wheel_version_matches_runtime": wheel_matches,
        }
        missing = [key for key, passed in hard_gates.items() if not passed]
        soft_scores = {
            "real_friction": 3 if qualified_measurement_count >= 8 else 1,
            "stable_entry": 3 if health.get("status") == "OK" else 0,
            "object_boundary": 3 if docs_complete else 1,
            "permission_matrix": 3
            if source_evidence["permission_boundary_tests"]
            else 0,
            "path_file_security": 3 if docs_complete else 1,
            "state_machine": 3 if int(health.get("cycle_count", 0)) >= 1 else 1,
            "concurrent_safety_U1": 3
            if source_evidence["conflict_lock_tests"]
            else 0,
            "task_ledger": 3 if qualified_measurement_count >= 8 else 1,
            "hash_chain_evidence_U2": 3 if evidence_chain_ok else 0,
            "risk_gate": 3 if source_evidence["risk_gate_code"] else 0,
            "audit_log": 3 if evidence_chain_ok else 0,
            "runtime_metrics": 3
            if status_is(performance, {"PERFORMANCE_BUDGET_PASSED", "PASSED"})
            else 0,
            "auto_diagnostic": 2
            if source_evidence["diagnostic_confidence_code"]
            else 0,
            "diagnostic_confidence_U4": 3
            if source_evidence["diagnostic_confidence_code"]
            else 0,
            "route_planner": 2 if commercial_ready else 0,
            "stop_freeze": 3 if source_evidence["stop_freeze_code"] else 0,
            "rollback_capability": 3 if rollback_verified else 0,
            "code_quality": 3 if status_is(commercial, {"RC_PASSED"}) else 0,
            "supply_chain_license": 3
            if source_evidence["supply_chain_license"] and wheel_matches
            else 0,
            "economic_gate": 3
            if status_is(retention, {"RETENTION_AUDIT_PASSED", "PASSED"})
            and status_is(garbage, {"CLEAN", "GARBAGE_AUDIT_CLEAN", "PASSED"})
            else 0,
            "meta_governance_U5": 2 if m7_reference["found"] else 0,
        }
        soft_score_total = sum(soft_scores.values())
        receipt = {
            "receipt_type": "M7_SELF_CHECK_AUDIT",
            "audit_id": new_id("m7_self_check_audit"),
            "status": "M7_PERSONAL_SINGLE_USER_READY" if not missing else "M7_BLOCKED",
            "reason": reason,
            "profile": {
                "m7_profile": "personal_single_user",
                "excluded_profile": "multiparty_enterprise",
                "exclusion_reason": (
                    "multi-user tenanting and third-party certification are not "
                    "part of this single-user external launch scope"
                ),
            },
            "hard_gate_result": {
                "gates": hard_gates,
                "missing": missing,
                "final_ceiling": "M7" if not missing else "M5_OR_BELOW",
            },
            "soft_scores": soft_scores,
            "soft_score_total": soft_score_total,
            "final_level": "M7_PERSONAL_CANDIDATE" if not missing else "BELOW_M7",
            "primary_failure_layer": "NONE" if not missing else "M7_HARD_GATE",
            "candidate_failure_layers": missing,
            "confidence": 0.92 if not missing else 0.68,
            "resolution_status": "located" if missing else "passed",
            "risk_level": "LOW" if not missing else "HIGH",
            "next_one_action": (
                "freeze single-user launch scope and ship with evidence bundle"
                if not missing
                else "repair the first missing M7 hard gate and rerun m7-self-check"
            ),
            "forbidden_actions": [
                "claim multi-user or third-party-certified readiness from this receipt",
                "publish without preserving this audit receipt and rollback evidence",
            ],
            "required_test": "m7-self-check plus installed self-check must pass",
            "stop_condition": "stop if any hard gate regresses or evidence chain fails",
            "rollback_plan": "use the latest upgrade-drill backup and disposable rollback procedure",
            "re_evaluation_point": "rerun after any release, permission, risk, or packaging change",
            "evidence": {
                "m7_reference": m7_reference,
                "health_status": health.get("status"),
                "integrity_ok": evidence_chain_ok,
                "commercial_readiness_id": commercial.get("audit_id")
                if isinstance(commercial, dict)
                else None,
                "external_product_audit_id": external.get("audit_id")
                if isinstance(external, dict)
                else None,
                "longitudinal_report_id": longitudinal.get("report_id")
                if isinstance(longitudinal, dict)
                else None,
                "qualified_measurement_count": qualified_measurement_count,
                "task_classes": task_classes,
                "upgrade_drill_id": upgrade.get("drill_id")
                if isinstance(upgrade, dict)
                else None,
                "source_evidence": source_evidence,
                "wheel": wheel,
                "docs": docs,
            },
            "live_install_modified": False,
            "cleanup_executed": False,
            "claim_ceiling": (
                "M7 Personal single-user self-check only; this receipt does not "
                "claim multi-party SaaS readiness, ISO certification, or externally "
                "audited compliance"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.m7_self_check_audit_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("m7_self_check_audit_receipts", updated, connection)
            self.db.set_runtime("m7_self_check_audit_last", receipt, connection)
            self.ledger.append("m7_self_check_audit_recorded", receipt, connection)
        return receipt

    def _m7_reference_evidence(
        self, m7_root: str | Path | None = None
    ) -> dict[str, Any]:
        candidates: list[Path] = []
        if m7_root is not None and str(m7_root).strip():
            candidates.append(Path(m7_root).expanduser())
        else:
            candidates.append(Path.home() / "Desktop" / "M7瀹舵棌")
            candidates.append(Path.home() / "OneDrive" / "Desktop" / "M7瀹舵棌")
        found = next((path.resolve() for path in candidates if path.is_dir()), None)
        required = [
            "M7_Runtime_Gate_v2.0_optimized.md",
            "M7_Runtime_Gate_Master_Library_v1.2_absorbed.md",
            "m7-runtime-gate-template/07_evolution/m7_gate_result.yaml",
            "m7-runtime-gate-template/05_risk/risk_gate.yaml",
            "m7-runtime-gate-template/03_authority/permission_matrix.yaml",
            "m7-runtime-gate-template/tests/hash_chain_tests.md",
            "m7-runtime-gate-template/tests/conflict_lock_tests.md",
            "m7-runtime-gate-template/tests/permission_tests.md",
        ]
        present: dict[str, str | None] = {}
        if found is not None:
            for relative in required:
                path = found / relative
                present[relative] = str(path) if path.is_file() else None
        return {
            "found": found is not None,
            "root": str(found) if found is not None else None,
            "required_files": present,
            "required_files_complete": bool(found)
            and all(value is not None for value in present.values()),
        }

    def _source_contains(self, requirements: dict[str, list[str]]) -> bool:
        for relative, patterns in requirements.items():
            if not self._source_file_contains(relative, patterns):
                return False
        return True

    def _source_file_contains(self, relative: str, patterns: list[str]) -> bool:
        for root in self._external_evidence_roots():
            path = root / relative
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if all(pattern in text for pattern in patterns):
                return True
        return False

    def _find_doc(self, name: str) -> Path | None:
        candidates: list[Path] = []
        for root in self._external_evidence_roots():
            candidates.extend([root / "docs" / name, root / name])
        for path in candidates:
            if path.is_file():
                return path
        return None

    def _resolve_external_product_wheel(
        self, wheel_path: str | Path | None
    ) -> Path | None:
        candidates: list[Path] = []
        if wheel_path is not None and str(wheel_path).strip():
            candidates.append(Path(wheel_path).expanduser())
        for root in self._external_evidence_roots():
            candidates.extend(root.glob("dist/workstation_living_system-*.whl"))
        for receipt in self.upgrade_drill_receipts(limit=20):
            wheel = receipt.get("wheel")
            if isinstance(wheel, dict) and str(wheel.get("path", "")).strip():
                candidates.append(Path(str(wheel["path"])).expanduser())
        for receipt in self._install_receipts():
            for key in ("wheel", "source_root"):
                value = str(receipt.get(key, "")).strip()
                if not value:
                    continue
                path = Path(value).expanduser()
                if key == "wheel":
                    candidates.append(path)
                else:
                    candidates.extend(
                        path.glob("dist/workstation_living_system-*.whl")
                    )
        existing = [path.resolve() for path in candidates if path.is_file()]
        if not existing:
            return None
        return max(existing, key=lambda path: path.stat().st_mtime)

    def _wheel_evidence(self, wheel: Path | None) -> dict[str, Any] | None:
        if wheel is None:
            return None
        version = self._wheel_version(wheel)
        return {
            "path": str(wheel),
            "bytes": wheel.stat().st_size,
            "sha256": self._file_sha256(wheel),
            "version": version,
            "runtime_version": __version__,
            "version_matches_runtime": version == __version__,
        }

    def _external_evidence_roots(self) -> list[Path]:
        roots: list[Path] = []
        for receipt in self._install_receipts():
            for key in ("install_root", "source_root"):
                value = str(receipt.get(key, "")).strip()
                if value:
                    roots.append(Path(value).expanduser())
        roots.extend([Path.cwd(), self.config.home_path])
        roots.extend(self.config.home_path.parents)
        module_path = Path(__file__).resolve()
        roots.extend(module_path.parents)
        resolved: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            try:
                normalized = root.resolve()
            except OSError:
                normalized = root.absolute()
            key = str(normalized).lower() if os.name == "nt" else str(normalized)
            if key not in seen:
                seen.add(key)
                resolved.append(normalized)
        return resolved

    def _install_receipts(self) -> list[dict[str, Any]]:
        receipts: list[dict[str, Any]] = []
        search_roots = [self.config.home_path, *self.config.home_path.parents]
        module_path = Path(__file__).resolve()
        search_roots.extend(module_path.parents)
        seen: set[Path] = set()
        for root in search_roots:
            for path in root.glob("INSTALL_RECEIPT*.json"):
                try:
                    resolved = path.resolve()
                except OSError:
                    resolved = path.absolute()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    receipts.append(payload)
        return receipts

    @staticmethod
    def _wheel_version(wheel: Path) -> str | None:
        name = wheel.name
        prefix = "workstation_living_system-"
        if not name.startswith(prefix) or not name.endswith(".whl"):
            return None
        remainder = name[len(prefix) : -4]
        version, separator, _tags = remainder.partition("-")
        if not separator:
            return None
        return version or None

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
        action_candidate: dict[str, Any] | None = None
        try:
            recovery_outcomes = self._resume_durable_actions()
            finish_phase("durable_action_recovery")
            sensor_summary, prediction_errors = self._poll_due_sensors()
            finish_phase("sensor_polling")
            daily_perception = self._refresh_daily_perception_summary()
            finish_phase("daily_perception")
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
            # The existing LearningSystem owns candidate identity; the existing
            # AutonomySystem chooses whether a candidate warrants a read-only
            # endogenous goal. Never invoke a model or a repair worker here.
            newly_observed_growth = self.learning.create_failure_candidates(
                minimum_repeats=3, lookback_days=30, max_new_candidates=1
            )
            event_goal_timings.append(
                {
                    "step": "real_failure_growth_discovery",
                    "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    "new_candidates": len(newly_observed_growth),
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
            growth_rehearsals = self.growth.advance_safe_endogenous_growth(
                autonomous_goal_ids
            )
            event_goal_timings.append(
                {
                    "step": "bounded_autonomous_growth_rehearsal",
                    "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    "attempted": len(growth_rehearsals),
                }
            )
            step_started = time.monotonic()
            active_goals = self.goals.active(limit=20)
            goal_pressure = self._goal_pressure_summary(
                goals=active_goals,
                daily_perception=daily_perception,
            )
            pressure_order = {
                item["goal_id"]: index
                for index, item in enumerate(goal_pressure["ranked_goals"])
            }
            active_goals.sort(
                key=lambda goal: pressure_order.get(goal.goal_id, len(active_goals))
            )
            event_goal_timings.append(
                {
                    "step": "active_goals_goal_pressure",
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
                "goal_pressure": goal_pressure,
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
                step_started = time.monotonic()
                memory_influence = self._record_memory_influence_proof(
                    cycle_id=cycle_id,
                    plan=plan,
                    memory_retrieval=memory_retrieval,
                    goal_pressure=goal_pressure,
                )
                planning_timings.append(
                    {
                        "step": "memory_influence_proof",
                        "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    }
                )
                plan.actions = plan.actions[: int(budget["max_actions"])]
                step_started = time.monotonic()
                action_candidate = self._record_action_candidate(
                    cycle_id=cycle_id,
                    plan=plan,
                    goal_pressure=goal_pressure,
                    daily_perception=daily_perception,
                    memory_influence=memory_influence,
                )
                if action_candidate.get("suppressed"):
                    plan.actions = []
                planning_timings.append(
                    {
                        "step": "bounded_action_candidate",
                        "elapsed_seconds": round(time.monotonic() - step_started, 4),
                    }
                )
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
                memory_influence = None
                plan = Plan(
                    rationale="Idle cycle: no external change, active goal, recovery, or prediction error.",
                    actions=[],
                )
                action_candidate = self._record_action_candidate(
                    cycle_id=cycle_id,
                    plan=plan,
                    goal_pressure=goal_pressure,
                    daily_perception=daily_perception,
                    memory_influence=memory_influence,
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
                "daily_perception": daily_perception,
                "goal_pressure": goal_pressure,
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
                "memory_influence": memory_influence,
                "action_candidate": action_candidate,
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

    def _refresh_daily_perception_summary(self) -> dict[str, Any]:
        summary = self._daily_perception_summary()
        self.db.set_runtime("daily_perception", summary)
        return summary

    def _goal_pressure_summary(
        self,
        *,
        goals: list[Goal] | list[dict[str, Any]],
        daily_perception: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return self.goal_pressure.rank(
            goals,
            daily_perception=daily_perception
            if isinstance(daily_perception, dict)
            else self._life_state_daily_perception(limit),
            recent_actions=self._recent_goal_actions(limit=100),
            limit=limit,
        )

    def _recent_goal_actions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT action_id,plan_id,goal_id,tool,purpose,risk,status,finished_at,error
            FROM actions
            WHERE goal_id IS NOT NULL
            ORDER BY COALESCE(finished_at, started_at, '') DESC, rowid DESC
            LIMIT ?
            """,
            (max(1, min(500, int(limit))),),
        )
        return [
            {
                "action_id": row["action_id"],
                "plan_id": row["plan_id"],
                "goal_id": row["goal_id"],
                "tool": row["tool"],
                "purpose": row["purpose"],
                "risk": row["risk"],
                "status": row["status"],
                "finished_at": row["finished_at"],
                "error": row["error"],
            }
            for row in rows
        ]

    def _record_memory_influence_proof(
        self,
        *,
        cycle_id: str,
        plan: Plan,
        memory_retrieval: dict[str, Any],
        goal_pressure: dict[str, Any],
    ) -> dict[str, Any]:
        proof = self.memory_influence.analyze_plan(
            plan=plan,
            memories=[
                dict(item)
                for item in memory_retrieval.get("selected", [])
                if isinstance(item, dict)
            ],
            goal_pressure=goal_pressure,
            limit=self.config.memory_retrieval_limit,
        )
        proof = {
            **proof,
            "cycle_id": cycle_id,
            "plan_id": plan.plan_id,
            "selected_plan_memory_ids": list(dict.fromkeys(plan.memory_ids)),
            "suppressed_memory_ids": [
                str(item.get("memory_id"))
                for item in memory_retrieval.get("suppressed", [])
                if isinstance(item, dict) and item.get("memory_id")
            ],
        }
        self.db.set_runtime("last_memory_influence", proof)
        return proof

    def _record_action_candidate(
        self,
        *,
        cycle_id: str,
        plan: Plan,
        goal_pressure: dict[str, Any],
        daily_perception: dict[str, Any],
        memory_influence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        candidate = self.action_candidates.build(
            plan=plan,
            goal_pressure=goal_pressure,
            daily_perception=daily_perception,
            memory_influence=memory_influence,
            outcome_learning=self._outcome_learning_summary(limit=20),
            tool_side_effects=self._action_tool_side_effects(plan),
            policy_decisions=self._action_policy_decisions(plan),
        )
        candidate = self._apply_self_model_candidate_readiness(candidate)
        candidate = {
            **candidate,
            "cycle_id": cycle_id,
            "plan_id": plan.plan_id,
        }
        self.db.set_runtime("last_action_candidate", candidate)
        return candidate

    def _action_tool_side_effects(self, plan: Plan) -> dict[str, str]:
        side_effects: dict[str, str] = {}
        for action in plan.actions:
            if action.tool in side_effects:
                continue
            try:
                side_effects[action.tool] = self.tools.get(action.tool).side_effect_class
            except KeyError:
                side_effects[action.tool] = "unknown"
        return side_effects

    def _action_policy_decisions(self, plan: Plan) -> dict[str, dict[str, Any]]:
        decisions: dict[str, dict[str, Any]] = {}
        for action in plan.actions:
            decision = self.policy.decide(action, approval_valid=False)
            decisions[action.action_id] = {
                "allowed": decision.allowed,
                "requires_approval": decision.requires_approval,
                "reason": decision.reason,
                "classified_risk": self.policy.classify(action).value,
            }
        return decisions

    def _apply_self_model_candidate_readiness(
        self, candidate: dict[str, Any]
    ) -> dict[str, Any]:
        if (
            not candidate.get("available")
            or candidate.get("suppressed")
            or not candidate.get("tool")
        ):
            return candidate
        readiness = self.self_model.readiness_for_action(candidate)
        candidate = {**candidate, "self_model_readiness": readiness}
        if readiness.get("attempt_allowed") is not False:
            return candidate
        return {
            **candidate,
            "available": False,
            "candidate_type": "self_model_deferred_action",
            "status": "DEFERRED_BY_SELF_MODEL",
            "suppressed": True,
            "requires_owner_approval": False,
            "approval_required": False,
            "executes_now": False,
            "reason": readiness.get("reason", "self_model_deferred"),
            "defer_to_owner": True,
        }

    def owner_outcome_feedback(self, limit: int = 50) -> list[dict[str, Any]]:
        feedback = self.db.get_runtime("owner_outcome_feedback", [])
        if not isinstance(feedback, list):
            return []
        return [dict(item) for item in feedback[: max(0, int(limit))] if isinstance(item, dict)]

    def record_owner_outcome_feedback(
        self,
        *,
        outcome: str,
        action_id: str | None = None,
        owner_note: str = "",
        evidence: dict[str, Any] | None = None,
        goal_progress_delta: float = 0.0,
    ) -> dict[str, Any]:
        evidence = evidence or {}
        if goal_progress_delta and not evidence:
            raise ValueError("goal progress feedback requires evidence")
        target = self._owner_feedback_target(action_id)
        record = self.outcome_learning.feedback_record(
            target=target,
            outcome=outcome,
            owner_note=owner_note,
            evidence=evidence,
            goal_progress_delta=goal_progress_delta,
        )
        if goal_progress_delta:
            goal_id = str(record["action"].get("goal_id") or "")
            if not goal_id:
                raise ValueError("goal progress feedback requires a goal-linked action")
            row = self.db.query_one(
                "SELECT progress FROM goals WHERE goal_id=?", (goal_id,)
            )
            if row is None:
                raise KeyError(f"unknown goal: {goal_id}")
            before = float(row["progress"])
            after = max(0.0, min(1.0, before + float(goal_progress_delta)))
            self.goals.update_progress(goal_id, after)
            record["goal_progress_update"] = {
                "goal_id": goal_id,
                "before": round(before, 4),
                "after": round(after, 4),
                "evidence_required": True,
                "evidence_present": True,
            }
        else:
            record["goal_progress_update"] = {
                "updated": False,
                "reason": "no progress delta supplied",
                "evidence_required_for_progress": True,
            }
        current = self.owner_outcome_feedback(limit=200)
        updated = [record, *current][:200]
        with self.db.transaction() as connection:
            evidence_id = self.ledger.append(
                "owner_outcome_feedback_recorded", record, connection
            )
            calibration = self.self_model.record_owner_outcome(
                action=record["action"],
                outcome=str(record["outcome"]),
                evidence_id=evidence_id,
                connection=connection,
            )
            record["self_model_calibration"] = {
                "key": calibration["key"],
                "capability_confidence": calibration["value"].get(
                    "capability_confidence"
                ),
                "should_defer": calibration["value"].get("should_defer"),
                "evidence_id": evidence_id,
            }
            self.db.set_runtime("owner_outcome_feedback", updated, connection)
        summary = self._outcome_learning_summary(feedback=updated, limit=20)
        heuristics = self._record_outcome_learning_heuristics(summary)
        return {
            **record,
            "learning_summary": {
                "suppressed_signature_count": len(summary["suppressed_signatures"]),
                "reusable_pattern_count": len(summary["reusable_patterns"]),
                "new_heuristic_memory_ids": heuristics,
            },
        }

    def _owner_feedback_target(self, action_id: str | None) -> dict[str, Any]:
        if action_id:
            row = self.db.query_one(
                """
                SELECT action_id,plan_id,goal_id,tool,purpose,expected_result,risk,status,
                       side_effect_class
                FROM actions WHERE action_id=?
                """,
                (action_id,),
            )
            if row is None:
                raise KeyError(f"unknown action: {action_id}")
            data = dict(row)
            data["risk_class"] = self.action_candidates.classify_action(
                data, side_effect_class=str(data.get("side_effect_class", "unknown"))
            )
            return data
        candidate = self.db.get_runtime("last_action_candidate", None)
        if not isinstance(candidate, dict) or not candidate.get("available"):
            raise ValueError("no action_id supplied and no available action candidate exists")
        return candidate

    def _outcome_learning_summary(
        self,
        *,
        feedback: list[dict[str, Any]] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return self.outcome_learning.summary(
            feedback if feedback is not None else self.owner_outcome_feedback(limit=200),
            limit=limit,
        )

    def _record_outcome_learning_heuristics(
        self, summary: dict[str, Any]
    ) -> list[str]:
        recorded = self.db.get_runtime("outcome_learning_heuristic_signatures", [])
        if not isinstance(recorded, list):
            recorded = []
        recorded_keys = {str(item) for item in recorded}
        created_ids: list[str] = []
        new_keys: list[str] = []
        for kind, key_name, items in (
            ("avoid", "suppressed_signatures", summary.get("suppressed_signatures", [])),
            ("prefer", "reusable_patterns", summary.get("reusable_patterns", [])),
        ):
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                signature = str(item.get("action_signature", ""))
                record_key = f"{kind}:{signature}"
                if not signature or record_key in recorded_keys:
                    continue
                examples = item.get("examples", [])
                action = (
                    examples[0].get("action", {})
                    if examples and isinstance(examples[0], dict)
                    else {}
                )
                effect = "avoid_tool" if kind == "avoid" else "prefer_tool"
                memory_id = self.memories.add(
                    MemoryItem(
                        memory_type="procedural",
                        content={
                            "claim": (
                                "Owner feedback marked this action pattern as one to avoid."
                                if kind == "avoid"
                                else "Owner feedback marked this action pattern as reusable."
                            ),
                            "action_signature": signature,
                            "decision_guidance": {
                                "effect": effect,
                                "tool": action.get("tool"),
                                "reason": item.get(
                                    "suppression_reason",
                                    item.get("reuse_reason", "owner_outcome_feedback"),
                                ),
                            },
                            "feedback_counts": {
                                "helped": item.get("helped", 0),
                                "failed": item.get("failed", 0),
                                "avoid": item.get("avoid", 0),
                            },
                        },
                        importance=0.86 if kind == "avoid" else 0.78,
                        confidence=0.88,
                        source_ids=[
                            str(example.get("feedback_id"))
                            for example in examples
                            if isinstance(example, dict) and example.get("feedback_id")
                        ]
                        or [signature],
                        tags=["owner-outcome", f"{kind}-heuristic"],
                    )
                )
                created_ids.append(memory_id)
                new_keys.append(record_key)
                recorded_keys.add(record_key)
        if new_keys:
            self.db.set_runtime(
                "outcome_learning_heuristic_signatures",
                [*new_keys, *recorded][:200],
            )
        return created_ids

    def _life_state_memory_influence(self, limit: int) -> dict[str, Any]:
        proof = self.db.get_runtime("last_memory_influence", None)
        if not isinstance(proof, dict):
            return {
                "available": False,
                "reason": "no memory influence proof has been recorded yet",
            }
        bounded = dict(proof)
        influences = bounded.get("influences", [])
        bounded["influences"] = (
            influences[:limit] if isinstance(influences, list) else []
        )
        return {"available": True, **bounded}

    def _daily_perception_summary(self, limit: int = 10) -> dict[str, Any]:
        day = datetime.now(UTC).date().isoformat()
        observations = self._daily_observation_dicts(day=day, limit=500)
        goals = self.goals.active(limit=20)
        memories = self.memories.recent(limit=50)
        return self.perception.daily_summary(
            observations,
            goals=goals,
            memories=memories,
            limit=limit,
            day=day,
        )

    def _daily_observation_dicts(
        self, *, day: str, limit: int
    ) -> list[dict[str, Any]]:
        day_start = f"{day}T00:00:00+00:00"
        rows = self.db.query_all(
            """
            SELECT o.observation_id,o.source,o.kind,o.subject,o.predicate,o.value_json,
                   o.confidence,o.evidence_kind,o.verification,o.observed_at,
                   o.metadata_json,o.event_id,e.salience_hint
            FROM observations o
            LEFT JOIN events e ON e.event_id=o.event_id
            WHERE o.observed_at >= ?
            ORDER BY o.observed_at DESC
            LIMIT ?
            """,
            (day_start, max(1, min(1000, int(limit)))),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "observation_id": row["observation_id"],
                    "event_id": row["event_id"],
                    "source": row["source"],
                    "kind": row["kind"],
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "value": json.loads(row["value_json"]),
                    "confidence": float(row["confidence"]),
                    "evidence_kind": row["evidence_kind"],
                    "verification": row["verification"],
                    "observed_at": row["observed_at"],
                    "metadata": json.loads(row["metadata_json"]),
                    "salience": float(row["salience_hint"] or 0.0),
                }
            )
        return result

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
        advisory_outcome = self._record_repair_skill_action_outcome(
            action=action,
            approval_id=approval_id,
            approval_valid=approval_valid,
            action_completed_evidence_id=evidence_id,
            result=result,
            final_success=final_success,
        )
        promoted_repair_skill_outcome = (
            advisory_outcome
            if isinstance(advisory_outcome, dict)
            and advisory_outcome.get("skill_status") == CandidateStatus.PROMOTED.value
            else None
        )
        if advisory_outcome is not None:
            payload["repair_skill_outcome"] = advisory_outcome
            payload["approved_repair_skill_outcome"] = advisory_outcome
            if promoted_repair_skill_outcome is not None:
                payload["promoted_repair_skill_outcome"] = promoted_repair_skill_outcome
        return {
            "action_id": action.action_id,
            "success": final_success,
            "status": final_status.value,
            "evaluation": evaluation,
            "output": result.output,
            "error": result.error,
            "repair_skill_outcome": advisory_outcome,
            "approved_repair_skill_outcome": advisory_outcome,
            "promoted_repair_skill_outcome": promoted_repair_skill_outcome,
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

    def life_state(self, limit: int | None = None) -> dict[str, Any]:
        """Return the compact living-system loop state for owner-facing views."""

        item_limit = self._life_state_limit(limit)
        active_goals = [
            self._compact_goal(goal)
            for goal in self.goals.active(limit=item_limit)
        ]
        memory_influences = self._top_memory_influences(limit=item_limit)
        daily_perception = self._life_state_daily_perception(item_limit)
        goal_pressure = self._goal_pressure_summary(
            goals=active_goals,
            daily_perception=daily_perception,
            limit=item_limit,
        )
        pressure_order = {
            item["goal_id"]: index
            for index, item in enumerate(goal_pressure["ranked_goals"])
        }
        active_goals.sort(
            key=lambda goal: pressure_order.get(goal["goal_id"], len(active_goals))
        )
        memory_influence_proof = self._life_state_memory_influence(item_limit)
        observations = self._latest_meaningful_observations(
            limit=item_limit,
            goals=active_goals,
            memories=memory_influences,
        )
        pending_approvals = self._pending_owner_approvals(
            limit=item_limit
        )
        next_action = self._next_action_candidate(
            active_goals, observations, goal_pressure=goal_pressure
        )
        return {
            "schema_version": 1,
            "version": __version__,
            "generated_at": utc_now(),
            "bounded": True,
            "authority": {
                "source": "canonical life organs; evidence/readiness remains safety context",
                "writes_canonical_state": False,
                "creates_evidence_receipt": False,
                "candidate_executes_action": False,
            },
            "system": {
                "home": str(self.config.home_path),
                "read_only": self.config.read_only,
                "paused": bool(self.db.get_runtime("paused", False)),
                "killed": bool(self.db.get_runtime("kill_switch", False)),
                "cycle_count": int(self.db.get_runtime("cycle_count", 0)),
            },
            "loop": [
                "sense",
                "remember",
                "judge",
                "act",
                "learn",
                "self-model",
                "sleep",
            ],
            "active_goals": active_goals,
            "latest_meaningful_observations": observations,
            "top_memory_influences": memory_influences,
            "memory_influence_proof": memory_influence_proof,
            "outcome_learning": self._outcome_learning_summary(limit=item_limit),
            "daily_perception": daily_perception,
            "goal_pressure": goal_pressure,
            "self_model_confidence": self._self_model_confidence(),
            "self_model_calibration": self._self_model_calibration(item_limit),
            "pending_owner_approvals": pending_approvals,
            "last_sleep_consolidation": self._last_sleep_consolidation_summary(),
            "next_action_candidate": next_action,
            "bounds": {
                "max_items_per_section": item_limit,
                "max_text_chars": self.MAX_LIFE_STATE_TEXT_CHARS,
                "source": "canonical life organs; evidence/readiness remains safety context",
            },
        }

    @classmethod
    def _life_state_limit(cls, limit: int | None) -> int:
        if limit is None:
            return cls.MAX_LIFE_STATE_ITEMS
        return max(1, min(20, int(limit)))

    def _life_state_daily_perception(self, limit: int) -> dict[str, Any]:
        summary = self.db.get_runtime("daily_perception", None)
        if not isinstance(summary, dict):
            summary = self._daily_perception_summary(limit=limit)
        bounded = dict(summary)
        top_changes = bounded.get("top_daily_changes", [])
        bounded["top_daily_changes"] = (
            top_changes[:limit] if isinstance(top_changes, list) else []
        )
        return bounded

    def _latest_meaningful_observations(
        self,
        limit: int,
        *,
        goals: list[dict[str, Any]] | None = None,
        memories: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT o.observation_id,o.source,o.kind,o.subject,o.predicate,
                   o.value_json,o.confidence,o.evidence_kind,o.verification,
                   o.observed_at,o.metadata_json,o.event_id,e.salience_hint
            FROM observations o
            LEFT JOIN events e ON e.event_id=o.event_id
            WHERE COALESCE(e.salience_hint, 0.0) >= 0.25
               OR o.kind IN ('sensor_error','service_health','resource','state')
            ORDER BY COALESCE(e.salience_hint, 0.0) DESC, o.observed_at DESC
            LIMIT ?
            """,
            (max(1, min(100, int(limit) * 4)),),
        )
        observations: list[dict[str, Any]] = []
        for row in rows:
            observation = {
                "observation_id": row["observation_id"],
                "event_id": row["event_id"],
                "source": row["source"],
                "kind": row["kind"],
                "subject": self._life_state_text(row["subject"]),
                "predicate": self._life_state_text(row["predicate"]),
                "value": self._life_state_value(json.loads(row["value_json"])),
                "confidence": float(row["confidence"]),
                "evidence_kind": row["evidence_kind"],
                "verification": row["verification"],
                "observed_at": row["observed_at"],
                "metadata": self._life_state_value(json.loads(row["metadata_json"])),
                "salience": float(row["salience_hint"] or 0.0),
            }
            observation["perception"] = self.perception.classify(
                observation,
                goals=goals or [],
                memories=memories or [],
            )
            observations.append(observation)
        meaningful = [
            item
            for item in observations
            if item["perception"]["classification"] != "noise"
        ]
        selected = meaningful or observations
        selected.sort(
            key=lambda item: (
                bool(item["perception"]["meaningful"]),
                float(item["perception"]["score"]),
                str(item.get("observed_at", "")),
            ),
            reverse=True,
        )
        return selected[: max(1, min(20, int(limit)))]

    def _top_memory_influences(self, limit: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT memory_id,memory_type,content_json,importance,confidence,
                   source_ids_json,tags_json,created_at,last_accessed_at,access_count
            FROM memories
            WHERE active=1
            ORDER BY (importance * confidence) DESC, access_count DESC, created_at DESC
            LIMIT ?
            """,
            (max(1, min(50, int(limit))),),
        )
        return [
            {
                "memory_id": row["memory_id"],
                "memory_type": row["memory_type"],
                "content": self._life_state_value(json.loads(row["content_json"])),
                "importance": float(row["importance"]),
                "confidence": float(row["confidence"]),
                "source_ids": json.loads(row["source_ids_json"])[:5],
                "tags": json.loads(row["tags_json"])[:8],
                "created_at": row["created_at"],
                "last_accessed_at": row["last_accessed_at"],
                "access_count": int(row["access_count"]),
                "influence_basis": "active memory ranked by importance, confidence, and prior access",
            }
            for row in rows
        ]

    def _self_model_confidence(self) -> dict[str, Any]:
        snapshot = self.self_model.snapshot()
        entries: list[dict[str, Any]] = [
            {
                "key": key,
                "confidence": float(value.get("confidence", 0.0)),
                "updated_at": value.get("updated_at"),
            }
            for key, value in snapshot.items()
            if isinstance(value, dict)
        ]
        if not entries:
            return {"overall": 0.0, "entry_count": 0, "top_entries": []}
        entries.sort(key=lambda item: (item["confidence"], item["key"]), reverse=True)
        overall = sum(float(item["confidence"]) for item in entries) / len(entries)
        return {
            "overall": round(overall, 3),
            "entry_count": len(entries),
            "top_entries": entries[: self.MAX_LIFE_STATE_ITEMS],
        }

    def _self_model_calibration(self, limit: int) -> dict[str, Any]:
        snapshot = self.self_model.snapshot()
        entries: list[dict[str, Any]] = []
        for key, item in snapshot.items():
            if not str(key).startswith("capability.owner_outcome."):
                continue
            value = item.get("value", {})
            if not isinstance(value, dict):
                continue
            entries.append(
                {
                    "key": key,
                    "tool": value.get("tool"),
                    "risk_class": value.get("risk_class"),
                    "capability_confidence": value.get("capability_confidence"),
                    "owner_observation_count": value.get("owner_observation_count", 0),
                    "should_defer": bool(value.get("should_defer", False)),
                    "defer_reason": value.get("defer_reason", ""),
                    "updated_at": item.get("updated_at"),
                }
            )
        entries.sort(
            key=lambda entry: (
                bool(entry["should_defer"]),
                int(entry.get("owner_observation_count", 0) or 0),
                str(entry.get("updated_at", "")),
            ),
            reverse=True,
        )
        return {
            "available": bool(entries),
            "deferred_capabilities": [
                item for item in entries if item["should_defer"]
            ][:limit],
            "calibrated_capabilities": entries[:limit],
            "authority": {
                "owner_outcomes_required": True,
                "receipt_volume_is_not_capability": True,
            },
        }

    def _pending_owner_approvals(self, limit: int) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT action_id,plan_id,goal_id,tool,purpose,expected_result,risk,status,
                   approval_id,started_at,finished_at,error
            FROM actions
            WHERE status IN ('WAITING_APPROVAL','APPROVED','UNKNOWN_SIDE_EFFECT')
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (max(1, min(50, int(limit))),),
        )
        return [
            {
                "action_id": row["action_id"],
                "plan_id": row["plan_id"],
                "goal_id": row["goal_id"],
                "tool": row["tool"],
                "purpose": self._life_state_text(row["purpose"]),
                "expected_result": self._life_state_text(row["expected_result"]),
                "risk": row["risk"],
                "status": row["status"],
                "approval_id": row["approval_id"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "reason": self._life_state_text(row["error"] or ""),
            }
            for row in rows
        ]

    def _last_sleep_consolidation_summary(self) -> dict[str, Any]:
        row = self.db.query_one(
            """
            SELECT evidence_id,payload_json,created_at
            FROM evidence
            WHERE event_type='sleep_consolidation_completed'
            ORDER BY seq DESC
            LIMIT 1
            """
        )
        next_focus = self.db.get_runtime("next_focus", [])
        if row is None:
            return {
                "last_sleep_at": self.db.get_runtime("last_sleep_at", None),
                "evidence_id": None,
                "summary": "No sleep consolidation has been recorded yet.",
                "next_focus": next_focus[: self.MAX_LIFE_STATE_ITEMS]
                if isinstance(next_focus, list)
                else [],
            }
        payload = json.loads(row["payload_json"])
        return {
            "last_sleep_at": self.db.get_runtime("last_sleep_at", row["created_at"]),
            "evidence_id": row["evidence_id"],
            "created_at": row["created_at"],
            "expired_facts": int(payload.get("expired_facts", 0) or 0),
            "contradictions_resolved": int(
                payload.get("contradictions_resolved", 0) or 0
            ),
            "semantic_created_count": len(payload.get("semantic_created", []) or []),
            "duplicates_deactivated": int(
                payload.get("duplicates_deactivated", 0) or 0
            ),
            "skill_candidate_count": len(payload.get("skill_candidates", []) or []),
            "focus": (payload.get("focus", []) or [])[: self.MAX_LIFE_STATE_ITEMS],
        }

    def _next_action_candidate(
        self,
        active_goals: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        *,
        goal_pressure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.db.query_one(
            """
            SELECT action_id,plan_id,goal_id,tool,purpose,expected_result,risk,status,
                   approval_id,error,side_effect_class
            FROM actions
            WHERE status IN ('WAITING_APPROVAL','APPROVED','PLANNED')
            ORDER BY CASE status
                WHEN 'WAITING_APPROVAL' THEN 0
                WHEN 'APPROVED' THEN 1
                ELSE 2
            END, rowid DESC
            LIMIT 1
            """
        )
        if row is not None:
            return {
                "available": True,
                "action_id": row["action_id"],
                "plan_id": row["plan_id"],
                "goal_id": row["goal_id"],
                "tool": row["tool"],
                "purpose": self._life_state_text(row["purpose"]),
                "expected_result": self._life_state_text(row["expected_result"]),
                "risk": row["risk"],
                "risk_class": self.action_candidates.classify_action(
                    dict(row), side_effect_class=row["side_effect_class"]
                ),
                "side_effect_class": row["side_effect_class"],
                "status": row["status"],
                "approval_required": row["status"] == "WAITING_APPROVAL",
                "requires_owner_approval": row["status"] == "WAITING_APPROVAL",
                "approval_id": row["approval_id"],
                "reason": self._life_state_text(row["error"] or ""),
            }
        recorded = self.db.get_runtime("last_action_candidate", None)
        if isinstance(recorded, dict) and recorded.get("available"):
            return recorded
        if goal_pressure and isinstance(goal_pressure.get("next_small_step"), dict):
            step = goal_pressure["next_small_step"]
            if step.get("available"):
                return {
                    **step,
                    "available": True,
                    "status": "CANDIDATE_ONLY",
                    "approval_required": False,
                    "requires_owner_approval": False,
                    "risk_class": "read",
                    "source": "goal_pressure",
                    "reason": step.get("rationale", ""),
                }
        if not active_goals:
            reason = "No active goals are available to drive a next action."
        elif not observations:
            reason = "Active goals exist, but no meaningful observations are available yet."
        else:
            reason = "No planned or approval-ready action candidate exists yet."
        return {"available": False, "reason": reason}

    @classmethod
    def _compact_goal(cls, goal: Goal) -> dict[str, Any]:
        return {
            "goal_id": goal.goal_id,
            "title": cls._life_state_text(goal.title),
            "description": cls._life_state_text(goal.description),
            "priority": goal.priority,
            "status": goal.status.value,
            "progress": goal.progress,
            "source": goal.source,
            "autonomous": goal.autonomous,
            "deadline": goal.deadline,
            "updated_at": goal.updated_at,
            "risk": goal.risk.value,
        }

    @classmethod
    def _life_state_text(cls, value: Any) -> str:
        text = "" if value is None else str(value)
        if len(text) <= cls.MAX_LIFE_STATE_TEXT_CHARS:
            return text
        return text[: cls.MAX_LIFE_STATE_TEXT_CHARS - 3] + "..."

    @classmethod
    def _life_state_value(cls, value: Any) -> Any:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if len(rendered) <= cls.MAX_LIFE_STATE_TEXT_CHARS:
            return value
        return {
            "truncated": True,
            "preview": rendered[: cls.MAX_LIFE_STATE_TEXT_CHARS - 3] + "...",
        }

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
            "external_product_audit_receipts": self.external_product_audit_receipts(),
            "m7_self_check_audit_receipts": self.m7_self_check_audit_receipts(),
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
            "external_product": latest_runtime_receipt(
                "external_product_audit_last",
                "external_product_audit_receipts",
            ),
            "m7_self_check": latest_runtime_receipt(
                "m7_self_check_audit_last",
                "m7_self_check_audit_receipts",
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
