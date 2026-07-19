from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import http.client
import json
import os
import tempfile

from .capabilities import ordinary_user_capability_map
from .config import default_config, load_or_create_config, save_config
from .db import Database
from .performance import (
    DEFAULT_PERFORMANCE_BUDGETS_SECONDS,
    PerformanceMeasurement,
)
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
    sub.add_parser("health", help="Show bounded operational health")
    life_state = sub.add_parser("life-state", help="Show bounded living-system state")
    life_state.add_argument("--limit", type=int, default=5)
    sub.add_parser("status", help="Show current state")
    sub.add_parser(
        "capabilities",
        help="Show ordinary-user capability labels and productization status",
    )
    sub.add_parser("sleep", help="Run offline memory and skill consolidation")
    sub.add_parser("verify", help="Verify database and evidence chain")
    sub.add_parser("self-check", help="Run installation and runtime self-check")
    garbage_audit = sub.add_parser(
        "garbage-audit",
        help="Read-only self-audit for cleanup candidates; does not delete files",
    )
    garbage_audit.add_argument(
        "--path",
        action="append",
        default=[],
        help="Root to scan; defaults to WLS home. May be repeated.",
    )
    garbage_audit.add_argument("--max-candidates", type=int, default=500)
    garbage_audit.add_argument("--reason", default="owner requested garbage audit")
    garbage_audit.add_argument(
        "--execute-cleanup",
        action="store_true",
        help="Quarantine only audited cleanup candidates; requires --approval-reference",
    )
    garbage_audit.add_argument(
        "--approval-reference",
        default="",
        help="Owner approval reference required with --execute-cleanup",
    )
    garbage_clear = sub.add_parser(
        "garbage-clear-quarantine",
        help="Owner-approved clear of one garbage audit quarantine directory",
    )
    garbage_clear.add_argument("--audit-id", required=True)
    garbage_clear.add_argument("--approval-reference", required=True)
    garbage_clear.add_argument(
        "--reason", default="owner requested garbage quarantine clear"
    )

    performance_audit = sub.add_parser(
        "performance-audit",
        help="Measure commercial readiness performance budgets and record a receipt",
    )
    performance_audit.add_argument("--samples", type=int, default=3)
    performance_audit.add_argument(
        "--reason", default="owner requested performance budget audit"
    )
    performance_audit.add_argument(
        "--include-write-workflows",
        action="store_true",
        help=(
            "Also measure owner-gated garbage review write APIs on a disposable "
            "temporary runtime home"
        ),
    )
    performance_audit.add_argument(
        "--include-rollback-workflow",
        action="store_true",
        help="Also measure disposable clone rollback drill performance",
    )
    retention_audit = sub.add_parser(
        "retention-audit",
        help="Audit runtime receipt retention pressure without deleting anything",
    )
    retention_audit.add_argument("--max-items", type=int, default=100)
    retention_audit.add_argument("--max-json-bytes", type=int, default=1_000_000)
    retention_audit.add_argument(
        "--reason", default="owner requested retention pressure audit"
    )

    soak_audit = sub.add_parser(
        "soak-audit",
        help="Run a bounded canonical-cycle reliability soak and record a receipt",
    )
    soak_audit.add_argument("--cycles", type=int, default=1)
    soak_audit.add_argument("--max-cycle-seconds", type=float)
    soak_audit.add_argument("--reason", default="owner requested bounded soak audit")

    upgrade_drill = sub.add_parser(
        "upgrade-drill",
        help="Back up the live DB and verify a restored copy without modifying the live install",
    )
    upgrade_drill.add_argument("--wheel", required=True, help="Wheel planned for upgrade")
    upgrade_drill.add_argument(
        "--disposable-clone",
        action="store_true",
        help="Also simulate upgrade and rollback on a temporary cloned home",
    )
    upgrade_drill.add_argument(
        "--reason", default="owner requested upgrade rollback drill"
    )

    commercial_readiness = sub.add_parser(
        "commercial-readiness-audit",
        help="Audit personal commercial Beta/RC readiness from local receipts",
    )
    commercial_readiness.add_argument(
        "--rc-min-qualified-measurements", type=int, default=8
    )
    commercial_readiness.add_argument(
        "--reason", default="owner requested commercial readiness audit"
    )

    external_product = sub.add_parser(
        "external-product-audit",
        help="Audit external single-user product delivery readiness",
    )
    external_product.add_argument(
        "--wheel",
        default="",
        help="Optional wheel path; defaults to the newest local dist wheel",
    )
    external_product.add_argument(
        "--include-multi-user",
        action="store_true",
        help="Require multi-user readiness; off by default for single-user delivery",
    )
    external_product.add_argument(
        "--reason", default="owner requested external product audit"
    )

    m7_self_check = sub.add_parser(
        "m7-self-check",
        help="Audit WLS against the local M7 personal single-user runtime gate",
    )
    m7_self_check.add_argument(
        "--m7-root",
        default="",
        help="Optional M7 reference folder; defaults to Desktop/M7家族 when present",
    )
    m7_self_check.add_argument("--reason", default="owner requested M7 self-check")

    longitudinal_start = sub.add_parser(
        "longitudinal-start",
        help="Start a receipt-bound owner-task longitudinal protocol",
    )
    longitudinal_start.add_argument("--host-id", required=True)
    longitudinal_start.add_argument("--baseline-commit", required=True)
    longitudinal_start.add_argument("--duration-days", type=int, default=30)
    longitudinal_start.add_argument(
        "--reason", default="owner requested longitudinal protocol"
    )

    longitudinal_record = sub.add_parser(
        "longitudinal-record",
        help="Record one owner-task longitudinal measurement",
    )
    longitudinal_record.add_argument("--protocol-id", required=True)
    longitudinal_record.add_argument("--task-class", required=True)
    outcome = longitudinal_record.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--success", action="store_true")
    outcome.add_argument("--failed", action="store_true")
    longitudinal_record.add_argument("--corrections", type=int, default=0)
    longitudinal_record.add_argument("--cost", type=float, default=0.0)
    longitudinal_record.add_argument("--latency-seconds", type=float, default=0.0)
    longitudinal_record.add_argument("--memory-benefit", action="store_true")
    longitudinal_record.add_argument("--skill-reuse", action="store_true")
    longitudinal_record.add_argument("--task-reference", default="")
    longitudinal_record.add_argument("--owner-review", default="")
    longitudinal_record.add_argument(
        "--reason", default="owner recorded longitudinal measurement"
    )

    longitudinal_report = sub.add_parser(
        "longitudinal-report",
        help="Compile a receipt-bound owner-task longitudinal report",
    )
    longitudinal_report.add_argument("--protocol-id", required=True)
    longitudinal_report.add_argument("--baseline-success-rate", type=float, default=1.0)
    longitudinal_report.add_argument("--max-regression", type=float, default=0.1)
    longitudinal_report.add_argument(
        "--reason", default="owner requested longitudinal report"
    )

    goal = sub.add_parser("add-goal", help="Add a human-authored goal")
    goal.add_argument("title")
    goal.add_argument("--description", default="")
    goal.add_argument("--priority", type=float, default=0.5)
    goal.add_argument("--criterion", action="append", default=[])

    patch_mission = sub.add_parser(
        "patch-mission",
        help="Start a local GitHub Patch Mission from a repo path and mission text",
    )
    patch_mission.add_argument("repo_path")
    patch_mission.add_argument("mission")
    patch_mission.add_argument(
        "--no-execute",
        action="store_true",
        help="Create mission state and next action candidate without executing it",
    )
    patch_step = sub.add_parser(
        "patch-mission-step",
        help="Advance a patch mission by one bounded inspect/test/draft action",
    )
    patch_step.add_argument("--mission-id")
    patch_step.add_argument(
        "--mode",
        choices=[
            "auto",
            "resume-next",
            "inspect-file",
            "test",
            "draft-patch",
            "from-test-result",
            "apply-patch",
            "pr-summary",
            "git-metadata",
            "git-prep",
            "commit-draft",
            "remote-summary",
            "branch-draft",
            "remote-live",
            "remote-live-summary",
            "push-draft",
            "pr-create-draft",
            "pr-status",
            "pr-update-push-draft",
            "pr-update-status",
            "pr-update-verify",
            "pr-update-next",
            "ci-log-evidence",
            "ci-fix-plan",
            "ci-next-action",
        ],
        default="auto",
    )
    patch_step.add_argument("--target")
    patch_step.add_argument("--draft")
    patch_step.add_argument("--action-id")
    patch_step.add_argument(
        "--no-execute",
        action="store_true",
        help="Create the next action candidate without executing/resolving policy",
    )
    patch_candidate_review = sub.add_parser(
        "patch-mission-candidate-review",
        help="Replay evidence for a Patch Mission repair skill candidate",
    )
    patch_candidate_review.add_argument("candidate_id")
    patch_candidate_review.add_argument(
        "--reason", default="owner requested Patch Mission repair candidate replay"
    )
    patch_candidate_sandbox = sub.add_parser(
        "patch-mission-candidate-sandbox",
        help="Run an owner-approved disposable sandbox validation for a Patch Mission repair skill candidate",
    )
    patch_candidate_sandbox.add_argument("candidate_id")
    patch_candidate_sandbox.add_argument(
        "--reason", default="owner requested Patch Mission repair candidate sandbox"
    )
    patch_candidate_sandbox.add_argument("--owner-approved", action="store_true")
    patch_candidate_validate = sub.add_parser(
        "patch-mission-candidate-validate",
        help="Human-validate a sandboxed Patch Mission repair skill candidate from persisted sandbox digests",
    )
    patch_candidate_validate.add_argument("candidate_id")
    patch_candidate_validate.add_argument(
        "--reason", default="owner validates Patch Mission repair candidate sandbox"
    )
    patch_candidate_validate.add_argument("--human-approved", action="store_true")
    patch_candidate_propose_skill = sub.add_parser(
        "patch-mission-candidate-propose-skill",
        help="Create a non-active declarative skill proposal from a validated Patch Mission repair candidate",
    )
    patch_candidate_propose_skill.add_argument("candidate_id")
    patch_candidate_propose_skill.add_argument(
        "--reason", default="owner requests Patch Mission repair skill proposal"
    )
    patch_skill_sandbox = sub.add_parser(
        "patch-mission-skill-sandbox",
        help="Start sandbox lifecycle evidence for a proposed Patch Mission repair skill",
    )
    patch_skill_sandbox.add_argument("skill_id")
    patch_skill_sandbox.add_argument(
        "--reason", default="owner starts Patch Mission repair skill sandbox"
    )
    patch_skill_validate = sub.add_parser(
        "patch-mission-skill-validate",
        help="Validate a sandboxed Patch Mission repair skill from persisted skill experiment evidence",
    )
    patch_skill_validate.add_argument("skill_id")
    patch_skill_validate.add_argument(
        "--reason", default="owner validates Patch Mission repair skill sandbox"
    )
    patch_skill_approve = sub.add_parser(
        "patch-mission-skill-approve",
        help="Approve a validated Patch Mission repair skill for advisory matching",
    )
    patch_skill_approve.add_argument("skill_id")
    patch_skill_approve.add_argument(
        "--reason", default="owner approves validated Patch Mission repair skill"
    )
    patch_skill_approve.add_argument("--human-approved", action="store_true")
    patch_skill_promotion_review_approve = sub.add_parser(
        "patch-mission-skill-promotion-review-approve",
        help="Approve a Patch Mission repair skill promotion-review candidate without promoting the skill",
    )
    patch_skill_promotion_review_approve.add_argument("candidate_id")
    patch_skill_promotion_review_approve.add_argument(
        "--reason",
        default="owner approves Patch Mission repair skill promotion review",
    )
    patch_skill_promotion_review_approve.add_argument(
        "--human-approved", action="store_true"
    )
    patch_skill_promote = sub.add_parser(
        "patch-mission-skill-promote",
        help="Promote a Patch Mission repair skill from an approved promotion-review candidate",
    )
    patch_skill_promote.add_argument("candidate_id")
    patch_skill_promote.add_argument(
        "--reason", default="owner promotes Patch Mission repair skill"
    )
    patch_skill_promote.add_argument("--human-approved", action="store_true")

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

    outcome_feedback = sub.add_parser(
        "outcome-feedback",
        help="Record owner feedback for an action or the latest next-action candidate",
    )
    outcome_feedback.add_argument(
        "outcome", choices=["helped", "failed", "avoid", "neutral"]
    )
    outcome_feedback.add_argument("--action-id")
    outcome_feedback.add_argument("--note", default="")
    outcome_feedback.add_argument("--evidence", default="{}")
    outcome_feedback.add_argument("--goal-progress-delta", type=float, default=0.0)

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

    ui = sub.add_parser("ui", help="Run loopback-only Owner Console UI")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8766)
    ui.add_argument("--no-browser", action="store_true")
    ui.add_argument("--app-mode", action="store_true",
                    help="Open in app/windowed mode (no address bar)")

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
        if args.command == "health":
            config_path = Path(args.config or default_config_path())
            config = load_or_create_config(config_path)
            db = Database(config.db_path)
            try:
                result = LivingSystem.build_health_snapshot(config, db)
            finally:
                db.close_all()
            print_json(result)
            return 0 if result["status"] in {"OK", "WARN"} else 2
        if args.command == "capabilities":
            print_json(ordinary_user_capability_map())
            return 0
        if args.command == "performance-audit":
            print_json(run_performance_audit(args))
            return 0
        runtime = runtime_from_args(args)
        if args.command == "once":
            print_json(runtime.run_cycle())
        elif args.command == "daemon":
            runtime.run_daemon(args.max_cycles)
        elif args.command == "status":
            print_json(runtime.status())
        elif args.command == "life-state":
            print_json(runtime.life_state(limit=args.limit))
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
        elif args.command == "garbage-audit":
            print_json(
                runtime.garbage_audit(
                    roots=[Path(item) for item in args.path] if args.path else None,
                    max_candidates=args.max_candidates,
                    reason=args.reason,
                    execute_cleanup=args.execute_cleanup,
                    approval_reference=args.approval_reference,
                )
            )
        elif args.command == "garbage-clear-quarantine":
            print_json(
                runtime.clear_garbage_quarantine(
                    audit_id=args.audit_id,
                    approval_reference=args.approval_reference,
                    reason=args.reason,
                )
            )
        elif args.command == "retention-audit":
            print_json(
                runtime.record_retention_audit(
                    reason=args.reason,
                    max_items=args.max_items,
                    max_json_bytes=args.max_json_bytes,
                )
            )
        elif args.command == "soak-audit":
            print_json(
                runtime.run_bounded_soak(
                    cycles=args.cycles,
                    reason=args.reason,
                    max_cycle_seconds=args.max_cycle_seconds,
                )
            )
        elif args.command == "upgrade-drill":
            print_json(
                runtime.run_upgrade_drill(
                    wheel_path=args.wheel,
                    reason=args.reason,
                    disposable_clone=args.disposable_clone,
                )
            )
        elif args.command == "commercial-readiness-audit":
            print_json(
                runtime.record_commercial_readiness_audit(
                    reason=args.reason,
                    rc_min_qualified_measurements=args.rc_min_qualified_measurements,
                )
            )
        elif args.command == "external-product-audit":
            print_json(
                runtime.record_external_product_audit(
                    reason=args.reason,
                    wheel_path=args.wheel or None,
                    exclude_multi_user=not bool(args.include_multi_user),
                )
            )
        elif args.command == "m7-self-check":
            print_json(
                runtime.record_m7_self_check_audit(
                    reason=args.reason,
                    m7_root=args.m7_root or None,
                )
            )
        elif args.command == "longitudinal-start":
            print_json(
                runtime.start_longitudinal_protocol(
                    host_id=args.host_id,
                    baseline_commit=args.baseline_commit,
                    duration_days=args.duration_days,
                    reason=args.reason,
                )
            )
        elif args.command == "longitudinal-record":
            print_json(
                runtime.record_longitudinal_measurement(
                    protocol_id=args.protocol_id,
                    task_class=args.task_class,
                    success=bool(args.success),
                    corrections=args.corrections,
                    cost=args.cost,
                    latency_seconds=args.latency_seconds,
                    memory_benefit=args.memory_benefit,
                    skill_reuse=args.skill_reuse,
                    task_reference=args.task_reference,
                    owner_review=args.owner_review,
                    reason=args.reason,
                )
            )
        elif args.command == "longitudinal-report":
            print_json(
                runtime.compile_longitudinal_report(
                    protocol_id=args.protocol_id,
                    baseline_success_rate=args.baseline_success_rate,
                    max_regression=args.max_regression,
                    reason=args.reason,
                )
            )
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
        elif args.command == "patch-mission":
            print_json(
                runtime.start_patch_mission(
                    repo_path=args.repo_path,
                    mission=args.mission,
                    execute_first_action=not bool(args.no_execute),
                )
            )
        elif args.command == "patch-mission-step":
            print_json(
                runtime.continue_patch_mission(
                    mission_id=args.mission_id,
                    mode=args.mode,
                    target=args.target,
                    draft=args.draft,
                    action_id=args.action_id,
                    execute=not bool(args.no_execute),
                )
            )
        elif args.command == "patch-mission-candidate-review":
            print_json(
                runtime.review_patch_mission_repair_skill_candidate(
                    candidate_id=args.candidate_id,
                    reason=args.reason,
                )
            )
        elif args.command == "patch-mission-candidate-sandbox":
            print_json(
                runtime.sandbox_patch_mission_repair_skill_candidate(
                    candidate_id=args.candidate_id,
                    reason=args.reason,
                    owner_approved=bool(args.owner_approved),
                )
            )
        elif args.command == "patch-mission-candidate-validate":
            print_json(
                runtime.validate_patch_mission_repair_skill_candidate(
                    candidate_id=args.candidate_id,
                    reason=args.reason,
                    human_approved=bool(args.human_approved),
                )
            )
        elif args.command == "patch-mission-candidate-propose-skill":
            print_json(
                runtime.propose_patch_mission_repair_skill_from_candidate(
                    candidate_id=args.candidate_id,
                    reason=args.reason,
                )
            )
        elif args.command == "patch-mission-skill-sandbox":
            print_json(
                runtime.start_patch_mission_repair_skill_sandbox(
                    skill_id=args.skill_id,
                    reason=args.reason,
                )
            )
        elif args.command == "patch-mission-skill-validate":
            print_json(
                runtime.validate_patch_mission_repair_skill_sandbox(
                    skill_id=args.skill_id,
                    reason=args.reason,
                )
            )
        elif args.command == "patch-mission-skill-approve":
            print_json(
                runtime.approve_patch_mission_repair_skill(
                    skill_id=args.skill_id,
                    reason=args.reason,
                    human_approved=args.human_approved,
                )
            )
        elif args.command == "patch-mission-skill-promotion-review-approve":
            print_json(
                runtime.approve_patch_mission_repair_skill_promotion_review(
                    candidate_id=args.candidate_id,
                    reason=args.reason,
                    human_approved=args.human_approved,
                )
            )
        elif args.command == "patch-mission-skill-promote":
            print_json(
                runtime.promote_patch_mission_repair_skill_from_review(
                    candidate_id=args.candidate_id,
                    reason=args.reason,
                    human_approved=args.human_approved,
                )
            )
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
        elif args.command == "outcome-feedback":
            evidence = json.loads(args.evidence)
            if not isinstance(evidence, dict):
                raise ValueError("evidence must be a JSON object")
            print_json(
                runtime.record_owner_outcome_feedback(
                    action_id=args.action_id,
                    outcome=args.outcome,
                    owner_note=args.note,
                    evidence=evidence,
                    goal_progress_delta=args.goal_progress_delta,
                )
            )
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
            if args.query:
                facts = [
                    {"kind": "world_fact", **item}
                    for item in runtime.world.query(args.query, args.limit)
                ]
                relationships = runtime.relationships.query(args.query, args.limit)
                combined = sorted(
                    [*facts, *relationships],
                    key=lambda item: float(item.get("score", 0.0)),
                    reverse=True,
                )[: args.limit]
                print_json(combined)
            else:
                print_json(runtime.world.active_facts(limit=args.limit))
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
        elif args.command == "ui":
            from .ui_server import WLSUIServer, _open_browser

            ui_server = WLSUIServer(runtime, args.host, args.port)
            ui_server.build()
            print_json(
                {
                    "url": ui_server.bootstrap_url,
                    "session_scope": "ephemeral-process-and-browser-tab",
                    "authority": "canonical LivingSystem",
                    "projection_only": True,
                }
            )
            if not args.no_browser:
                _open_browser(ui_server.bootstrap_url,
                              app_mode=bool(getattr(args, "app_mode", False)))
            ui_server.serve_forever()
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


