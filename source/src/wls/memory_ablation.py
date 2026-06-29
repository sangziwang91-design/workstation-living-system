from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, TypedDict
import hashlib
import json

from .config import default_config
from .runtime import LivingSystem
from .schemas import MemoryItem, Observation


ADVANTAGE_MEMORY_ID = "mem_et003_advantage"
REFUTED_MEMORY_ID = "mem_et003_refutation"


class _AblationTask(TypedDict):
    task_id: str
    project_id: str
    entity_id: str
    failure_signature: str
    task_kind: str
    expected_behavior: str
    path_enabled: Path
    path_disabled: Path


class _RefutationTask(TypedDict):
    path: Path
    project_id: str
    entity_id: str
    failure_signature: str
    task_kind: str
    expected_behavior: str


def _make_runtime(home: Path, memory_mode: str = "enabled") -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    config.max_actions_per_cycle = 2
    config.sleep_after_idle_cycles = 100
    config.provider = {
        "type": "cognitive",
        "fallback": "deterministic",
        "memory_mode": memory_mode,
    }
    return LivingSystem(config)


def _add_advantage_memory(runtime: LivingSystem) -> str:
    memory = MemoryItem(
        memory_id=ADVANTAGE_MEMORY_ID,
        memory_type="failure",
        content={
            "project_id": "project-et003-ablation",
            "entity_id": "entity-missing-target",
            "failure_signature": "list_directory:missing_path",
            "outcome_type": "FAILURE",
            "applicability_conditions": {
                "task_kind": "inspect_known_missing_path"
            },
            "decision_guidance": {
                "effect": "avoid_tool",
                "tool": "list_directory",
                "reason": (
                    "The same project/entity/failure mechanism previously showed "
                    "that the requested path is absent; do not repeat the wrong tool."
                ),
            },
            "mechanism": "list_directory on the known absent target repeats failure",
        },
        importance=1.0,
        confidence=0.99,
        source_ids=["failure-evidence-et003-001"],
        tags=["causal", "failure", "controlled-ablation"],
    )
    return runtime.memories.add(memory)


def _add_refutation_memory(runtime: LivingSystem) -> str:
    memory = MemoryItem(
        memory_id=REFUTED_MEMORY_ID,
        memory_type="causal",
        content={
            "project_id": "project-et003-refutation",
            "entity_id": "entity-valid-target",
            "failure_signature": "contract:legacy_listing",
            "outcome_type": "SUCCESS",
            "applicability_conditions": {
                "task_kind": "inspect_valid_path_with_legacy_contract"
            },
            "decision_guidance": {
                "effect": "replace_acceptance",
                "tool": "list_directory",
                "acceptance": ["output contains impossible_et003_marker"],
                "reason": (
                    "A legacy causal memory incorrectly predicts that the marker "
                    "is required for directory inspection."
                ),
            },
            "mechanism": "legacy acceptance contract assumed a nonexistent marker",
        },
        importance=1.0,
        confidence=0.99,
        source_ids=["legacy-evidence-et003-001"],
        tags=["causal", "refutation-probe"],
    )
    return runtime.memories.add(memory)


def _task_observation(
    *,
    task_id: str,
    path: Path,
    project_id: str,
    entity_id: str,
    failure_signature: str,
    task_kind: str,
) -> Observation:
    return Observation(
        source="et003-controlled-sequence",
        kind="external_event",
        subject=f"task:{task_id}",
        predicate="request",
        value={"action": "inspect_path", "path": str(path)},
        confidence=1.0,
        metadata={
            "dedupe_key": f"et003:{project_id}:{task_id}",
            "project_id": project_id,
            "entity_id": entity_id,
            "failure_signature": failure_signature,
            "conditions": {"task_kind": task_kind},
        },
    )


