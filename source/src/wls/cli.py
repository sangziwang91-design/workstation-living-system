from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import os

from .config import default_config, save_config
from .runtime import LivingSystem
from .schemas import CandidateStatus, Event, Goal
from .server import WLSServer


def default_config_path() -> Path:
    env = os.environ.get("WLS_CONFIG")
    if env:
        return Path(env)
    home = os.environ.get("WLS_HOME")
    if home:
        return Path(home) / "config.json"
    return default_config().home_path / "config.json"


def runtime_from_args(args) -> LivingSystem:
    config_path = Path(args.config or default_config_path())
    return LivingSystem.from_config_path(config_path)


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wls", description="Workstation Living System"
    )
    parser.add_argument(
        "--config",
        help="Path to config.json; defaults to WLS_CONFIG or WLS_HOME/config.json",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a standalone WLS home and configuration")
    init.add_argument("--home", required=True)
    init.add_argument("--force", action="store_true")

    sub.add_parser("once", help="Run one observe-learn-act cycle")
    daemon = sub.add_parser("daemon", help="Run persistent cycles")
    daemon.add_argument("--max-cycles", type=int)
    sub.add_parser("status", help="Show current state")
    sub.add_parser("sleep", help="Run offline memory and skill consolidation")
    sub.add_parser("verify", help="Verify database and evidence chain")
    sub.add_parser("self-check", help="Run installation and runtime self-check")

    goal = sub.add_parser("add-goal", help="Add a human-authored goal")
    goal.add_argument("title")
    goal.add_argument("--description", default="")
    goal.add_argument("--priority", type=float, default=0.5)
    goal.add_argument("--criterion", action="append", default=[])

    event = sub.add_parser("add-event", help="Add a structured event")
    event.add_argument("event_type")
    event.add_argument("--source", default="cli")
    event.add_argument("--payload", default="{}", help="JSON object")
    event.add_argument("--salience", type=float, default=0.5)
    event.add_argument("--dedupe-key")

    relationship = sub.add_parser(
        "add-relationship",
        help="Record evidence-bound relationship or stable user context",
    )
    relationship.add_argument("subject")
    relationship.add_argument("relation_type")
    relationship.add_argument("--value", required=True, help="JSON value")
    relationship.add_argument(
        "--stability", choices=["transient", "working", "stable"], default="working"
    )
    relationship.add_argument("--confidence", type=float, default=0.7)
    relationship.add_argument("--source-id", action="append", required=True)

    approve = sub.add_parser("approve", help="Approve one exact pending action")
    approve.add_argument("action_id")
    approve.add_argument("--minutes", type=int, default=30)
    approve.add_argument("--reason", default="")

    reject = sub.add_parser("reject", help="Reject one exact pending action")
    reject.add_argument("action_id")
    reject.add_argument("--reason", default="")

    resume_action = sub.add_parser("resume-action", help="Resume an approved action")
    resume_action.add_argument("action_id")

    resolve = sub.add_parser(
        "resolve-unknown", help="Resolve an action with unknown side effects"
    )
    resolve.add_argument("action_id")
    resolve.add_argument(
        "resolution", choices=["SUCCEEDED", "FAILED", "CANCELLED", "RETRY_SAFE"]
    )
    resolve.add_argument("--evidence", default="{}")

    pause = sub.add_parser("pause")
    pause.add_argument("reason")
    resume = sub.add_parser("resume")
    resume.add_argument("evidence")
    kill = sub.add_parser("kill")
    kill.add_argument("reason")
    reset = sub.add_parser("reset-kill")
    reset.add_argument("evidence")

    world = sub.add_parser("world")
    world.add_argument("--query", default="")
    world.add_argument("--limit", type=int, default=50)
    memory = sub.add_parser("memories")
    memory.add_argument("--type")
    memory.add_argument("--limit", type=int, default=50)
    sub.add_parser("skills", help="List learned skills and lifecycle state")

    skill_transition = sub.add_parser(
        "skill",
        help="Advance one declarative learned skill through its gated lifecycle",
    )
    skill_transition.add_argument("skill_id")
    skill_transition.add_argument(
        "target", choices=[item.value for item in CandidateStatus]
    )
    skill_transition.add_argument("--evidence", default="{}")
    skill_transition.add_argument("--human-approved", action="store_true")

    serve = sub.add_parser("serve", help="Run loopback-only JSON API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    transition = sub.add_parser(
        "candidate", help="Advance a bounded evolution candidate"
    )
    transition.add_argument("candidate_id")
    transition.add_argument("target", choices=[item.value for item in CandidateStatus])
    transition.add_argument("--evidence", default="{}")
    transition.add_argument("--human-approved", action="store_true")

    failure_candidates = sub.add_parser(
        "failure-candidates", help="Create candidates from repeated recorded failures"
    )
    failure_candidates.add_argument("--minimum-repeats", type=int, default=3)
    failure_candidates.add_argument("--lookback-days", type=int, default=30)

    growth_recover = sub.add_parser(
        "growth-recover", help="Run a frozen-baseline recovery experiment"
    )
    growth_recover.add_argument("candidate_id")
    growth_recover.add_argument(
        "--strategy",
        choices=["contract_recovery", "fixture_recovery"],
        default="contract_recovery",
    )

    growth_propose = sub.add_parser(
        "growth-propose", help="Create a versioned skill from passed recovery evidence"
    )
    growth_propose.add_argument("candidate_id")
    growth_propose.add_argument("recovery_experiment_id")
    growth_propose.add_argument("--name")

    growth_validate = sub.add_parser(
        "growth-validate", help="Validate the proposed skill in isolation"
    )
    growth_validate.add_argument("growth_cycle_id")

    growth_promote = sub.add_parser(
        "growth-promote", help="Human-approve and promote a validated skill"
    )
    growth_promote.add_argument("growth_cycle_id")
    growth_promote.add_argument("--actor", required=True)
    growth_promote.add_argument("--authorization-reference", required=True)
    growth_promote.add_argument("--human-approved", action="store_true")

    growth_reuse = sub.add_parser(
        "growth-reuse", help="Reuse a promoted skill through the canonical runtime"
    )
    growth_reuse.add_argument("growth_cycle_id")
    growth_reuse.add_argument("--task-title")

    growth_rollback = sub.add_parser(
        "growth-rollback", help="Human-authorize and test rollback of a promoted skill"
    )
    growth_rollback.add_argument("growth_cycle_id")
    growth_rollback.add_argument("--actor", required=True)
    growth_rollback.add_argument("--authorization-reference", required=True)
    growth_rollback.add_argument("--human-approved", action="store_true")

    growth_status = sub.add_parser("growth-status", help="Show growth-cycle evidence")
    growth_status.add_argument("--limit", type=int, default=20)

    cognition = sub.add_parser(
        "cognition", help="Show durable local hypotheses, predictions, and calibration"
    )
    cognition.add_argument("--limit", type=int, default=20)

    export = sub.add_parser("export-evidence")
    export.add_argument("path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            config_path = Path(args.config or (Path(args.home) / "config.json"))
            if config_path.exists() and not args.force:
                raise FileExistsError(f"config already exists: {config_path}")
            config = default_config(args.home)
            save_config(config, config_path)
            runtime = LivingSystem(config)
            print_json(
                {
                    "config": str(config_path),
                    "home": str(config.home_path),
                    "status": runtime.status(),
                }
            )
            return 0
        runtime = runtime_from_args(args)
        if args.command == "once":
            print_json(runtime.run_cycle())
        elif args.command == "daemon":
            runtime.run_daemon(args.max_cycles)
        elif args.command == "status":
            print_json(runtime.status())
        elif args.command == "sleep":
            print_json(runtime.sleep.run())
        elif args.command == "verify":
            result = runtime.verify_integrity(full=True)
            print_json(result)
            return 0 if result["ok"] else 2
        elif args.command == "self-check":
            result = self_check(runtime)
            print_json(result)
            return 0 if result["ok"] else 2
        elif args.command == "add-goal":
            goal = Goal(
                title=args.title,
                description=args.description,
                priority=args.priority,
                success_criteria=args.criterion,
                source="cli",
                autonomous=False,
            )
            print_json({"goal_id": runtime.add_goal(goal)})
        elif args.command == "add-event":
            payload = json.loads(args.payload)
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
            event = Event(
                event_type=args.event_type,
                source=args.source,
                payload=payload,
                salience_hint=args.salience,
                dedupe_key=args.dedupe_key,
            )
            event_id, inserted = runtime.ingest_event(event)
            print_json({"event_id": event_id, "inserted": inserted})
        elif args.command == "add-relationship":
            value = json.loads(args.value)
            relation_id = runtime.relationships.record(
                subject=args.subject,
                relation_type=args.relation_type,
                value=value,
                stability=args.stability,
                confidence=args.confidence,
                source_ids=args.source_id,
            )
            print_json({"relation_id": relation_id})
        elif args.command == "approve":
            approval_id = runtime.approvals.issue(
                args.action_id, True, args.minutes, args.reason
            )
            print_json({"approval_id": approval_id})
        elif args.command == "reject":
            approval_id = runtime.approvals.issue(
                args.action_id, False, 30, args.reason
            )
            print_json({"approval_id": approval_id})
        elif args.command == "resume-action":
            print_json(runtime.resume_action(args.action_id))
        elif args.command == "resolve-unknown":
            evidence = json.loads(args.evidence)
            if not isinstance(evidence, dict):
                raise ValueError("evidence must be a JSON object")
            runtime.resolve_unknown_action(args.action_id, args.resolution, evidence)
            print_json({"resolved": True})
        elif args.command == "pause":
            print_json({"evidence_id": runtime.pause(args.reason)})
        elif args.command == "resume":
            print_json({"evidence_id": runtime.resume(args.evidence)})
        elif args.command == "kill":
            print_json({"evidence_id": runtime.kill(args.reason)})
        elif args.command == "reset-kill":
            print_json({"evidence_id": runtime.reset_kill(args.evidence)})
        elif args.command == "world":
            print_json(
                runtime.world.query(args.query, args.limit)
                if args.query
                else runtime.world.active_facts(limit=args.limit)
            )
        elif args.command == "memories":
            print_json(runtime.memories.recent(args.type, args.limit))
        elif args.command == "skills":
            rows = runtime.db.query_all(
                "SELECT skill_id,name,version,status,success_rate,use_count,definition_json FROM skills ORDER BY name,version DESC"
            )
            print_json(
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
            evidence = json.loads(args.evidence)
            if not isinstance(evidence, dict):
                raise ValueError("evidence must be a JSON object")
            runtime.skills.transition(
                args.skill_id,
                CandidateStatus(args.target),
                evidence,
                args.human_approved,
            )
            print_json({"transitioned": True})
        elif args.command == "serve":
            server = WLSServer(runtime, args.host, args.port)
            print_json(
                {
                    "host": args.host,
                    "port": args.port,
                    "token_path": str(
                        runtime.config.secret_path.with_name("server.token")
                    ),
                }
            )
            server.serve_forever()
        elif args.command == "candidate":
            evidence = json.loads(args.evidence)
            if not isinstance(evidence, dict):
                raise ValueError("evidence must be a JSON object")
            runtime.learning.transition_candidate(
                args.candidate_id,
                CandidateStatus(args.target),
                evidence,
                args.human_approved,
            )
            print_json({"transitioned": True})
        elif args.command == "failure-candidates":
            print_json(
                {
                    "candidate_ids": runtime.learning.create_failure_candidates(
                        minimum_repeats=args.minimum_repeats,
                        lookback_days=args.lookback_days,
                    )
                }
            )
        elif args.command == "growth-recover":
            print_json(
                runtime.growth.run_recovery_experiment(
                    args.candidate_id, args.strategy
                )
            )
        elif args.command == "growth-propose":
            print_json(
                runtime.growth.propose_skill_from_recovery(
                    args.candidate_id,
                    args.recovery_experiment_id,
                    name=args.name,
                )
            )
        elif args.command == "growth-validate":
            print_json(runtime.growth.validate_skill(args.growth_cycle_id))
        elif args.command == "growth-promote":
            print_json(
                runtime.growth.approve_and_promote(
                    args.growth_cycle_id,
                    args.actor,
                    args.authorization_reference,
                    human_approved=args.human_approved,
                )
            )
        elif args.command == "growth-reuse":
            print_json(
                runtime.growth.reuse_on_runtime_task(
                    args.growth_cycle_id, task_title=args.task_title
                )
            )
        elif args.command == "growth-rollback":
            print_json(
                runtime.growth.rollback(
                    args.growth_cycle_id,
                    args.actor,
                    args.authorization_reference,
                    human_approved=args.human_approved,
                )
            )
        elif args.command == "growth-status":
            print_json(runtime.growth.summary(limit=args.limit))
        elif args.command == "cognition":
            print_json(
                {
                    "summary": runtime.cognition.summary(limit=500),
                    "recent": runtime.cognition.recent(limit=args.limit),
                    "temporal_world": runtime.temporal_world.summary(),
                }
            )
        elif args.command == "export-evidence":
            print_json({"path": str(runtime.ledger.export_jsonl(args.path))})
        else:
            parser.error("unhandled command")
        return 0
    except Exception as exc:
        print_json({"error": f"{type(exc).__name__}: {exc}"})
        return 1


def self_check(runtime: LivingSystem) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["directories"] = all(
        path.exists()
        for path in (
            runtime.config.home_path,
            runtime.config.db_path.parent,
            runtime.config.sandbox_path,
            runtime.config.inbox_path,
            runtime.config.outbox_path,
        )
    )
    checks["database"] = runtime.db.integrity_check()
    checks["evidence"] = runtime.ledger.verify()
    checks["identity"] = "identity" in runtime.self_model.snapshot()
    checks["tools"] = sorted(runtime.tools._tools)
    checks["sensor_types"] = sorted(
        {sensor.sensor_type for sensor in runtime.config.sensors}
    )
    checks["read_only"] = runtime.config.read_only
    checks["growth_tables"] = all(
        runtime.db.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        is not None
        for name in (
            "recovery_experiments",
            "skill_experiments",
            "growth_cycles",
            "growth_measurements",
        )
    )
    ok = (
        checks["directories"]
        and checks["database"][0]
        and checks["evidence"][0]
        and checks["identity"]
        and checks["growth_tables"]
    )
    return {"ok": bool(ok), "checks": checks}


if __name__ == "__main__":
    raise SystemExit(main())
