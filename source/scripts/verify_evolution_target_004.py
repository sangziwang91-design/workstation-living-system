import sys
import json
import tempfile
from pathlib import Path

# Add source/src to sys.path
source_root = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(source_root / "src"))

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import Goal, GoalStatus, RiskLevel

def test_et004_full_lifecycle():
    print("ET004: Testing goal decomposition, review, and debt transition...")
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        config.secret_path.write_bytes(b"a" * 32)
        home.joinpath("secrets").mkdir(parents=True, exist_ok=True)
        home.joinpath("secrets/approval.key").write_bytes(b"b" * 32)

        runtime = LivingSystem(config)

        # 1. Create a parent goal
        parent = Goal(
            title="Parent Task",
            description="A big task",
            priority=0.8,
            source="user",
            autonomous=False,
            risk=RiskLevel.READ
        )
        parent_id = runtime.goals.add(parent)

        # 2. Decompose it
        tasks = [
            {"title": "Subtask 1", "task_spec": {"action": "noop"}},
            {"title": "Subtask 2", "task_spec": {"action": "noop"}, "dependencies": ["previous"]}
        ]
        runtime.goal_runtime.decomposer.decompose(parent_id, tasks)

        # Verify status of parent
        updated_parent = runtime.goals.get(parent_id)
        assert updated_parent.status == GoalStatus.DECOMPOSED

        # Verify subgoals
        children = runtime.goals.children(parent_id)
        assert len(children) == 2
        assert children[0].status == GoalStatus.ACTIVE
        assert children[1].status == GoalStatus.WAITING

        # 3. Test integrity with negative case
        ok, counts = runtime.goal_runtime.integrity()
        assert ok is True

        # Inject orphan debt
        runtime.db.execute(
            """
            INSERT INTO goal_debts (
                debt_id, goal_id, debt_type, reason, severity,
                source_ids_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("debt1", "nonexistent", "test", "reason", 0.5, "[]", "OPEN", "now")
        )
        ok, counts = runtime.goal_runtime.integrity()
        assert ok is False
        assert counts["orphan_goal_debts"] == 1

    print("ET004: Lifecycle test PASS")

def main():
    try:
        test_et004_full_lifecycle()
        print("ET004 Baseline: PASS")
        sys.exit(0)
    except Exception as e:
        print(f"ET004 Baseline: FAIL - {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
