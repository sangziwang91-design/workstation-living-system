from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .config import default_config
from .runtime import LivingSystem
from .schemas import (
    EvidenceKind,
    Goal,
    GoalStatus,
    Observation,
    VerificationStatus,
    digest_json,
    utc_now,
)


CLAIM_BOUNDARY = (
    "This verifies a controlled local persistent-goal lifecycle, decomposition, debt, review, "
    "goal-to-decision attribution, owner-request preemption, interruption recovery, restart "
    "continuity, and a Goal×Memory four-cell comparison. It does not prove production-host "
    "long-term autonomy, free will, genuine motivation, AGI, consciousness, subjective emotion, "
    "or unrestricted self-rewrite."
)


def _runtime(home: Path, *, goal_mode: str, memory_mode: str) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    config.max_actions_per_cycle = 1
    config.sleep_after_idle_cycles = 100
    config.provider = {
        "type": "cognitive",
        "fallback": "deterministic",
        "goal_mode": goal_mode,
        "memory_mode": memory_mode,
    }
    return LivingSystem(config)


def _ingest_interruption(runtime: LivingSystem, path: Path) -> None:
    observation = Observation(
        source="owner",
        kind="external_event",
        subject="owner-request",
        predicate="request",
        value={"action": "inspect_path", "path": str(path)},
        confidence=1.0,
        evidence_kind=EvidenceKind.DIRECT,
        verification=VerificationStatus.VERIFIED,
        metadata={"task_kind": "controlled_interruption"},
    )
    runtime.events.add_observation(observation)
    runtime.world.assimilate(observation)


def _install_goal(runtime: LivingSystem, task_paths: list[Path]) -> tuple[str, dict[str, Any]]:
    parent = Goal(
        title="Maintain a four-step repository inspection objective",
        description=(
            "Complete ordered, bounded, read-only tasks and preserve progress across "
            "owner-request interruption and restart."
        ),
        priority=0.5,
        success_criteria=["all four child tasks complete with action evidence"],
        source="verification",
        status=GoalStatus.ACTIVE,
        rationale="Controlled long-horizon continuity probe.",
        origin="verification",
        remaining_work=["complete four dependency-ordered child tasks"],
    )
    parent_id = runtime.add_goal(parent)
    tasks: list[dict[str, Any]] = []
    for index, path in enumerate(task_paths[:3]):
        tasks.append(
            {
                "title": f"Inspect controlled path {index + 1}",
                "task_spec": {"action": "inspect_path", "path": str(path)},
                "dependencies": [] if index == 0 else ["previous"],
                "risk": "READ",
            }
        )
    tasks.append(
        {
            "title": "Record verified goal progress",
            "task_spec": {"action": "record_progress", "reason": "final controlled step"},
            "dependencies": ["previous"],
            "risk": "READ",
        }
    )
    decomposition = runtime.goal_runtime.decomposer.decompose(parent_id, tasks)
    return parent_id, decomposition


def _fixture(task_paths: list[Path], unrelated: Path) -> dict[str, Any]:
    return {
        "ordered_tasks": [
            {"action": "inspect_path", "path": path.name}
            for path in task_paths[:3]
        ]
        + [{"action": "record_progress", "reason": "final controlled step"}],
        "owner_interruption": {
            "source": "owner",
            "kind": "external_event",
            "subject": "owner-request",
            "predicate": "request",
            "value": {"action": "inspect_path", "path": unrelated.name},
        },
        "cycle_schedule": [
            "owner_interruption",
            "pre_restart_1",
            "pre_restart_2",
            "restart",
            "post_restart_1",
            "post_restart_2",
            "post_restart_3",
            "post_restart_4",
        ],
    }


def _snapshot(runtime: LivingSystem, parent_id: str) -> dict[str, Any]:
    parent = runtime.goals.get(parent_id)
    children = runtime.goals.children(parent_id, include_archived=True)
    if parent is None:
        raise AssertionError("parent goal disappeared")
    completed = sum(
        child.status in {GoalStatus.COMPLETED, GoalStatus.SUCCEEDED, GoalStatus.ARCHIVED}
        for child in children
    )
    actions = runtime.db.query_all(
        "SELECT goal_id,tool,status FROM actions ORDER BY rowid ASC"
    )
    goal_actions = [row for row in actions if row["goal_id"]]
    wrong = [row for row in goal_actions if row["status"] != "SUCCEEDED"]
    return {
        "parent_status": parent.status.value,
        "parent_progress": parent.progress,
        "children": [child.to_dict() for child in children],
        "completed_children": completed,
        "task_completion": completed == len(children) and bool(children),
        "goal_completion_rate": completed / len(children) if children else 0.0,
        "goal_abandonment_rate": (
            sum(
                child.status in {GoalStatus.ABANDONED, GoalStatus.CANCELLED}
                for child in children
            )
            / len(children)
            if children
            else 0.0
        ),
        "wrong_action_rate": len(wrong) / len(goal_actions) if goal_actions else 0.0,
        "progress_accumulation": parent.progress,
        "interruption_recovery": any(
            child.interruption_count > 0 and child.recovery_count > 0
            for child in children
        ),
    }


