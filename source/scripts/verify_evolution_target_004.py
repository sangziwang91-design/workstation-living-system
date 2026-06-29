from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import sys

from wls.config import default_config
from wls.goal_ablation import run_evolution_target_004
from wls.runtime import LivingSystem
from wls.schemas import Goal, GoalStatus


def _runtime(home: Path) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    config.max_actions_per_cycle = 1
    config.sleep_after_idle_cycles = 100
    config.provider.update(
        {"type": "cognitive", "goal_mode": "enabled", "memory_mode": "enabled"}
    )
    return LivingSystem(config)


def _focused_task_spec_probe(root: Path) -> dict:
    runtime = _runtime(root / "task-spec")
    target = runtime.config.sandbox_path / "controlled-target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "evidence.txt").write_text("bounded", encoding="utf-8")
    parent = Goal(
        title="Generic durable objective",
        description="Complete a bounded child whose actionable target exists only in task_spec.",
        priority=0.8,
        success_criteria=["child action succeeds with canonical evidence"],
    )
    parent_id = runtime.add_goal(parent)
    decomposition = runtime.goal_runtime.decomposer.decompose(
        parent_id,
        [
            {
                "title": "Generic child",
                "description": "No path or tool appears in this text.",
                "task_spec": {"action": "inspect_path", "path": str(target)},
            }
        ],
    )
    child_id = decomposition["subgoal_ids"][0]
    cycle = runtime.run_cycle()
    action = runtime.db.query_one(
        "SELECT action_id,goal_id,tool,status FROM actions WHERE goal_id=? ORDER BY rowid DESC LIMIT 1",
        (child_id,),
    )
    child = runtime.goals.get(child_id)
    attribution = runtime.db.query_one(
        "SELECT counterfactual_json,goal_influenced_decision,goal_delta FROM goal_attributions ORDER BY created_at DESC LIMIT 1"
    )
    if action is None or child is None or attribution is None:
        raise AssertionError("task_spec probe did not persist the expected action/goal/attribution")
    counterfactual = json.loads(attribution["counterfactual_json"])
    outcomes = cycle.get("outcomes", [])
    if action["tool"] != "list_directory" or action["status"] != "SUCCEEDED":
        raise AssertionError(f"task_spec did not execute canonically: {dict(action)}")
    if action["goal_id"] != child_id:
        raise AssertionError("task_spec action lost child goal provenance")
    if child.status not in {GoalStatus.COMPLETED, GoalStatus.SUCCEEDED, GoalStatus.ARCHIVED}:
        raise AssertionError(f"child did not complete from canonical evidence: {child.status}")
    if counterfactual.get("method") != "WRITE_FREE_BOUNDED_COGNITIVE_RANK":
        raise AssertionError(f"counterfactual was not independently ranked: {counterfactual}")
    if not any(item.get("provenance") == "EXECUTED_CURRENT_ACTION" for item in outcomes):
        raise AssertionError("current action outcome provenance was not recorded")

    invalid_rejected = False
    invalid_runtime = _runtime(root / "invalid-spec")
    try:
        invalid_parent = invalid_runtime.add_goal(
            Goal(title="Invalid specification parent", description="negative control")
        )
        try:
            invalid_runtime.goal_runtime.decomposer.decompose(
                invalid_parent,
                [{"title": "invalid child", "task_spec": {"action": "shell"}}],
            )
        except ValueError:
            invalid_rejected = True
    finally:
        invalid_runtime.close()
    if not invalid_rejected:
        raise AssertionError("unknown task_spec action was accepted")

    traversal_runtime = _runtime(root / "path-policy")
    try:
        traversal_parent = traversal_runtime.add_goal(
            Goal(title="Traversal specification parent", description="negative control")
        )
        forbidden_root = Path(traversal_runtime.config.home_path.anchor or os.sep)
        traversal_runtime.goal_runtime.decomposer.decompose(
            traversal_parent,
            [
                {
                    "title": "escape child",
                    "task_spec": {"action": "inspect_path", "path": str(forbidden_root)},
                }
            ],
        )
        traversal_cycle = traversal_runtime.run_cycle()
        traversal_rejected = any(
            item.get("status") == "REJECTED"
            for item in traversal_cycle.get("outcomes", [])
        )
    finally:
        traversal_runtime.close()
    if not traversal_rejected:
        raise AssertionError("path outside configured roots was not rejected by canonical policy")

    integrity_ok, integrity = runtime.goal_runtime.integrity()
    if not integrity_ok:
        raise AssertionError(f"goal integrity failed before negative mutation: {integrity}")
    runtime.db.execute(
        """
        INSERT INTO goal_debts(
            debt_id,goal_id,debt_type,reason,severity,source_ids_json,status,created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        ("negative-orphan", "missing-goal", "TEST", "negative control", 0.5, "[]", "OPEN", "now"),
    )
    negative_ok, negative_integrity = runtime.goal_runtime.integrity()
    if negative_ok or negative_integrity.get("orphan_goal_debts") != 1:
        raise AssertionError("goal integrity verifier failed to reject orphan debt")
    result = {
        "parent_id": parent_id,
        "child_id": child_id,
        "action": dict(action),
        "child_status": child.status.value,
        "counterfactual": counterfactual,
        "goal_influenced_decision": bool(attribution["goal_influenced_decision"]),
        "goal_delta": float(attribution["goal_delta"]),
        "outcome_provenance": [item.get("provenance") for item in outcomes],
        "invalid_task_spec_rejected": invalid_rejected,
        "path_escape_rejected": traversal_rejected,
        "negative_integrity": negative_integrity,
    }
    runtime.close()
    return result


def main() -> int:
    with TemporaryDirectory(prefix="wls-et004-authoritative-") as tmp:
        root = Path(tmp)
        report = run_evolution_target_004(root / "ablation")
        if not report.get("passed"):
            print(json.dumps(report, indent=2, default=str))
            return 1
        focused = _focused_task_spec_probe(root)
        output = {
            "target": "EVOLUTION-TARGET-004",
            "passed": True,
            "goal_enabled_vs_disabled_ablation": report,
            "focused_task_spec_probe": focused,
            "verified": [
                "persistent parent/child lifecycle",
                "dependency-ordered bounded task execution",
                "interruption and runtime restart continuity",
                "goal-disabled comparison",
                "schema-validated task_spec execution through canonical policy/tools",
                "write-free goal-free counterfactual",
                "outcome provenance",
                "negative integrity, invalid-spec, and path-policy rejection",
            ],
            "unknown": [
                "owner-host weeks-or-months advantage",
                "unrestricted autonomous planning",
                "production utility",
            ],
            "claim_ceiling": "controlled local persistent-goal implementation with discriminating negative controls",
        }
        print(json.dumps(output, indent=2, sort_keys=True, default=str))
        return 0


if __name__ == "__main__":
    sys.exit(main())
