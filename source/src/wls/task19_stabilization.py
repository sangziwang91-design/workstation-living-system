from __future__ import annotations

from types import MethodType
from typing import Any
import json

from .cognition import HypothesisCandidate
from .schemas import ActionSpec, Plan, RiskLevel, digest_json


STABILIZATION_VERSION = "task19-convergence-2"
SUPPORTED_TASK_ACTIONS = {"noop", "inspect_path", "read_file", "record_progress"}
ATTRIBUTABLE_PROVENANCE = {
    "EXECUTED_CURRENT_ACTION",
    "RECOVERED_DURABLE_ACTION",
}


def _validated_task_spec(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("task_spec must be an object")
    action = str(value.get("action", "")).strip()
    if action not in SUPPORTED_TASK_ACTIONS:
        raise ValueError(f"unsupported task_spec action: {action or '<missing>'}")
    allowed = {
        "noop": {"action", "reason"},
        "inspect_path": {"action", "path", "limit"},
        "read_file": {"action", "path", "max_bytes"},
        "record_progress": {"action", "reason"},
    }[action]
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"unexpected task_spec keys for {action}: {sorted(extra)}")
    result = dict(value)
    if action in {"inspect_path", "read_file"}:
        path = str(result.get("path", "")).strip()
        if not path:
            raise ValueError(f"task_spec {action} requires path")
        result["path"] = path
    if action == "inspect_path":
        limit = int(result.get("limit", 200))
        if not 1 <= limit <= 1000:
            raise ValueError("inspect_path limit must be within 1..1000")
        result["limit"] = limit
    if action == "read_file":
        max_bytes = int(result.get("max_bytes", 524288))
        if not 1 <= max_bytes <= 4 * 1024 * 1024:
            raise ValueError("read_file max_bytes must be within 1..4194304")
        result["max_bytes"] = max_bytes
    return result


