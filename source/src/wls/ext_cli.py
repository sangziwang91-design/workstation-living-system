from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import sys

from . import cli as base_cli
from .config import default_config, save_config
from .schemas import CandidateStatus
from .v2_runtime import LivingSystemV2


NEW_COMMANDS = {
    "skill-experiment",
    "skill-experiments",
    "recovery-experiment",
    "recovery-experiments",
    "evolution-materialize",
    "evolution-approve",
    "evolution-promote",
    "evolution-observe",
    "evolution-evaluate",
    "evolution-rollback",
    "evolution-status",
}
EXTENDED_BASE_COMMANDS = {
    "init",
    "once",
    "daemon",
    "status",
    "sleep",
    "verify",
    "self-check",
    "skills",
    "skill",
    "candidate",
}


def _json_object(raw: str, label: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def new_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wls")
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)

    skill = sub.add_parser("skill-experiment")
    skill.add_argument("skill_id")
    skill_list = sub.add_parser("skill-experiments")
    skill_list.add_argument("--skill-id")
    skill_list.add_argument("--limit", type=int, default=50)

    recovery = sub.add_parser("recovery-experiment")
    recovery.add_argument("candidate_id")
    recovery.add_argument(
        "--strategy",
        choices=["contract_recovery", "fixture_recovery"],
        default="contract_recovery",
    )
    recovery_list = sub.add_parser("recovery-experiments")
    recovery_list.add_argument("--candidate-id")
    recovery_list.add_argument("--limit", type=int, default=50)

    materialize = sub.add_parser(
        "evolution-materialize",
        help="Create a versioned skill from a validated recovery candidate",
    )
    materialize.add_argument("candidate_id")
    materialize.add_argument("recovery_experiment_id")

    approve = sub.add_parser(
        "evolution-approve",
        help="Owner-approve a linked validated candidate and skill",
    )
    approve.add_argument("candidate_id")
    approve.add_argument("skill_id")
    approve.add_argument("--evidence", required=True)
    approve.add_argument("--human-approved", action="store_true")

    promote = sub.add_parser(
        "evolution-promote",
        help="Owner-promote a linked approved candidate and skill",
    )
    promote.add_argument("candidate_id")
    promote.add_argument("skill_id")
    promote.add_argument("--evidence", required=True)
    promote.add_argument("--human-approved", action="store_true")

    observe = sub.add_parser(
        "evolution-observe",
        help="Backfill one durable post-promotion runtime action observation",
    )
    observe.add_argument("action_id")

    evaluate = sub.add_parser(
        "evolution-evaluate", help="Evaluate measured post-promotion benefit"
    )
    evaluate.add_argument("deployment_id")

    rollback = sub.add_parser(
        "evolution-rollback", help="Owner-roll back one promoted deployment"
    )
    rollback.add_argument("deployment_id")
    rollback.add_argument("--reason", required=True)
    rollback.add_argument("--human-approved", action="store_true")

    evolution_status = sub.add_parser("evolution-status")
    evolution_status.add_argument("--candidate-id")
    evolution_status.add_argument("--skill-id")
    evolution_status.add_argument("--limit", type=int, default=50)
    return parser


def runtime(config_value: str | None) -> LivingSystemV2:
    path = Path(config_value or base_cli.default_config_path())
    return LivingSystemV2.from_config_path(path)  # type: ignore[return-value]


