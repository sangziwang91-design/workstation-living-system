"""Real Windows read failures -> canonical endogenous learning proposal.

Independent evidence: the target file really does NOT exist on Windows, three
canonical read_file executions each FAILED and were persisted with distinct
action IDs; only then does WLS create its own bounded learning/inquiry goal.
This does not establish self-coding, owner task value, or retained skill gain.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Plan, RiskLevel, new_id, utc_now

SCHEMA = "wls.g1.hosted_windows_external_failure_growth.v1"


def recorded_failure(runtime: LivingSystem, target: Path) -> tuple[str, str]:
    if target.exists():
        raise ValueError("source file unexpectedly exists: not a real failure")
    cycle_id = new_id("cycle")
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    action = ActionSpec(
        tool="read_file",
        arguments={"path": str(target), "max_bytes": 1024},
        purpose="Observe missing Windows fixture evidence using read-only tool",
        expected_result="external file content only if it actually exists",
        risk=RiskLevel.READ,
        acceptance=["output contains text"],
    )
    plan = Plan(rationale="Independently measure actual filesystem availability", actions=[action])
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    outcome = runtime._execute_plan(plan)
    if len(outcome) != 1 or outcome[0].get("success") is not False:
        raise ValueError("expected a genuine failed read_file operation")
    row = runtime.db.query_one(
        "SELECT tool,status,risk,result_json,started_at,finished_at FROM actions "
        "WHERE action_id=?", (action.action_id,),
    )
    if (
        row is None or row["tool"] != "read_file" or row["risk"] != "READ"
        or row["status"] != "FAILED" or row["started_at"] is None
        or row["finished_at"] is None or row["result_json"] is None
    ):
        raise ValueError("canonical failed action was not committed to WLS")
    persisted = json.loads(row["result_json"])
    if persisted.get("evaluation", {}).get("accepted") is not False:
        raise ValueError("missing independent evaluator rejection")
    if persisted.get("result", {}).get("success") is not False:
        raise ValueError("tool did not independently report failure")
    # The tool failure came from a real missing external path, not a fake
    # always-failing acceptance criterion on a successful/noop action.
    if not str(persisted["result"].get("error") or "").strip():
        raise ValueError("tool failed without an external I/O error")
    runtime.db.execute(
        "UPDATE cycles SET status='SUCCEEDED',finished_at=? WHERE cycle_id=?",
        (utc_now(), cycle_id),
    )
    return action.action_id, cycle_id


def execute() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="wls-g1-endogenous-windows-") as tmp:
        home = Path(tmp) / "home"
        config = default_config(home)
        config.sensors = []
        config.max_actions_per_cycle = 0
        runtime = LivingSystem(config)
        target = home / "sandbox" / "evidence-missing.txt"
        if target.exists():
            raise ValueError("negative control target unexpectedly exists")
        source_ids: list[str] = []
        first_two: list[str] = []
        for index in range(3):
            action_id, _cycle_id = recorded_failure(runtime, target)
            source_ids.append(action_id)
            if index == 1:
                assert runtime.learning.create_failure_candidates(max_new_candidates=1) == []
                first_two = source_ids[:]
        if first_two != source_ids[:2]:
            raise ValueError("two-failure negative control not executed")
        if len(set(source_ids)) != 3 or target.exists():
            raise ValueError("failures reused action identity or created absent file")
        # Real WLS lifecycle autonomously evaluates *persisted* failures.
        result = runtime.run_cycle()
        if result.get("status") != "SUCCEEDED":
            raise ValueError("canonical WLS cycle failed while considering real I/O errors")
        candidate = runtime.db.query_one(
            "SELECT candidate_id,source_ids_json FROM evolution_candidates "
            "WHERE candidate_type='failure_repair'"
        )
        if candidate is None:
            raise ValueError("no WLS-created repair candidate from real failures")
        sources = json.loads(candidate["source_ids_json"])
        if len(sources) != 3 or set(sources) != set(source_ids):
            raise ValueError("WLS candidate not grounded in the three actual failed actions")
        goal = runtime.db.query_one(
            "SELECT goal_id,source,autonomous,task_spec_json FROM goals "
            "WHERE source='autonomy.learning'"
        )
        if goal is None or goal["autonomous"] != 1:
            raise ValueError("no independently created autonomous investigation goal")
        task_spec = json.loads(goal["task_spec_json"])
        if (
            task_spec.get("candidate_id") != candidate["candidate_id"]
            or task_spec.get("proposal_only") is not True
            or set(task_spec.get("source_action_ids", [])) != set(source_ids)
        ):
            raise ValueError("autonomous growth goal lacks authentic source attribution")
        canonical = runtime.verify_integrity(full=True)
        if canonical.get("ok") is not True:
            raise ValueError("WLS source evidence chain failed integrity check")
        runtime.db.close_all()

        # The one canonical WLS home must reopen without losing causal
        # provenance or re-proposing an unchanged hypothesis.
        restarted = LivingSystem(config)
        before_candidates = int(restarted.db.query_one(
            "SELECT COUNT(*) AS n FROM evolution_candidates "
            "WHERE candidate_type='failure_repair'"
        )["n"])
        before_goals = int(restarted.db.query_one(
            "SELECT COUNT(*) AS n FROM goals WHERE source='autonomy.learning'"
        )["n"])
        restarted.run_cycle()
        after_candidates = int(restarted.db.query_one(
            "SELECT COUNT(*) AS n FROM evolution_candidates "
            "WHERE candidate_type='failure_repair'"
        )["n"])
        after_goals = int(restarted.db.query_one(
            "SELECT COUNT(*) AS n FROM goals WHERE source='autonomy.learning'"
        )["n"])
        if (
            before_candidates != after_candidates or before_goals != after_goals
            or before_candidates != 1 or before_goals != 1
            or restarted.verify_integrity(full=True).get("ok") is not True
        ):
            raise ValueError("restart lost or duplicated genuine failure-learning evidence")
        with closing(sqlite3.connect(config.db_path)) as db:
            actual = db.execute(
                "SELECT COUNT(*) FROM actions WHERE tool='read_file' "
                "AND status='FAILED'"
            ).fetchone()[0]
        restarted.db.close_all()
        if actual != 3:
            raise ValueError("canonical read failures changed during replay")
        return {
            "schema": SCHEMA,
            "status": "REAL_WINDOWS_FAILURE_TO_AUTONOMOUS_GOAL",
            "failure_tool": "read_file", "external_target_existed": False,
            "independently_failed_real_actions": 3,
            "negative_control_two_failures_created_candidates": 0,
            "source_action_id_digest": hashlib.sha256(
                "\n".join(sorted(source_ids)).encode("utf-8")
            ).hexdigest(),
            "autonomous_growth_goals": 1,
            "candidate_source_references_verified": True,
            "restart_preserves_provenance": True,
            "repeated_cycle_creates_duplicate": False,
            "read_only": True,
            "external_task_repaired": False,
            "model_calls": 0,
            "skill_improvement_proven": False,
            "claim_ceiling": (
                "real_Windows_read_failures_caused_an_endogenous_bounded_goal; "
                "NOT autonomous external repair, model-driven RSI or retained task advantage"
            ),
        }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        report = execute()
    except (OSError, ValueError, TypeError, KeyError, AssertionError, sqlite3.Error) as exc:
        report = {
            "schema": SCHEMA, "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "reason": str(exc)[:220],
            "claim_ceiling": "failed_endogenous_real_windows_qualification",
        }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": report["status"],
                      "real_failed_actions": report.get("independently_failed_real_actions", 0)}))
    return 0 if report["status"] == "REAL_WINDOWS_FAILURE_TO_AUTONOMOUS_GOAL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