def run_performance_audit(args) -> dict[str, Any]:
    from time import perf_counter
    from .ui_server import WLSUIServer

    config_path = Path(args.config or default_config_path())
    config = load_or_create_config(config_path)
    if not str(args.reason).strip():
        raise ValueError("performance audit reason is required")
    samples = max(1, min(20, int(args.samples)))
    budgets = DEFAULT_PERFORMANCE_BUDGETS_SECONDS

    health_samples: list[float] = []
    for _ in range(samples):
        db = Database(config.db_path)
        try:
            started = perf_counter()
            LivingSystem.build_health_snapshot(config, db)
            health_samples.append(perf_counter() - started)
        finally:
            db.close_all()

    runtime = None
    try:
        started = perf_counter()
        runtime = LivingSystem(config)
        runtime_init_seconds = perf_counter() - started
        started = perf_counter()
        runtime.garbage_audit(max_candidates=200, reason=args.reason)
        garbage_audit_seconds = perf_counter() - started

        started = perf_counter()
        self_check(runtime)
        self_check_seconds = perf_counter() - started

        def request_ui(
            server: WLSUIServer,
            method: str,
            path: str,
            *,
            body: dict[str, Any] | None = None,
        ) -> tuple[float, dict[str, Any]]:
            headers = {"Authorization": f"Bearer {server.token}"}
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers.update(
                    {
                        "Content-Type": "application/json",
                        "X-WLS-UI": "1",
                        "Origin": f"http://127.0.0.1:{server.bound_port}",
                    }
                )
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.bound_port, timeout=10
            )
            try:
                started = perf_counter()
                connection.request(method, path, body=payload, headers=headers)
                response = connection.getresponse()
                raw = response.read()
                elapsed = perf_counter() - started
                if response.status >= 400:
                    raise RuntimeError(f"{path} returned HTTP {response.status}")
                data = json.loads(raw.decode("utf-8")) if raw else {}
                return elapsed, data
            finally:
                connection.close()

        ui_server = WLSUIServer(runtime, port=0)
        ui_server.start_in_thread()
        ui_measurements: dict[str, float] = {}
        try:
            for name, path in (
                ("ui_api_health", "/api/health"),
                ("ui_api_bootstrap", "/api/bootstrap"),
                ("ui_api_product", "/api/product"),
            ):
                elapsed, _ = request_ui(ui_server, "GET", path)
                ui_measurements[name] = elapsed
        finally:
            ui_server.shutdown()

        measurements = [
            PerformanceMeasurement(
                "health_snapshot",
                health_samples,
                budgets["health_snapshot"],
            ),
            PerformanceMeasurement(
                "runtime_init",
                [runtime_init_seconds],
                budgets["runtime_init"],
            ),
            PerformanceMeasurement(
                "garbage_audit",
                [garbage_audit_seconds],
                budgets["garbage_audit"],
            ),
            PerformanceMeasurement(
                "self_check",
                [self_check_seconds],
                budgets["self_check"],
            ),
            PerformanceMeasurement(
                "ui_api_health",
                [ui_measurements["ui_api_health"]],
                budgets["ui_api_health"],
            ),
            PerformanceMeasurement(
                "ui_api_bootstrap",
                [ui_measurements["ui_api_bootstrap"]],
                budgets["ui_api_bootstrap"],
            ),
            PerformanceMeasurement(
                "ui_api_product",
                [ui_measurements["ui_api_product"]],
                budgets["ui_api_product"],
            ),
        ]
        if bool(getattr(args, "include_write_workflows", False)):
            write_measurements = _measure_disposable_garbage_review_writes(
                request_ui=request_ui,
                reason=args.reason,
                budgets=budgets,
            )
            measurements.extend(write_measurements)
        if bool(getattr(args, "include_rollback_workflow", False)):
            measurements.extend(
                _measure_disposable_rollback_workflow(
                    reason=args.reason,
                    budgets=budgets,
                )
            )

        return runtime.record_performance_budget_audit(
            measurements=measurements,
            reason=args.reason,
        )
    finally:
        if runtime is not None:
            try:
                runtime.lease.release()
            except Exception:
                pass
            runtime.db.close_all()