def dispatch_new(args_list: list[str]) -> int:
    args = new_parser().parse_args(args_list)
    system = runtime(args.config)
    if args.command == "skill-experiment":
        base_cli.print_json(system.skill_experiments.run(args.skill_id))
    elif args.command == "skill-experiments":
        base_cli.print_json(
            system.skill_experiments.list_experiments(
                skill_id=args.skill_id, limit=args.limit
            )
        )
    elif args.command == "recovery-experiment":
        base_cli.print_json(system.recoveries.run(args.candidate_id, args.strategy))
    elif args.command == "recovery-experiments":
        base_cli.print_json(
            system.recoveries.list_experiments(
                candidate_id=args.candidate_id, limit=args.limit
            )
        )
    elif args.command == "evolution-materialize":
        base_cli.print_json(
            system.evolution.materialize_skill(
                args.candidate_id, args.recovery_experiment_id
            )
        )
    elif args.command == "evolution-approve":
        base_cli.print_json(
            system.evolution.approve(
                args.candidate_id,
                args.skill_id,
                _json_object(args.evidence, "evidence"),
                args.human_approved,
            )
        )
    elif args.command == "evolution-promote":
        base_cli.print_json(
            system.evolution.promote(
                args.candidate_id,
                args.skill_id,
                _json_object(args.evidence, "evidence"),
                args.human_approved,
            )
        )
    elif args.command == "evolution-observe":
        base_cli.print_json(
            {"action_id": args.action_id, "observation": system.evolution.observe_action(args.action_id)}
        )
    elif args.command == "evolution-evaluate":
        base_cli.print_json(system.evolution.evaluate(args.deployment_id))
    elif args.command == "evolution-rollback":
        base_cli.print_json(
            system.evolution.rollback(
                args.deployment_id,
                _json_object(args.reason, "reason"),
                args.human_approved,
            )
        )
    elif args.command == "evolution-status":
        base_cli.print_json(
            system.evolution.status(
                candidate_id=args.candidate_id,
                skill_id=args.skill_id,
                limit=args.limit,
            )
        )
    return 0


def dispatch_extended(args_list: list[str]) -> int:
    args = base_cli.build_parser().parse_args(args_list)
    if args.command == "init":
        config_path = Path(args.config or (Path(args.home) / "config.json"))
        if config_path.exists() and not args.force:
            raise FileExistsError(f"config already exists: {config_path}")
        config = default_config(args.home)
        save_config(config, config_path)
        system = LivingSystemV2(config)
        base_cli.print_json(
            {
                "config": str(config_path),
                "home": str(config.home_path),
                "status": system.status(),
            }
        )
        return 0
    system = runtime(args.config)
    if args.command == "once":
        base_cli.print_json(system.run_cycle())
    elif args.command == "daemon":
        system.run_daemon(args.max_cycles)
    elif args.command == "status":
        base_cli.print_json(system.status())
    elif args.command == "sleep":
        base_cli.print_json(system.sleep.run())
    elif args.command == "verify":
        result = system.verify_integrity(full=True)
        base_cli.print_json(result)
        return 0 if result["ok"] else 2
    elif args.command == "self-check":
        result = base_cli.self_check(system)
        required_tables = (
            "skill_experiments",
            "recovery_experiments",
            "evolution_skill_links",
            "skill_deployments",
            "skill_reuse_observations",
        )
        result["checks"]["experiment_tables"] = all(
            system.db.query_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            )
            is not None
            for name in required_tables
        )
        result["ok"] = bool(result["ok"] and result["checks"]["experiment_tables"])
        base_cli.print_json(result)
        return 0 if result["ok"] else 2
    elif args.command == "skills":
        rows = system.db.query_all(
            "SELECT skill_id,name,version,status,success_rate,use_count,definition_json FROM skills ORDER BY name,version DESC"
        )
        base_cli.print_json(
            [
                {
                    "skill_id": row["skill_id"],
                    "name": row["name"],
                    "version": row["version"],
                    "status": row["status"],
                    "success_rate": row["success_rate"],
                    "use_count": row["use_count"],
                    "definition": json.loads(row["definition_json"]),
                }
                for row in rows
            ]
        )
    elif args.command == "skill":
        evidence = _json_object(args.evidence, "evidence")
        system.skills.transition(
            args.skill_id,
            CandidateStatus(args.target),
            evidence,
            args.human_approved,
        )
        base_cli.print_json({"transitioned": True})
    elif args.command == "candidate":
        evidence = _json_object(args.evidence, "evidence")
        system.learning.transition_candidate(
            args.candidate_id,
            CandidateStatus(args.target),
            evidence,
            args.human_approved,
        )
        base_cli.print_json({"transitioned": True})
    return 0


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    command = next(
        (item for item in args_list if item in NEW_COMMANDS | EXTENDED_BASE_COMMANDS),
        None,
    )
    try:
        if command in NEW_COMMANDS:
            return dispatch_new(args_list)
        if command in EXTENDED_BASE_COMMANDS:
            return dispatch_extended(args_list)
        return base_cli.main(args_list)
    except Exception as exc:
        base_cli.print_json({"error": f"{type(exc).__name__}: {exc}"})
        return 1