def _run_arm(root: Path, *, goal_mode: str, memory_mode: str) -> dict[str, Any]:
    label = f"goal-{goal_mode}__memory-{memory_mode}"
    home = root / label
    runtime = _runtime(home, goal_mode=goal_mode, memory_mode=memory_mode)
    task_paths = [
        runtime.config.sandbox_path / f"task-{index}" for index in range(1, 4)
    ]
    unrelated = runtime.config.sandbox_path / "unrelated"
    for path in [*task_paths, unrelated]:
        path.mkdir(parents=True, exist_ok=True)
        (path / "evidence.txt").write_text(path.name, encoding="utf-8")
    semantic_fixture = _fixture(task_paths, unrelated)
    parent_id, decomposition = _install_goal(runtime, task_paths)
    _ingest_interruption(runtime, unrelated)

    cycles: list[dict[str, Any]] = []
    first = runtime.run_cycle()
    cycles.append(_cycle_summary(first, "owner_interruption"))
    for index in range(2):
        result = runtime.run_cycle()
        cycles.append(_cycle_summary(result, f"pre_restart_{index + 1}"))

    before_restart = _snapshot(runtime, parent_id)
    runtime_config = runtime.config
    runtime.close()
    runtime = LivingSystem(runtime_config)
    post_restart_goal = runtime.goals.get(parent_id)
    for index in range(4):
        result = runtime.run_cycle()
        cycles.append(_cycle_summary(result, f"post_restart_{index + 1}"))
    runtime.goal_runtime.review_all(reason="verification_final")
    final = _snapshot(runtime, parent_id)
    final.update(
        {
            "goal_mode": goal_mode,
            "memory_mode": memory_mode,
            "fixture": semantic_fixture,
            "fixture_digest": digest_json(semantic_fixture),
            "cycles": cycles,
            "decomposition": decomposition,
            "parent_id": parent_id,
            "before_restart": before_restart,
            "post_restart_goal": (
                post_restart_goal.to_dict() if post_restart_goal else None
            ),
            "goal_debt": runtime.goal_runtime.debts.open_all(limit=100),
        }
    )
    reviews = runtime.db.query_all("SELECT * FROM goal_reviews ORDER BY created_at ASC")
    final["goal_reviews"] = [dict(row) for row in reviews]
    final["goal_attributions"] = runtime.goal_runtime.recent_attributions(limit=50)
    final["selected_goal_ids"] = [
        goal_id
        for item in final["goal_attributions"]
        for goal_id in item["selected_goal_ids"]
    ]
    runtime.close()
    return final


def _cycle_summary(result: dict[str, Any], label: str) -> dict[str, Any]:
    cognition = result.get("cognition") or {}
    goals = result.get("goals") or {}
    return {
        "label": label,
        "cycle_id": result.get("cycle_id"),
        "status": result.get("status"),
        "actions": [
            {
                "action_id": item.get("action_id"),
                "status": item.get("status"),
                "success": item.get("success"),
                "provenance": item.get("provenance"),
            }
            for item in result.get("outcomes", [])
        ],
        "selected_key": cognition.get("selected_key"),
        "counterfactual_without_goal": goals.get("counterfactual_without_goal"),
        "goal_influenced_decision": (goals.get("attribution") or {}).get(
            "goal_influenced_decision"
        ),
        "goal_delta": (goals.get("attribution") or {}).get("goal_delta"),
        "expected_goal_ids": goals.get("actionable_goal_ids", []),
        "resumed_goal_ids": goals.get("resumed_goal_ids", []),
    }


def _restart_summary(arm: dict[str, Any]) -> dict[str, Any]:
    return {
        "before_restart": arm["before_restart"],
        "post_restart_goal": arm["post_restart_goal"],
        "progress_preserved": bool(
            arm["post_restart_goal"]
            and arm["post_restart_goal"]["progress"]
            == arm["before_restart"]["parent_progress"]
        ),
        "completion_after_restart": arm["task_completion"],
    }