def _measure_disposable_garbage_review_writes(
    *,
    request_ui,
    reason: str,
    budgets: dict[str, float],
) -> list[PerformanceMeasurement]:
    from .ui_server import WLSUIServer

    with tempfile.TemporaryDirectory(prefix="wls-write-workflow-") as temp_root:
        home = Path(temp_root) / "home"
        config = default_config(home)
        (home / "build" / "artifact").mkdir(parents=True, exist_ok=True)
        (home / "build" / "artifact" / "bundle.tmp").write_text(
            "temporary build output", encoding="utf-8"
        )
        (home / ".pytest_cache").mkdir(parents=True, exist_ok=True)
        (home / ".pytest_cache" / "README.log").write_text(
            "temporary cache output", encoding="utf-8"
        )
        runtime = LivingSystem(config)
        try:
            ui_server = WLSUIServer(runtime, port=0)
            ui_server.start_in_thread()
            try:
                scan_seconds, audit = request_ui(
                    ui_server,
                    "POST",
                    "/api/garbage-audit/scan",
                    body={
                        "reason": f"{reason} disposable write scan",
                        "max_candidates": 20,
                    },
                )
                if int(audit.get("candidate_count", 0)) < 1:
                    raise RuntimeError("disposable garbage scan found no candidates")
                cleanup_seconds, cleanup = request_ui(
                    ui_server,
                    "POST",
                    "/api/garbage-audit/cleanup",
                    body={
                        "reason": f"{reason} disposable write cleanup",
                        "approval_reference": "performance-audit-disposable-owner",
                        "max_candidates": 20,
                    },
                )
                if cleanup.get("cleanup_executed") is not True:
                    raise RuntimeError("disposable garbage cleanup was not executed")
                clear_seconds, clear = request_ui(
                    ui_server,
                    "POST",
                    "/api/garbage-audit/quarantine-clear",
                    body={
                        "audit_id": str(cleanup.get("audit_id", "")),
                        "approval_reference": "performance-audit-disposable-clear",
                        "reason": f"{reason} disposable quarantine clear",
                    },
                )
                if clear.get("status") not in {
                    "QUARANTINE_CLEARED",
                    "QUARANTINE_ALREADY_ABSENT",
                }:
                    raise RuntimeError("disposable quarantine clear did not finish")
            finally:
                ui_server.shutdown()
        finally:
            runtime.db.close_all()
    return [
        PerformanceMeasurement(
            "ui_api_garbage_scan",
            [scan_seconds],
            budgets["ui_api_garbage_scan"],
        ),
        PerformanceMeasurement(
            "ui_api_garbage_cleanup",
            [cleanup_seconds],
            budgets["ui_api_garbage_cleanup"],
        ),
        PerformanceMeasurement(
            "ui_api_garbage_clear",
            [clear_seconds],
            budgets["ui_api_garbage_clear"],
        ),
    ]