def _task_action(goal: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    action = spec["action"]
    goal_id = str(goal.get("goal_id", "")) or None
    title = str(goal.get("title", "bounded goal"))
    if action == "inspect_path":
        return {
            "tool": "list_directory",
            "arguments": {"path": spec["path"], "limit": spec["limit"]},
            "purpose": f"Advance goal through bounded path inspection: {title}",
            "expected_result": "Bounded directory listing",
            "risk": RiskLevel.READ.value,
            "goal_id": goal_id,
            "skill_id": None,
            "acceptance": ["output contains items"],
        }
    if action == "read_file":
        return {
            "tool": "read_file",
            "arguments": {"path": spec["path"], "max_bytes": spec["max_bytes"]},
            "purpose": f"Advance goal through bounded file inspection: {title}",
            "expected_result": "File content or bounded binary preview",
            "risk": RiskLevel.READ.value,
            "goal_id": goal_id,
            "skill_id": None,
            "acceptance": ["output contains path"],
        }
    reason = str(spec.get("reason", "bounded task specification"))[:1000]
    return {
        "tool": "noop",
        "arguments": {"reason": reason},
        "purpose": f"Record bounded goal progress without external side effect: {title}",
        "expected_result": "No external change",
        "risk": RiskLevel.READ.value,
        "goal_id": goal_id,
        "skill_id": None,
        "acceptance": ["output ok is true"],
    }


def _action_spec(value: dict[str, Any]) -> ActionSpec:
    return ActionSpec(
        tool=str(value["tool"]),
        arguments=dict(value.get("arguments", {})),
        purpose=str(value["purpose"]),
        expected_result=str(value["expected_result"]),
        risk=RiskLevel(str(value.get("risk", RiskLevel.READ.value))),
        goal_id=value.get("goal_id"),
        skill_id=value.get("skill_id"),
        acceptance=[str(item) for item in value.get("acceptance", [])],
    )


def _is_attributable(item: dict[str, Any]) -> bool:
    provenance = item.get("provenance")
    return provenance in ATTRIBUTABLE_PROVENANCE or provenance is None


def _filter_attributable(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in outcomes if _is_attributable(item)]


def register_wls(runtime: Any) -> None:
    """Install bounded repairs through existing WLS authorities only."""
    if getattr(runtime, "_task19_stabilization_installed", False):
        return
    runtime._task19_stabilization_installed = True
    runtime._task19_goal_counterfactuals = {}
    runtime._task19_recovery_mode = False

    decomposer = runtime.goal_runtime.decomposer
    original_decompose = decomposer.decompose

    def decompose(self: Any, goal_id: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        validated: list[dict[str, Any]] = []
        for item in tasks:
            clone = dict(item)
            clone["task_spec"] = _validated_task_spec(clone.get("task_spec"))
            validated.append(clone)
        return original_decompose(goal_id, validated)

    decomposer.decompose = MethodType(decompose, decomposer)

    cognition = runtime.cognition
    original_candidates = cognition._candidates

    def candidates(
        self: Any, context: dict[str, Any], *, include_memories: bool
    ) -> list[HypothesisCandidate]:
        result = original_candidates(context, include_memories=include_memories)
        for goal in context.get("goals", []):
            raw_spec = goal.get("task_spec")
            if not raw_spec:
                continue
            spec = _validated_task_spec(raw_spec)
            action = _task_action(goal, spec)
            goal_id = str(goal.get("goal_id", "")) or None
            result.append(
                HypothesisCandidate(
                    key=f"goal_task_spec:{goal_id}:{spec['action']}",
                    subject=str(goal.get("title", goal_id or "goal")),
                    claim="The accepted persistent goal supplies a bounded executable task specification.",
                    rationale=(
                        "The task specification is schema-validated and remains subject "
                        "to canonical policy and tool gates."
                    ),
                    base_score=min(0.99, 0.90 + 0.05 * float(goal.get("priority", 0.5))),
                    actions=[action],
                    support_ids=[goal_id] if goal_id else [],
                    goal_id=goal_id,
                    cause_predicate="validated_goal_task_spec",
                )
            )
        return result

    cognition._candidates = MethodType(candidates, cognition)

    planner = runtime.planner
    original_plan = planner.plan

    def plan(self: Any, context: dict[str, Any]) -> Plan:
        cycle_id = str(context.get("cycle_id", ""))
        goal_free = dict(context)
        goal_free["goals"] = []
        goal_free["workspace"] = [
            item
            for item in context.get("workspace", [])
            if item.get("item_type") != "goal"
        ]
        ranked = runtime.cognition._rank(goal_free, include_memories=True)
        if ranked:
            chosen = ranked[0]
            runtime._task19_goal_counterfactuals[cycle_id] = {
                "key": chosen.key,
                "score": float(chosen.score),
                "action_digest": digest_json(chosen.actions),
                "context_digest": digest_json(
                    {
                        "workspace": goal_free.get("workspace", []),
                        "goals": [],
                        "world_facts": goal_free.get("world_facts", []),
                        "memories": goal_free.get("memories", []),
                        "matching_skills": goal_free.get("matching_skills", []),
                        "budget": goal_free.get("budget", {}),
                        "available_tools": goal_free.get("available_tools", []),
                    }
                ),
                "method": "WRITE_FREE_BOUNDED_COGNITIVE_RANK",
            }
        result = original_plan(context)
        max_actions = max(0, int(context.get("budget", {}).get("max_actions", 0)))
        if max_actions:
            for goal in context.get("goals", []):
                raw_spec = goal.get("task_spec")
                if not raw_spec:
                    continue
                goal_id = str(goal.get("goal_id", "")) or None
                if any(action.goal_id == goal_id for action in result.actions):
                    break
                desired = _action_spec(_task_action(goal, _validated_task_spec(raw_spec)))
                result.actions = [desired, *result.actions][:max_actions]
                break
        return result

    planner.plan = MethodType(plan, planner)

    goal_runtime = runtime.goal_runtime
    original_record_decision = goal_runtime.record_decision

    def record_decision(
        self: Any,
        cycle_id: str,
        *,
        selected_goal_ids: list[str],
        counterfactual: dict[str, Any],
    ) -> dict[str, Any]:
        frozen = runtime._task19_goal_counterfactuals.pop(cycle_id, None)
        trace = runtime.db.query_one(
            "SELECT trace_id,selected_key,selected_hypothesis_id FROM cognitive_traces WHERE cycle_id=?",
            (cycle_id,),
        )
        if frozen is None:
            frozen = dict(counterfactual)
            frozen["method"] = "CALLER_FALLBACK_UNVERIFIED"
        effective_goal_ids = selected_goal_ids if trace is not None else []
        result = original_record_decision(
            cycle_id,
            selected_goal_ids=effective_goal_ids,
            counterfactual=frozen,
        )
        actual_actions: list[dict[str, Any]] = []
        if trace is not None and trace["selected_hypothesis_id"]:
            row = runtime.db.query_one(
                "SELECT actions_json FROM cognitive_hypotheses WHERE hypothesis_id=?",
                (trace["selected_hypothesis_id"],),
            )
            if row is not None:
                actual_actions = json.loads(row["actions_json"])
        actual_digest = digest_json(actual_actions)
        counter_digest = str(frozen.get("action_digest", ""))
        influenced = bool(
            effective_goal_ids
            and (
                str(trace["selected_key"]) != str(frozen.get("key", ""))
                or (counter_digest and actual_digest != counter_digest)
            )
        )
        runtime.db.execute(
            "UPDATE goal_attributions SET goal_influenced_decision=? WHERE goal_trace_id=?",
            (int(influenced), result["goal_trace_id"]),
        )
        result["goal_influenced_decision"] = influenced
        result["actual_action_digest"] = actual_digest
        result["counterfactual_action_digest"] = counter_digest
        runtime.ledger.append(
            "goal_attribution_refined",
            {
                "goal_trace_id": result["goal_trace_id"],
                "goal_influenced_decision": influenced,
                "actual_action_digest": actual_digest,
                "counterfactual_action_digest": counter_digest,
            },
        )
        return result

    goal_runtime.record_decision = MethodType(record_decision, goal_runtime)

    original_execute_action = runtime._execute_action

    def execute_action(
        self: Any, action: Any, approval_id: str | None = None
    ) -> dict[str, Any]:
        prior = self.db.query_one(
            "SELECT action_id FROM actions WHERE idempotency_key=? AND status='SUCCEEDED' AND action_id<>? ORDER BY rowid ASC LIMIT 1",
            (action.idempotency_key, action.action_id),
        )
        result = original_execute_action(action, approval_id=approval_id)
        if result.get("reused"):
            provenance = "REUSED_PRIOR_RESULT"
        elif self._task19_recovery_mode and result.get("status") in {"SUCCEEDED", "FAILED"}:
            provenance = "RECOVERED_DURABLE_ACTION"
        elif result.get("status") in {"SUCCEEDED", "FAILED"}:
            provenance = "EXECUTED_CURRENT_ACTION"
        else:
            provenance = "NO_OBSERVABLE_OUTCOME"
        source_action_id = str(prior["action_id"]) if prior is not None else None
        evidence_id = self.ledger.append(
            "action_outcome_provenance",
            {
                "action_id": action.action_id,
                "provenance": provenance,
                "source_action_id": source_action_id,
                "result_digest": digest_json(result),
            },
        )
        result.update(
            {
                "provenance": provenance,
                "source_action_id": source_action_id,
                "provenance_evidence_id": evidence_id,
            }
        )
        return result

    runtime._execute_action = MethodType(execute_action, runtime)

    original_execute_plan = runtime._execute_plan

    def execute_plan(self: Any, plan: Plan) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        skill_results: dict[str, list[bool]] = {}
        for action in plan.actions:
            outcome = self._execute_action(action)
            outcomes.append(outcome)
            if not _is_attributable(outcome):
                continue
            if action.skill_id:
                skill_results.setdefault(action.skill_id, []).append(
                    bool(outcome.get("success"))
                )
            if action.goal_id and outcome.get("success"):
                row = self.db.query_one(
                    "SELECT progress FROM goals WHERE goal_id=?", (action.goal_id,)
                )
                if row is not None:
                    self.goals.update_progress(
                        action.goal_id, min(1.0, float(row["progress"]) + 0.25)
                    )
        for skill_id, values in skill_results.items():
            self.skills.record_use(skill_id, all(values))
        self._refresh_plan_status(plan.plan_id)
        return outcomes

    runtime._execute_plan = MethodType(execute_plan, runtime)
    runtime._task19_original_execute_plan = original_execute_plan

    original_resume = runtime._resume_durable_actions

    def resume_durable_actions(self: Any) -> list[dict[str, Any]]:
        self._task19_recovery_mode = True
        try:
            return original_resume()
        finally:
            self._task19_recovery_mode = False

    runtime._resume_durable_actions = MethodType(resume_durable_actions, runtime)

    original_cognition_resolve = cognition.resolve_cycle

    def cognition_resolve(
        self: Any, cycle_id: str, plan: Plan, outcomes: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        return original_cognition_resolve(
            cycle_id, plan, _filter_attributable(outcomes)
        )

    cognition.resolve_cycle = MethodType(cognition_resolve, cognition)

    memory_attribution = runtime.memory_attribution
    original_memory_resolve = memory_attribution.resolve

    def memory_resolve(
        self: Any,
        cycle_id: str,
        outcomes: list[dict[str, Any]],
        cognition_result: dict[str, Any] | None,
        *,
        frozen: bool = False,
    ) -> dict[str, Any] | None:
        return original_memory_resolve(
            cycle_id,
            _filter_attributable(outcomes),
            cognition_result,
            frozen=frozen,
        )

    memory_attribution.resolve = MethodType(memory_resolve, memory_attribution)

    original_goal_resolve = goal_runtime.resolve_cycle

    def goal_resolve(
        self: Any,
        cycle_id: str,
        *,
        selected_goal_ids: list[str],
        outcomes: list[dict[str, Any]],
        attribution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return original_goal_resolve(
            cycle_id,
            selected_goal_ids=selected_goal_ids,
            outcomes=_filter_attributable(outcomes),
            attribution=attribution,
        )

    goal_runtime.resolve_cycle = MethodType(goal_resolve, goal_runtime)

    runtime.ledger.append(
        "task19_stabilization_installed",
        {
            "version": STABILIZATION_VERSION,
            "task_spec_actions": sorted(SUPPORTED_TASK_ACTIONS),
            "outcome_provenance": sorted(
                {
                    "EXECUTED_CURRENT_ACTION",
                    "REUSED_PRIOR_RESULT",
                    "RECOVERED_DURABLE_ACTION",
                    "NO_OBSERVABLE_OUTCOME",
                }
            ),
        },
    )
