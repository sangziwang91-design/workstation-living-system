from __future__ import annotations

from typing import Any
import json


ATTRIBUTABLE = {
    "EXECUTED_CURRENT_ACTION",
    "RECOVERED_DURABLE_ACTION",
}


def recover_episode(
    runtime: Any,
    cycle_id: str,
    plan_id: str,
    outcomes: list[dict[str, Any]],
) -> str | None:
    """Restore one missing episode only from attributable execution evidence."""

    rows = runtime.db.query_all(
        "SELECT content_json FROM memories WHERE memory_type='episodic'"
    )
    for row in rows:
        try:
            if json.loads(row["content_json"]).get("cycle_id") == cycle_id:
                return None
        except (json.JSONDecodeError, AttributeError):
            continue
    attributable = [
        item
        for item in outcomes
        if item.get("provenance") in ATTRIBUTABLE
    ]
    if not attributable:
        return None
    checkpoint = runtime.db.query_one(
        """
        SELECT payload_json FROM cycle_checkpoints
        WHERE cycle_id=? AND phase='PLAN_PERSISTED'
        """,
        (cycle_id,),
    )
    context = json.loads(checkpoint["payload_json"]) if checkpoint else {}
    cycle = runtime.db.query_one(
        "SELECT workspace_json FROM cycles WHERE cycle_id=?", (cycle_id,)
    )
    workspace = (
        json.loads(cycle["workspace_json"])
        if cycle is not None and cycle["workspace_json"]
        else []
    )
    return runtime.learning.record_episode(
        cycle_id=cycle_id,
        event_ids=[str(value) for value in context.get("event_ids", [])],
        plan_id=plan_id,
        outcomes=attributable,
        prediction_errors=[],
        workspace=workspace,
    )
