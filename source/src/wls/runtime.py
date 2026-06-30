from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
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
from .schemas import (
    ActionSpec,
    ActionStatus,
    Event,
    Goal,
    Plan,
    RiskLevel,
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
