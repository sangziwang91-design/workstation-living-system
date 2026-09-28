from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import os

from .config import default_config
from .runtime import LivingSystem


def default_config_path() -> Path:
    env = os.environ.get("WLS_CONFIG")
    if env:
        return Path(env)
    home = os.environ.get("WLS_HOME")
    if home:
        return Path(home) / "config.json"
    return default_config().home_path / "config.json"


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wls-patch-missions",
        description="Inspect persisted WLS Patch Mission continuity without mutating runtime state.",
    )
    parser.add_argument(
        "--config",
        help="Path to config.json; defaults to WLS_CONFIG or WLS_HOME/config.json",
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--mission-id",
        help="Return only one persisted Patch Mission by mission_id.",
    )
    parser.add_argument(
        "--continuity-only",
        action="store_true",
        help="Return only mission_id and continuity for compact owner handoff.",
    )
    return parser


def _compact(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "mission_id": record.get("mission_id"),
        "status": record.get("status"),
        "mission": record.get("mission"),
        "repo_path": record.get("repo_path"),
        "goal_id": record.get("goal_id"),
        "latest_action_id": record.get("continuity", {}).get("latest_action_id"),
        "continuity": record.get("continuity", {}),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = LivingSystem.from_config_path(Path(args.config or default_config_path()))
    records = runtime.patch_missions(limit=args.limit)
    if args.mission_id:
        records = [item for item in records if item.get("mission_id") == args.mission_id]
    payload_records = [_compact(item) for item in records] if args.continuity_only else records
    print_json(
        {
            "count": len(payload_records),
            "patch_missions": payload_records,
            "authority": {
                "projection_only": True,
                "mutates_runtime_state": False,
                "next_actions_require_existing_patch_mission_policy": True,
            },
        }
    )
    runtime.db.close_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
