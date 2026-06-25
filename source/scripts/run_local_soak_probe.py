from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wls.config import load_or_create_config
from wls.runtime import LivingSystem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one isolated canonical WLS cycle for the 24-hour local soak."
    )
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--phase", type=int, choices=range(1, 7), required=True)
    parser.add_argument("--cycle", type=int, required=True)
    return parser


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    home = args.home.expanduser().resolve()
    config = load_or_create_config(home / "config.json", home=home)

    if not config.read_only:
        raise RuntimeError("24-hour soak requires the isolated WLS home to remain read-only")
    if config.allow_autonomous_reversible_writes:
        raise RuntimeError("24-hour soak forbids autonomous reversible writes")

    runtime = LivingSystem(config)
    cycle_result = runtime.run_cycle()
    integrity = runtime.verify_integrity(full=True)
    status = runtime.status()
    runtime.db.checkpoint()

    cycle_status = str(cycle_result.get("status", "UNKNOWN"))
    healthy = (
        cycle_status == "SUCCEEDED"
        and bool(integrity.get("ok"))
        and not bool(status.get("paused"))
        and not bool(status.get("killed"))
    )

    return {
        "schema_version": "1.0",
        "campaign": args.campaign,
        "commit_sha": args.commit_sha,
        "phase": args.phase,
        "cycle": args.cycle,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "healthy": healthy,
        "wls_home": str(home),
        "read_only": config.read_only,
        "provider": status.get("planner_provider"),
        "cycle_status": cycle_status,
        "cycle_id": cycle_result.get("cycle_id"),
        "cycle_count": status.get("cycle_count"),
        "plan_status": cycle_result.get("plan_status"),
        "event_counts": status.get("event_counts"),
        "integrity": integrity,
    }


def main() -> int:
    args = build_parser().parse_args()
    result = run_probe(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