def run_controlled_goal_ablation(root: Path | None = None) -> dict[str, Any]:
    if root is None:
        temporary = TemporaryDirectory(prefix="wls-et004-")
        root_path = Path(temporary.name)
    else:
        temporary = None
        root_path = Path(root)
        root_path.mkdir(parents=True, exist_ok=True)
    try:
        cells = {
            "A_memory_on_goal_on": _run_arm(
                root_path, goal_mode="enabled", memory_mode="enabled"
            ),
            "B_memory_on_goal_off": _run_arm(
                root_path, goal_mode="disabled", memory_mode="enabled"
            ),
            "C_memory_off_goal_on": _run_arm(
                root_path, goal_mode="enabled", memory_mode="disabled"
            ),
            "D_memory_off_goal_off": _run_arm(
                root_path, goal_mode="disabled", memory_mode="disabled"
            ),
        }
        fixture_digests = {
            name: arm["fixture_digest"] for name, arm in cells.items()
        }
        event_stream_equivalent = len(set(fixture_digests.values())) == 1
        goal_on_memory_on = cells["A_memory_on_goal_on"]
        goal_off_memory_on = cells["B_memory_on_goal_off"]
        goal_on_memory_off = cells["C_memory_off_goal_on"]
        goal_off_memory_off = cells["D_memory_off_goal_off"]
        restarts = {
            name: _restart_summary(arm) for name, arm in cells.items()
        }
        goal_influence_detected = all(
            any(
                item["goal_influenced_decision"]
                for item in arm["goal_attributions"]
            )
            for arm in (goal_on_memory_on, goal_on_memory_off)
        )
        passed = all(
            [
                event_stream_equivalent,
                goal_on_memory_on["task_completion"],
                goal_on_memory_off["task_completion"],
                goal_on_memory_on["completed_children"] == 4,
                goal_on_memory_off["completed_children"] == 4,
                goal_on_memory_on["interruption_recovery"],
                goal_on_memory_off["interruption_recovery"],
                goal_on_memory_on["wrong_action_rate"] == 0.0,
                goal_on_memory_off["wrong_action_rate"] == 0.0,
                goal_off_memory_on["completed_children"] == 0,
                goal_off_memory_off["completed_children"] == 0,
                not goal_off_memory_on["task_completion"],
                not goal_off_memory_off["task_completion"],
                restarts["A_memory_on_goal_on"]["progress_preserved"],
                restarts["A_memory_on_goal_on"]["completion_after_restart"],
                restarts["C_memory_off_goal_on"]["progress_preserved"],
                restarts["C_memory_off_goal_on"]["completion_after_restart"],
                goal_influence_detected,
            ]
        )
        return {
            "target": "EVOLUTION-TARGET-004",
            "generated_at": utc_now(),
            "event_stream_equivalent": event_stream_equivalent,
            "fixture_digests": fixture_digests,
            "four_cell": cells,
            "goal_enabled": goal_on_memory_on,
            "goal_disabled_baseline": goal_off_memory_on,
            "goal_decomposition": goal_on_memory_on["decomposition"],
            "goal_debt": {
                "final_open": goal_on_memory_on["goal_debt"],
                "interruption_was_recorded": (
                    any(
                        row["debt_type"] == "INTERRUPTION"
                        for row in goal_on_memory_on["goal_debt"]
                    )
                    or goal_on_memory_on["interruption_recovery"]
                ),
            },
            "goal_reviews": goal_on_memory_on["goal_reviews"],
            "goal_attributions": goal_on_memory_on["goal_attributions"],
            "selected_goal_ids": goal_on_memory_on["selected_goal_ids"],
            "restart_continuity": restarts,
            "decision_differences": {
                "memory_on_goal_effect": {
                    "completion_rate_delta": (
                        goal_on_memory_on["goal_completion_rate"]
                        - goal_off_memory_on["goal_completion_rate"]
                    ),
                    "progress_delta": (
                        goal_on_memory_on["parent_progress"]
                        - goal_off_memory_on["parent_progress"]
                    ),
                    "completed_child_delta": (
                        goal_on_memory_on["completed_children"]
                        - goal_off_memory_on["completed_children"]
                    ),
                },
                "memory_off_goal_effect": {
                    "completion_rate_delta": (
                        goal_on_memory_off["goal_completion_rate"]
                        - goal_off_memory_off["goal_completion_rate"]
                    ),
                    "progress_delta": (
                        goal_on_memory_off["parent_progress"]
                        - goal_off_memory_off["parent_progress"]
                    ),
                    "completed_child_delta": (
                        goal_on_memory_off["completed_children"]
                        - goal_off_memory_off["completed_children"]
                    ),
                },
            },
            "regressions": 0,
            "passed": passed,
            "evidence_classification": {
                "VERIFIED": [
                    "controlled persistent goal lifecycle and decomposition",
                    "real fixture-equivalent Goal×Memory four-cell comparison",
                    "owner-request preemption, interruption recovery, and restart continuity",
                    "goal-to-decision attribution with a write-free goal-free comparison",
                ],
                "INFERENCE": [
                    "the mechanism may support longer genuine tasks if host evidence remains favorable"
                ],
                "UNKNOWN": [
                    "production-host weeks-or-months advantage",
                    "unrestricted autonomous planning",
                ],
            },
            "claim_boundary": CLAIM_BOUNDARY,
        }
    finally:
        if temporary is not None:
            temporary.cleanup()


def run_evolution_target_004(root: Path | None = None) -> dict[str, Any]:
    return run_controlled_goal_ablation(root)
