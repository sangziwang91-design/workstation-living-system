from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

NOTION_PAGE_ID = "38940ff6ad6281b6bd69d700c9322d77"
NOTION_PAGE_URL = "https://app.notion.com/p/38940ff6ad6281b6bd69d700c9322d77"
REPOSITORY = "sangziwang91-design/workstation-living-system-private"
CANONICAL_RUNTIME = "source/src/wls/runtime.py::LivingSystem"

STATUS_BY_DIRECTORY = {
    "ready": "READY",
    "claimed": "CLAIMED",
    "blocked": "BLOCKED",
    "verification": "VERIFICATION",
    "completed": "COMPLETED",
}

REQUIRED_FILES = (
    "EVOLUTION_CHAIN.md",
    ".evolution/README.md",
    ".evolution/EVOLUTION_PACKET.schema.json",
    ".evolution/worker_registry.json",
    ".evolution/CURRENT_CHAIN.json",
    ".evolution/notion_sync_contract.json",
    ".evolution/roadmap/WLS_EVOLUTION_ROADMAP.md",
)

REQUIRED_PACKET_KEYS = {
    "schema_version",
    "task_id",
    "target_id",
    "title",
    "status",
    "priority",
    "risk",
    "minimum_worker_capability",
    "worker",
    "context",
    "objective",
    "allowed_paths",
    "forbidden_changes",
    "acceptance",
    "stop_conditions",
    "output_contract",
    "handoff",
    "timestamps",
}