def _measure_disposable_rollback_workflow(
    *,
    reason: str,
    budgets: dict[str, float],
) -> list[PerformanceMeasurement]:
    from time import perf_counter

    with tempfile.TemporaryDirectory(prefix="wls-rollback-workflow-") as temp_root:
        root = Path(temp_root)
        home = root / "home"
        wheel = root / "dist" / "workstation_living_system-test.whl"
        wheel.parent.mkdir(parents=True, exist_ok=True)
        wheel.write_bytes(b"disposable rollback performance wheel")
        runtime = LivingSystem(default_config(home))
        try:
            started = perf_counter()
            receipt = runtime.run_upgrade_drill(
                wheel_path=wheel,
                reason=f"{reason} disposable rollback performance",
                disposable_clone=True,
            )
            elapsed = perf_counter() - started
            clone = receipt.get("disposable_clone_rollback")
            if receipt.get("status") != "UPGRADE_DRILL_PASSED":
                raise RuntimeError("disposable rollback performance drill failed")
            if not isinstance(clone, dict) or clone.get("passed") is not True:
                raise RuntimeError("disposable clone rollback did not pass")
            if clone.get("clone_home_removed") is not True:
                raise RuntimeError("disposable clone rollback left a clone home")
        finally:
            runtime.db.close_all()
    return [
        PerformanceMeasurement(
            "rollback_disposable_clone",
            [elapsed],
            budgets["rollback_disposable_clone"],
        )
    ]


if __name__ == "__main__":
    raise SystemExit(main())
