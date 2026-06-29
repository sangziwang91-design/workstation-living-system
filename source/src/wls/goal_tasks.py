from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .schemas import ActionSpec, RiskLevel


SUPPORTED_TASK_ACTIONS = frozenset(
    {"noop", "inspect_path", "read_file", "record_progress"}
)


def validate_task_spec(value: Any) -> dict[str, Any]:
    """Return a normalized bounded task specification or fail closed."""
    if not isinstance(value, Mapping):
        raise ValueError("task_spec must be an object")
    action = str(value.get("action", "")).strip()
    if action not in SUPPORTED_TASK_ACTIONS:
        raise ValueError(f"unsupported task_spec action: {action or '<missing>'}")
    allowed = {
        "noop": {"action", "reason"},
        "inspect_path": {"action", "path", "limit"},
        "read_file": {"action", "path", "max_bytes", "encoding"},
        "record_progress": {"action", "reason"},
    }[action]
    extra = {str(key) for key in value} - allowed
    if extra:
        raise ValueError(
            f"unexpected task_spec keys for {action}: {sorted(extra)}"
        )
    normalized = {str(key): item for key, item in value.items()}
    normalized["action"] = action
    if action in {"inspect_path", "read_file"}:
        path = str(normalized.get("path", "")).strip()
        if not path:
            raise ValueError(f"task_spec {action} requires path")
        normalized["path"] = path
    if action == "inspect_path":
        limit = int(normalized.get("limit", 200))
        if not 1 <= limit <= 1000:
            raise ValueError("inspect_path limit must be within 1..1000")
        normalized["limit"] = limit
    if action == "read_file":
        max_bytes = int(normalized.get("max_bytes", 524288))
        if not 1 <= max_bytes <= 4 * 1024 * 1024:
            raise ValueError("read_file max_bytes must be within 1..4194304")
        normalized["max_bytes"] = max_bytes
        encoding = str(normalized.get("encoding", "utf-8")).strip()
        if not encoding or len(encoding) > 64:
            raise ValueError("read_file encoding is invalid")
        normalized["encoding"] = encoding
    if action in {"noop", "record_progress"}:
        reason = str(
            normalized.get("reason", "bounded task specification")
        ).strip()
        normalized["reason"] = reason[:1000] or "bounded task specification"
    return normalized


def task_spec_action(
    goal: Mapping[str, Any],
    task_spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate a validated task spec into the canonical ActionSpec shape."""
    spec = validate_task_spec(task_spec if task_spec is not None else goal.get("task_spec"))
    action = spec["action"]
    goal_id = str(goal.get("goal_id", "")).strip() or None
    title = str(goal.get("title", "bounded goal")).strip() or "bounded goal"
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
            "arguments": {
                "path": spec["path"],
                "max_bytes": spec["max_bytes"],
                "encoding": spec["encoding"],
            },
            "purpose": f"Advance goal through bounded file inspection: {title}",
            "expected_result": "File content or bounded binary preview",
            "risk": RiskLevel.READ.value,
            "goal_id": goal_id,
            "skill_id": None,
            "acceptance": ["output contains path"],
        }
    return {
        "tool": "noop",
        "arguments": {"reason": spec["reason"]},
        "purpose": f"Record bounded goal progress without external side effect: {title}",
        "expected_result": "No external change",
        "risk": RiskLevel.READ.value,
        "goal_id": goal_id,
        "skill_id": None,
        "acceptance": ["output ok is true"],
    }


def task_spec_action_spec(
    goal: Mapping[str, Any],
    task_spec: Mapping[str, Any] | None = None,
) -> ActionSpec:
    value = task_spec_action(goal, task_spec)
    return ActionSpec(
        tool=str(value["tool"]),
        arguments=dict(value["arguments"]),
        purpose=str(value["purpose"]),
        expected_result=str(value["expected_result"]),
        risk=RiskLevel(str(value["risk"])),
        goal_id=value.get("goal_id"),
        skill_id=value.get("skill_id"),
        acceptance=[str(item) for item in value.get("acceptance", [])],
    )