def _load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing file: {path.as_posix()}")
        return {}
    except json.JSONDecodeError as exc:
        errors.append(f"invalid JSON in {path.as_posix()}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"top-level JSON value must be an object: {path.as_posix()}")
        return {}
    return value


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _worker_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    workers = registry.get("workers", [])
    if not isinstance(workers, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for worker in workers:
        if isinstance(worker, dict) and isinstance(worker.get("worker_id"), str):
            result[worker["worker_id"]] = worker
    return result


def _validate_packet(
    packet_path: Path,
    expected_status: str,
    workers: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    packet = _load_json(packet_path, errors)
    if not packet:
        return

    missing = sorted(REQUIRED_PACKET_KEYS - packet.keys())
    _require(
        not missing,
        f"{packet_path.as_posix()} missing keys: {', '.join(missing)}",
        errors,
    )
    _require(
        packet.get("schema_version") == "1.0",
        f"{packet_path.as_posix()} must use schema_version 1.0",
        errors,
    )
    task_id = packet.get("task_id")
    _require(
        isinstance(task_id, str) and re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{2,127}", task_id) is not None,
        f"{packet_path.as_posix()} has an invalid task_id",
        errors,
    )
    _require(
        task_id == packet_path.stem,
        f"{packet_path.as_posix()} filename must equal task_id",
        errors,
    )
    _require(
        packet.get("status") == expected_status,
        f"{packet_path.as_posix()} status must be {expected_status}",
        errors,
    )
    _require(
        packet.get("risk") in {"LOW", "MODERATE", "HIGH", "CRITICAL"},
        f"{packet_path.as_posix()} has an invalid risk",
        errors,
    )
    _require(
        packet.get("minimum_worker_capability")
        in {"bounded_deterministic", "repository_engineering", "architecture_and_review"},
        f"{packet_path.as_posix()} has an invalid minimum_worker_capability",
        errors,
    )

    context = packet.get("context")
    _require(isinstance(context, dict), f"{packet_path.as_posix()} context must be an object", errors)
    if isinstance(context, dict):
        _require(
            context.get("repository") == REPOSITORY,
            f"{packet_path.as_posix()} repository anchor mismatch",
            errors,
        )
        _require(
            context.get("canonical_runtime") == CANONICAL_RUNTIME,
            f"{packet_path.as_posix()} canonical runtime mismatch",
            errors,
        )
        _require(
            context.get("notion_page_id") == NOTION_PAGE_ID,
            f"{packet_path.as_posix()} Notion page ID mismatch",
            errors,
        )
        _require(
            context.get("notion_page_url") == NOTION_PAGE_URL,
            f"{packet_path.as_posix()} Notion page URL mismatch",
            errors,
        )
        evidence = context.get("github_evidence")
        _require(
            isinstance(evidence, list) and bool(evidence),
            f"{packet_path.as_posix()} requires GitHub evidence anchors",
            errors,
        )

    for key in ("allowed_paths", "forbidden_changes", "stop_conditions"):
        value = packet.get(key)
        _require(
            isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value),
            f"{packet_path.as_posix()} {key} must be a non-empty string list",
            errors,
        )

    acceptance = packet.get("acceptance")
    _require(
        isinstance(acceptance, dict),
        f"{packet_path.as_posix()} acceptance must be an object",
        errors,
    )
    if isinstance(acceptance, dict):
        for key in ("commands", "conditions", "evidence_required"):
            value = acceptance.get(key)
            _require(
                isinstance(value, list) and bool(value),
                f"{packet_path.as_posix()} acceptance.{key} must be non-empty",
                errors,
            )

    worker = packet.get("worker")
    _require(isinstance(worker, dict), f"{packet_path.as_posix()} worker must be an object", errors)
    if isinstance(worker, dict):
        assigned_worker_id = worker.get("assigned_worker_id")
        required_status = worker.get("provider_status_required")
        if assigned_worker_id is not None:
            registered_worker = workers.get(str(assigned_worker_id))
            _require(
                registered_worker is not None,
                f"{packet_path.as_posix()} assigns an unknown worker: {assigned_worker_id}",
                errors,
            )
            if registered_worker is not None:
                _require(
                    registered_worker.get("status") == required_status,
                    f"{packet_path.as_posix()} worker status does not satisfy the packet",
                    errors,
                )

    if expected_status == "BLOCKED":
        dependencies = packet.get("dependencies")
        handoff = packet.get("handoff")
        _require(
            isinstance(dependencies, list) and bool(dependencies),
            f"{packet_path.as_posix()} blocked packet requires dependencies",
            errors,
        )
        _require(
            isinstance(handoff, dict) and bool(handoff.get("blocker")),
            f"{packet_path.as_posix()} blocked packet requires a blocker",
            errors,
        )


def validate_repository(repo_root: Path | None = None) -> list[str]:
    root = repo_root or Path(__file__).resolve().parents[2]
    errors: list[str] = []

    for relative_path in REQUIRED_FILES:
        _require((root / relative_path).is_file(), f"missing required file: {relative_path}", errors)

    schema = _load_json(root / ".evolution/EVOLUTION_PACKET.schema.json", errors)
    registry = _load_json(root / ".evolution/worker_registry.json", errors)
    current = _load_json(root / ".evolution/CURRENT_CHAIN.json", errors)
    notion_contract = _load_json(root / ".evolution/notion_sync_contract.json", errors)

    _require(
        schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
        "evolution packet schema must declare JSON Schema draft 2020-12",
        errors,
    )
    _require(
        schema.get("type") == "object" and schema.get("additionalProperties") is False,
        "evolution packet schema must be a closed object",
        errors,
    )

    workers = _worker_map(registry)
    _require(bool(workers), "worker registry must contain workers", errors)
    active_code_workers = [
        worker
        for worker in workers.values()
        if worker.get("status") == "ACTIVE" and worker.get("write_authority") == "BRANCH_AND_PULL_REQUEST"
    ]
    _require(
        len(active_code_workers) == 1,
        "bootstrap requires exactly one ACTIVE branch-and-PR code worker",
        errors,
    )
    _require(
        registry.get("primary_code_worker_id") == "chatgpt_interactive",
        "ChatGPT must remain the bootstrap primary code worker",
        errors,
    )
    if active_code_workers:
        _require(
            active_code_workers[0].get("worker_id") == "chatgpt_interactive",
            "the only ACTIVE code worker must be chatgpt_interactive",
            errors,
        )

    for candidate_id in (
        "gemini_candidate",
        "claude_candidate",
        "glm_candidate",
        "deepseek_candidate",
    ):
        candidate = workers.get(candidate_id)
        _require(candidate is not None, f"missing provider candidate: {candidate_id}", errors)
        if candidate is not None:
            _require(
                candidate.get("status") == "PENDING_NOT_ACTIVATED",
                f"{candidate_id} must remain PENDING_NOT_ACTIVATED",
                errors,
            )
            _require(
                candidate.get("write_authority") == "NONE",
                f"{candidate_id} must not have write authority",
                errors,
            )

    authority = current.get("authority", {})
    notion_anchor = current.get("notion_anchor", {})
    _require(
        isinstance(authority, dict) and authority.get("canonical_runtime") == CANONICAL_RUNTIME,
        "CURRENT_CHAIN canonical runtime mismatch",
        errors,
    )
    _require(
        isinstance(notion_anchor, dict) and notion_anchor.get("page_id") == NOTION_PAGE_ID,
        "CURRENT_CHAIN Notion page ID mismatch",
        errors,
    )
    _require(
        isinstance(notion_anchor, dict) and notion_anchor.get("page_url") == NOTION_PAGE_URL,
        "CURRENT_CHAIN Notion page URL mismatch",
        errors,
    )
    _require(
        current.get("worker_state", {}).get("primary_code_worker_id") == "chatgpt_interactive",
        "CURRENT_CHAIN primary worker mismatch",
        errors,
    )

    notion = notion_contract.get("notion", {})
    github = notion_contract.get("github", {})
    _require(
        isinstance(notion, dict) and notion.get("page_id") == NOTION_PAGE_ID,
        "Notion sync contract page ID mismatch",
        errors,
    )
    _require(
        isinstance(notion, dict) and notion.get("page_url") == NOTION_PAGE_URL,
        "Notion sync contract page URL mismatch",
        errors,
    )
    _require(
        isinstance(github, dict) and github.get("repository") == REPOSITORY,
        "Notion sync contract repository mismatch",
        errors,
    )
    _require(
        isinstance(github, dict) and github.get("canonical_runtime") == CANONICAL_RUNTIME,
        "Notion sync contract canonical runtime mismatch",
        errors,
    )

    queue_root = root / ".evolution/queue"
    for directory_name, expected_status in STATUS_BY_DIRECTORY.items():
        directory = queue_root / directory_name
        _require(directory.is_dir(), f"missing queue directory: {directory.relative_to(root).as_posix()}", errors)
        if not directory.is_dir():
            continue
        for packet_path in sorted(directory.glob("*.json")):
            _validate_packet(packet_path, expected_status, workers, errors)

    return errors


def main() -> int:
    errors = validate_repository()
    report = {
        "schema_version": "1.0",
        "check": "WLS_REPO_NATIVE_EVOLUTION_CHAIN",
        "success": not errors,
        "error_count": len(errors),
        "errors": errors,
        "notion_page": NOTION_PAGE_URL,
        "primary_worker": "chatgpt_interactive",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
