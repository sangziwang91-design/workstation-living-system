"""Real, independent-outcome WLS actions on ephemeral GitHub Windows.

This is a *functional slice*, not autonomous repair. No second WLS runtime,
owner data, trusted GitHub token, model calls, writes, or self-scoring.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any

from verify_hosted_living_loop import cli

SCHEMA = "wls.g1.hosted_windows_real_actions.v1"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def examine_action(home: Path, mission: dict[str, Any], *, expected_tool: str,
                   expected_marker: str, expected_source: Path) -> dict[str, Any]:
    """Require real persisted tool evidence and an independently checked file."""
    outcomes = mission.get("outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != 1:
        raise ValueError("missing exactly one canonical tool outcome")
    outcome = outcomes[0]
    if not isinstance(outcome, dict) or outcome.get("success") is not True:
        raise ValueError("canonical WLS action did not succeed")
    if outcome.get("reused") is True or outcome.get("status") != "SUCCEEDED":
        raise ValueError("stale or replayed WLS action")
    if not isinstance(mission.get("action_id"), str):
        raise ValueError("missing persisted action identifier")
    conn = sqlite3.connect(home / "state" / "wls.db")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT tool,status,result_json FROM actions WHERE action_id=?",
            (mission["action_id"],),
        ).fetchone()
    finally:
        conn.close()
    if row is None or row["tool"] != expected_tool or row["status"] != "SUCCEEDED":
        raise ValueError("WLS action SQLite provenance missing or wrong tool")
    payload = json.loads(row["result_json"])
    if not isinstance(payload, dict) or payload.get("evaluation", {}).get("accepted") is not True:
        raise ValueError("missing persisted independent action acceptance")
    tool_result = payload.get("result")
    if not isinstance(tool_result, dict) or tool_result.get("success") is not True:
        raise ValueError("tool result missing or unsuccessful")
    rendered = json.dumps(tool_result.get("output"), ensure_ascii=False)
    if expected_marker not in rendered:
        raise ValueError("observed tool output did not contain external marker")
    source_bytes = expected_source.read_bytes()
    if expected_marker.encode("utf-8") not in source_bytes and (
        expected_tool == "read_file"
    ):
        raise ValueError("on-disk source does not support WLS observation")
    return {
        "task_class": "repository_instruction_read" if expected_tool == "read_file"
        else "directory_inventory",
        "tool": expected_tool,
        "action_id": mission["action_id"],
        "source_sha256": digest(source_bytes),
        "outcome_sha256": digest(row["result_json"].encode("utf-8")),
        "real_tool_executed": True,
        "external_marker_verified": True,
        "replayed": False,
    }


def execute(workspace: Path, head: str) -> dict[str, Any]:
    if len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise ValueError("must provide exact Git commit")
    with tempfile.TemporaryDirectory(prefix="wls-g1-actions-") as root:
        base = Path(root)
        home = base / "home"
        repo_a = base / "repo-instruction"
        repo_a.mkdir()
        marker_a = "wls-g1-independent-code-instruction"
        (repo_a / "AGENTS.md").write_text(
            "# Public synthetic task\n" + marker_a + "\n", encoding="utf-8"
        )
        (repo_a / "module.py").write_text("VALUE = 17\n", encoding="utf-8")
        repo_b = base / "repo-directory"
        repo_b.mkdir()
        marker_b = "wls-g1-inventory-probe.dat"
        (repo_b / marker_b).write_text("public synthetic directory listing\n", encoding="utf-8")
        config = cli(home, "init")
        if config.get("status", {}).get("read_only") is not True:
            raise ValueError("canonical WLS is not read-only")
        first = cli(home, "patch-mission", str(repo_a),
                    "Inspect the public repository contribution instructions")
        source_a = repo_a / "AGENTS.md"
        evidence_a = examine_action(
            home, first, expected_tool="read_file",
            expected_marker=marker_a, expected_source=source_a,
        )
        second = cli(home, "patch-mission", str(repo_b),
                     "Inspect the public synthetic project file listing")
        source_b = repo_b / marker_b
        evidence_b = examine_action(
            home, second, expected_tool="list_directory",
            expected_marker=marker_b, expected_source=source_b,
        )
        if evidence_a["action_id"] == evidence_b["action_id"]:
            raise ValueError("same action ID reused between different real tasks")
        if cli(home, "verify").get("ok") is not True:
            raise ValueError("canonical ledger and SQLite integrity failed")
        status = cli(home, "status")
        if status.get("read_only") is not True:
            raise ValueError("readonly permission changed")
        # Patch Missions create evidence-bound mission cycles, not ordinary
        # wls-once cycles; status.cycle_count is NOT their canonical counter.
        # Check the two source-linked mission cycles in the actual DB after
        # a separate WLS process has reopened and verified the same home.
        ids = (first.get("cycle_id"), second.get("cycle_id"))
        if (any(not isinstance(i, str) or not i.startswith("cycle_") for i in ids)
                or ids[0] == ids[1]):
            raise ValueError("missing distinct canonical patch-mission cycles")
        with sqlite3.connect(home / "state/wls.db") as conn:
            stored_cycles = conn.execute(
                "SELECT cycle_id,status FROM cycles WHERE cycle_id IN (?,?)",
                ids,
            ).fetchall()
        if len(stored_cycles) != 2 or any(row[1] != "SUCCEEDED" for row in stored_cycles):
            raise ValueError("patch-mission cycles not persisted across WLS processes")
    return {
        "schema": SCHEMA,
        "status": "TWO_OBSERVED_WINDOWS_ACTION_CLASSES",
        "head_sha": head,
        "task_count": 2,
        "distinct_task_classes": 2,
        "actions": [evidence_a, evidence_b],
        "canonical_ledger_verified": True,
        "new_process_verified_persistence": True,
        "persisted_mission_cycles": 2,
        "read_only": True,
        "real_owner_task_count": 0,
        "autonomous_goal_count": 0,
        "model_calls": 0,
        "heldout_skill_gain_proven": False,
        "claim_ceiling": (
            "two controlled but real Windows tool observations; not independent "
            "autonomy, cross-run persistence, user value, or RSI improvement"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = execute(args.output.parent, args.head)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        # This probe creates *only* public synthetic fixtures. Retain a short
        # diagnostic rather than inventing success or uploading tool payloads.
        error_summary = str(exc)[:240]
        print(f"G1_WLS_TASK_BLOCKED {type(exc).__name__}: {error_summary}", file=sys.stderr)
        result = {
            "schema": SCHEMA, "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "failure_summary": error_summary,
            "claim_ceiling": "no_real_task_claim_without_observed_tool_success",
        }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"status": result["status"], "tasks": result.get("task_count", 0)}))
    return 0 if result["status"] == "TWO_OBSERVED_WINDOWS_ACTION_CLASSES" else 2


if __name__ == "__main__":
    raise SystemExit(main())
