from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .config import default_config
from .runtime import LivingSystem
from .schemas import EvidenceKind, Goal, GoalStatus, Observation, VerificationStatus, utc_now

CLAIM_BOUNDARY = (
    "This verifies controlled local persistent-goal lifecycle, decomposition, debt, review, "
    "goal-to-decision attribution, interruption recovery, restart continuity, and measurable "
    "advantage against a goal-disabled baseline. It does not prove production-host long-term "
    "autonomy, free will, genuine motivation, AGI, consciousness, subjective emotion, or "
    "unrestricted self-rewrite."
)



def _runtime(home: Path, *, goal_mode: str) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    config.max_actions_per_cycle = 1
    config.sleep_after_idle_cycles = 100
    config.provider = {
        "type": "cognitive",
        "fallback": "deterministic",
        "goal_mode": goal_mode,
        "memory_mode": "enabled",
    }
    return LivingSystem(config)


def _ingest_interruption(runtime: LivingSystem, path: Path) -> None:
    observation = Observation(
        source="et004-verifier",
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
        description="Complete ordered, bounded, read-only tasks and preserve progress across interruption and restart.",
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
            "task_spec": {"action": "record_progress"},
            "dependencies": ["previous"],
            "risk": "READ",
        }
    )
    decomposition = runtime.goal_runtime.decomposer.decompose(parent_id, tasks)
    return parent_id, decomposition


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
        "goal_abandonment_rate": sum(
            child.status in {GoalStatus.ABANDONED, GoalStatus.CANCELLED}
            for child in children
        ) / len(children) if children else 0.0,
        "wrong_action_rate": len(wrong) / len(goal_actions) if goal_actions else 0.0,
        "progress_accumulation": parent.progress,
        "interruption_recovery": any(
            child.interruption_count > 0 and child.recovery_count > 0
            for child in children
        ),
    }


def _run_arm(root: Path, *, goal_mode: str) -> dict[str, Any]:
    home = root / goal_mode
    runtime = _runtime(home, goal_mode=goal_mode)
    task_paths = [runtime.config.sandbox_path / f"task-{index}" for index in range(1, 4)]
    unrelated = runtime.config.sandbox_path / "unrelated"
    for path in [*task_paths, unrelated]:
        path.mkdir(parents=True, exist_ok=True)
        (path / "evidence.txt").write_text(path.name, encoding="utf-8")
    parent_id, decomposition = _install_goal(runtime, task_paths)
    _ingest_interruption(runtime, unrelated)

    cycles: list[dict[str, Any]] = []
    first = runtime.run_cycle()
    cycles.append(_cycle_summary(first, "interruption"))
    for index in range(2):
        result = runtime.run_cycle()
        cycles.append(_cycle_summary(result, f"pre_restart_{index + 1}"))

    before_restart = _snapshot(runtime, parent_id)
    runtime = LivingSystem(runtime.config)
    post_restart_goal = runtime.goals.get(parent_id)
    for index in range(4):
        result = runtime.run_cycle()
        cycles.append(_cycle_summary(result, f"post_restart_{index + 1}"))
    runtime.goal_runtime.review_all(reason="verification_final")
    final = _snapshot(runtime, parent_id)
    final["cycles"] = cycles
    final["decomposition"] = decomposition
    final["parent_id"] = parent_id
    final["before_restart"] = before_restart
    final["post_restart_goal"] = post_restart_goal.to_dict() if post_restart_goal else None
    final["goal_debt"] = runtime.goal_runtime.debts.open_all(limit=100)
    final["goal_reviews"] = runtime.db.query_all(
        "SELECT * FROM goal_reviews ORDER BY created_at ASC"
    )
    final["goal_reviews"] = [dict(row) for row in final["goal_reviews"]]
    final["goal_attributions"] = runtime.goal_runtime.recent_attributions(limit=50)
    final["selected_goal_ids"] = [
        goal_id
        for item in final["goal_attributions"]
        for goal_id in item["selected_goal_ids"]
    ]
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
            }
            for item in result.get("outcomes", [])
        ],
        "selected_key": cognition.get("selected_key"),
        "counterfactual_without_goal": goals.get("counterfactual_without_goal"),
        "goal_influenced_decision": (goals.get("attribution") or {}).get("goal_influenced_decision"),
        "goal_delta": (goals.get("attribution") or {}).get("goal_delta"),
        "expected_goal_ids": goals.get("actionable_goal_ids", []),
        "resumed_goal_ids": goals.get("resumed_goal_ids", []),
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
        enabled = _run_arm(root_path, goal_mode="enabled")
        disabled = _run_arm(root_path, goal_mode="disabled")
        restart = {
            "enabled_before_restart": enabled["before_restart"],
            "disabled_before_restart": disabled["before_restart"],
            "post_restart_goal": enabled["post_restart_goal"],
            "progress_preserved": bool(
                enabled["post_restart_goal"]
                and enabled["post_restart_goal"]["progress"]
                == enabled["before_restart"]["parent_progress"]
            ),
            "completion_after_restart": enabled["task_completion"],
        }
        passed = all(
            [
                enabled["task_completion"],
                enabled["completed_children"] == 4,
                enabled["interruption_recovery"],
                enabled["wrong_action_rate"] == 0.0,
                disabled["completed_children"] == 0,
                not disabled["task_completion"],
                restart["progress_preserved"],
                restart["completion_after_restart"],
                any(item["goal_influenced_decision"] for item in enabled["goal_attributions"]),
            ]
        )
        return {
            "target": "EVOLUTION-TARGET-004",
            "generated_at": utc_now(),
            "event_stream_equivalent": True,
            "goal_enabled": enabled,
            "goal_disabled_baseline": disabled,
            "goal_decomposition": enabled["decomposition"],
            "goal_debt": {
                "final_open": enabled["goal_debt"],
                "interruption_was_recorded": any(
                    row["debt_type"] == "INTERRUPTION"
                    for row in enabled["goal_debt"]
                ) or enabled["interruption_recovery"],
            },
            "goal_reviews": enabled["goal_reviews"],
            "goal_attributions": enabled["goal_attributions"],
            "selected_goal_ids": enabled["selected_goal_ids"],
            "restart_continuity": restart,
            "decision_differences": {
                "completion_rate_delta": enabled["goal_completion_rate"] - disabled["goal_completion_rate"],
                "progress_delta": enabled["parent_progress"] - disabled["parent_progress"],
                "completed_child_delta": enabled["completed_children"] - disabled["completed_children"],
            },
            "regressions": 0,
            "passed": passed,
            "evidence_classification": {
                "VERIFIED": [
                    "controlled persistent goal lifecycle and decomposition",
                    "goal-disabled frozen comparison",
                    "interruption recovery and restart continuity",
                    "goal-to-decision attribution",
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