def _run_task(
    runtime: LivingSystem,
    *,
    task_id: str,
    path: Path,
    project_id: str,
    entity_id: str,
    failure_signature: str,
    task_kind: str,
    expected_behavior: str,
    after_restart: bool,
) -> dict[str, Any]:
    observation = _task_observation(
        task_id=task_id,
        path=path,
        project_id=project_id,
        entity_id=entity_id,
        failure_signature=failure_signature,
        task_kind=task_kind,
    )
    runtime.events.add_observation(observation)
    runtime.world.assimilate(observation)
    result = runtime.run_cycle()
    plan = runtime.db.query_one(
        "SELECT plan_id FROM plans WHERE cycle_id=?", (result["cycle_id"],)
    )
    action = (
        runtime.db.query_one(
            """
            SELECT tool,status,error FROM actions
            WHERE plan_id=? ORDER BY rowid LIMIT 1
            """,
            (plan["plan_id"],),
        )
        if plan is not None
        else None
    )
    cognition = result.get("cognition") or {}
    attribution = cognition.get("memory_attribution") or {}
    predictions = cognition.get("predictions", [])
    tool = str(action["tool"]) if action is not None else "missing"
    success = bool(result.get("outcomes") and result["outcomes"][0].get("success"))
    if expected_behavior == "avoid_known_failure":
        completed = tool == "noop" and success
        wrong_tool = tool == "list_directory"
    else:
        completed = tool == "list_directory" and success
        wrong_tool = tool != "list_directory"
    semantic_event = {
        "project_id": project_id,
        "entity_id": entity_id,
        "failure_signature": failure_signature,
        "task_kind": task_kind,
        "expected_behavior": expected_behavior,
    }
    return {
        "task_id": task_id,
        "semantic_event": semantic_event,
        "semantic_event_sha256": hashlib.sha256(
            json.dumps(semantic_event, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "cycle_id": result["cycle_id"],
        "selected_key": cognition.get("selected_key"),
        "tool": tool,
        "action_status": str(action["status"]) if action is not None else "MISSING",
        "success": success,
        "task_completed": completed,
        "wrong_tool": wrong_tool,
        "prediction_confirmed": bool(predictions)
        and all(item.get("status") == "CONFIRMED" for item in predictions),
        "selected_memory_ids": attribution.get("selected_memory_ids", []),
        "suppressed_memory_ids": attribution.get("suppressed_memory_ids", []),
        "memory_changed_decision": attribution.get("memory_changed_decision", False),
        "memory_delta": attribution.get("memory_delta", 0.0),
        "counterfactual_without_memory": attribution.get(
            "counterfactual_without_memory", {}
        ),
        "causal_reason": attribution.get("causal_reason", ""),
        "after_restart": after_restart,
    }


def _decision_family(key: str) -> str:
    if key.startswith("causal_memory_avoid_tool:"):
        return "causal_memory_avoid_tool"
    if key.startswith("causal_memory_contract:"):
        return "causal_memory_contract"
    if key.startswith("inspect_requested_path:"):
        return "inspect_requested_path"
    return key.split(":", 1)[0]


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    failures = sum(not item["success"] for item in results)
    invalid_results = [
        item
        for item in results
        if item["semantic_event"]["expected_behavior"] == "avoid_known_failure"
    ]
    families = [
        _decision_family(str(item.get("selected_key") or ""))
        for item in invalid_results
    ]
    dominant = Counter(families).most_common(1)[0][1] if families else 0
    return {
        "tasks": total,
        "success_rate": sum(item["success"] for item in results) / total if total else 0.0,
        "failure_recurrence": max(0, failures - 1),
        "wrong_tool_rate": sum(item["wrong_tool"] for item in results) / total if total else 0.0,
        "rollback_rate": 0.0,
        "prediction_confirmation_rate": (
            sum(item["prediction_confirmed"] for item in results) / total
            if total
            else 0.0
        ),
        "decision_stability": dominant / len(families) if families else 0.0,
        "task_completion": (
            sum(item["task_completed"] for item in results) / total if total else 0.0
        ),
        "failures": failures,
    }


def run_controlled_ablation(root: Path) -> dict[str, Any]:
    enabled = _make_runtime(root / "enabled", "enabled")
    disabled = _make_runtime(root / "disabled", "disabled")
    _add_advantage_memory(enabled)
    _add_advantage_memory(disabled)

    enabled_valid = enabled.config.sandbox_path / "valid-control"
    disabled_valid = disabled.config.sandbox_path / "valid-control"
    enabled_valid.mkdir(parents=True)
    disabled_valid.mkdir(parents=True)
    (enabled_valid / "evidence.txt").write_text("verified", encoding="utf-8")
    (disabled_valid / "evidence.txt").write_text("verified", encoding="utf-8")
    enabled_missing = enabled.config.sandbox_path / "known-missing"
    disabled_missing = disabled.config.sandbox_path / "known-missing"

    tasks: list[_AblationTask] = [
        {
            "task_id": "missing-001",
            "project_id": "project-et003-ablation",
            "entity_id": "entity-missing-target",
            "failure_signature": "list_directory:missing_path",
            "task_kind": "inspect_known_missing_path",
            "expected_behavior": "avoid_known_failure",
            "path_enabled": enabled_missing,
            "path_disabled": disabled_missing,
        },
        {
            "task_id": "missing-002",
            "project_id": "project-et003-ablation",
            "entity_id": "entity-missing-target",
            "failure_signature": "list_directory:missing_path",
            "task_kind": "inspect_known_missing_path",
            "expected_behavior": "avoid_known_failure",
            "path_enabled": enabled_missing,
            "path_disabled": disabled_missing,
        },
        {
            "task_id": "missing-003-after-restart",
            "project_id": "project-et003-ablation",
            "entity_id": "entity-missing-target",
            "failure_signature": "list_directory:missing_path",
            "task_kind": "inspect_known_missing_path",
            "expected_behavior": "avoid_known_failure",
            "path_enabled": enabled_missing,
            "path_disabled": disabled_missing,
        },
        {
            "task_id": "valid-control-after-restart",
            "project_id": "project-et003-ablation",
            "entity_id": "entity-valid-control",
            "failure_signature": "none",
            "task_kind": "inspect_valid_path",
            "expected_behavior": "inspect_valid_path",
            "path_enabled": enabled_valid,
            "path_disabled": disabled_valid,
        },
    ]
    enabled_results: list[dict[str, Any]] = []
    disabled_results: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        after_restart = index >= 2
        if index == 2:
            enabled_config = enabled.config
            disabled_config = disabled.config
            enabled.close()
            disabled.close()
            enabled = LivingSystem(enabled_config)
            disabled = LivingSystem(disabled_config)
        enabled_results.append(
            _run_task(
                enabled,
                task_id=task["task_id"],
                path=task["path_enabled"],
                project_id=task["project_id"],
                entity_id=task["entity_id"],
                failure_signature=task["failure_signature"],
                task_kind=task["task_kind"],
                expected_behavior=task["expected_behavior"],
                after_restart=after_restart,
            )
        )
        disabled_results.append(
            _run_task(
                disabled,
                task_id=task["task_id"],
                path=task["path_disabled"],
                project_id=task["project_id"],
                entity_id=task["entity_id"],
                failure_signature=task["failure_signature"],
                task_kind=task["task_kind"],
                expected_behavior=task["expected_behavior"],
                after_restart=after_restart,
            )
        )

    enabled_metrics = _metrics(enabled_results)
    disabled_metrics = _metrics(disabled_results)
    differences = [
        {
            "task_id": left["task_id"],
            "memory_enabled_key": left["selected_key"],
            "memory_disabled_key": right["selected_key"],
            "memory_enabled_success": left["success"],
            "memory_disabled_success": right["success"],
            "attributed_memory_ids": left["selected_memory_ids"],
            "different": left["selected_key"] != right["selected_key"],
        }
        for left, right in zip(enabled_results, disabled_results, strict=True)
    ]
    restart_enabled = next(
        item for item in enabled_results if item["task_id"] == "missing-003-after-restart"
    )
    restart_disabled = next(
        item for item in disabled_results if item["task_id"] == "missing-003-after-restart"
    )
    control_enabled = enabled_results[-1]
    control_disabled = disabled_results[-1]
    regressions = int(
        not control_enabled["task_completed"]
        or not control_disabled["task_completed"]
        or control_enabled["tool"] != control_disabled["tool"]
    )
    attributed_ids = sorted(
        {
            memory_id
            for item in enabled_results
            for memory_id in item["selected_memory_ids"]
        }
    )
    passed = bool(
        enabled_metrics["success_rate"] > disabled_metrics["success_rate"]
        and enabled_metrics["failure_recurrence"] < disabled_metrics["failure_recurrence"]
        and enabled_metrics["wrong_tool_rate"] < disabled_metrics["wrong_tool_rate"]
        and enabled_metrics["task_completion"] > disabled_metrics["task_completion"]
        and ADVANTAGE_MEMORY_ID in attributed_ids
        and restart_enabled["task_completed"]
        and not restart_disabled["task_completed"]
        and regressions == 0
    )
    enabled_memory_state = enabled.memories.memory_state(ADVANTAGE_MEMORY_ID)
    disabled_memory_state = disabled.memories.memory_state(ADVANTAGE_MEMORY_ID)
    enabled.close()
    disabled.close()
    return {
        "passed": passed,
        "memory_enabled": {
            **enabled_metrics,
            "results": enabled_results,
            "memory_state": enabled_memory_state,
        },
        "memory_disabled_baseline": {
            **disabled_metrics,
            "results": disabled_results,
            "memory_state": disabled_memory_state,
        },
        "decision_differences": differences,
        "attributed_memory_ids": attributed_ids,
        "restart_continuity": {
            "memory_enabled": restart_enabled,
            "memory_disabled": restart_disabled,
            "advantage_preserved": restart_enabled["task_completed"]
            and not restart_disabled["task_completed"],
        },
        "regressions": regressions,
        "event_stream_equivalent": all(
            left["semantic_event_sha256"] == right["semantic_event_sha256"]
            for left, right in zip(enabled_results, disabled_results, strict=True)
        ),
    }


def run_refutation_probe(root: Path) -> dict[str, Any]:
    runtime = _make_runtime(root / "refutation", "enabled")
    _add_refutation_memory(runtime)
    target = runtime.config.sandbox_path / "valid-target"
    target.mkdir(parents=True)
    (target / "evidence.txt").write_text("verified", encoding="utf-8")
    common: _RefutationTask = {
        "path": target,
        "project_id": "project-et003-refutation",
        "entity_id": "entity-valid-target",
        "failure_signature": "contract:legacy_listing",
        "task_kind": "inspect_valid_path_with_legacy_contract",
        "expected_behavior": "inspect_valid_path",
    }
    first = _run_task(runtime, task_id="refute-001", after_restart=False, **common)
    after_first = runtime.memories.memory_state(REFUTED_MEMORY_ID)
    second = _run_task(runtime, task_id="refute-002", after_restart=False, **common)
    after_second = runtime.memories.memory_state(REFUTED_MEMORY_ID)
    runtime_config = runtime.config
    runtime.close()
    restarted = LivingSystem(runtime_config)
    third = _run_task(
        restarted,
        task_id="refute-003-after-restart",
        after_restart=True,
        **common,
    )
    after_restart = restarted.memories.memory_state(REFUTED_MEMORY_ID)
    passed = bool(
        not first["success"]
        and after_first["validity_state"] == "WEAKENED"
        and not second["success"]
        and after_second["validity_state"] == "REFUTED"
        and after_restart["validity_state"] == "REFUTED"
        and REFUTED_MEMORY_ID in third["suppressed_memory_ids"]
        and third["task_completed"]
        and not third["memory_changed_decision"]
    )
    restarted.close()
    return {
        "passed": passed,
        "memory_id": REFUTED_MEMORY_ID,
        "first_failure": first,
        "state_after_first": after_first,
        "second_failure": second,
        "state_after_second": after_second,
        "restart_decision": third,
        "state_after_restart": after_restart,
        "suppression_preserved": REFUTED_MEMORY_ID in third["suppressed_memory_ids"],
    }


def run_evolution_target_003(root: Path) -> dict[str, Any]:
    ablation = run_controlled_ablation(root / "ablation")
    refutation = run_refutation_probe(root / "refutation-probe")
    return {
        "passed": bool(ablation["passed"] and refutation["passed"]),
        **ablation,
        "refutation": refutation,
        "refuted_memory_ids": [REFUTED_MEMORY_ID] if refutation["passed"] else [],
    }
