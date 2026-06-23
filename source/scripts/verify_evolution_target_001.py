from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import argparse
import json
import platform
import sys

from wls import LivingSystem, __version__
from wls.config import default_config
from wls.schemas import ActionSpec, Plan, RiskLevel, new_id, utc_now


def record_failure(
    runtime: LivingSystem,
    *,
    tool: str,
    arguments: dict[str, Any],
    acceptance: list[str],
    purpose: str,
) -> str:
    cycle_id = new_id("cycle")
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    action = ActionSpec(
        tool=tool,
        arguments=arguments,
        purpose=purpose,
        expected_result="failure remains visible",
        risk=RiskLevel.READ,
        acceptance=acceptance,
    )
    plan = Plan(rationale="EVOLUTION-TARGET-001 controlled reproduction", actions=[action])
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    outcome = runtime._execute_plan(plan)[0]
    runtime.db.execute(
        "UPDATE cycles SET finished_at=?,status='SUCCEEDED' WHERE cycle_id=?",
        (utc_now(), cycle_id),
    )
    if outcome.get("success"):
        raise RuntimeError("controlled failure unexpectedly succeeded")
    return action.action_id


def execute_case(
    root: Path,
    *,
    name: str,
    tool: str,
    arguments: dict[str, Any],
    acceptance: list[str],
    purpose: str,
    strategy: str,
    expect_retain: bool,
) -> dict[str, Any]:
    config = default_config(root)
    config.sensors = []
    config.max_actions_per_cycle = 4
    config.sleep_after_idle_cycles = 100
    runtime = LivingSystem(config)
    failure_ids = [
        record_failure(
            runtime,
            tool=tool,
            arguments=arguments,
            acceptance=acceptance,
            purpose=purpose,
        )
        for _ in range(3)
    ]
    candidate_ids = runtime.learning.create_failure_candidates(minimum_repeats=3)
    if len(candidate_ids) != 1:
        raise RuntimeError(f"expected one failure candidate, found {candidate_ids}")
    candidate_id = candidate_ids[0]
    recovery = runtime.growth.run_recovery_experiment(candidate_id, strategy)
    proposal = runtime.growth.propose_skill_from_recovery(
        candidate_id, recovery["experiment_id"]
    )
    validation = runtime.growth.validate_skill(proposal["growth_cycle_id"])
    promotion = runtime.growth.approve_and_promote(
        proposal["growth_cycle_id"],
        actor="owner-authorized-local-verification-fixture",
        authorization_reference="EVOLUTION-TARGET-001/local-verification",
        human_approved=True,
    )
    reuse = runtime.growth.reuse_on_runtime_task(proposal["growth_cycle_id"])
    rollback = None
    expected_decision = "RETAIN" if expect_retain else "ROLLBACK_REQUIRED"
    if reuse["measurement"]["decision"] != expected_decision:
        raise RuntimeError(
            f"expected {expected_decision}, got {reuse['measurement']['decision']}"
        )
    if not expect_retain:
        rollback = runtime.growth.rollback(
            proposal["growth_cycle_id"],
            actor="owner-authorized-local-verification-fixture",
            authorization_reference="EVOLUTION-TARGET-001/local-rollback-verification",
            human_approved=True,
        )
        if not rollback["rollback"]["passed"]:
            raise RuntimeError("rollback verification failed")
    integrity = runtime.verify_integrity(full=True)
    if not integrity["ok"]:
        raise RuntimeError(f"integrity failed: {integrity}")
    return {
        "case": name,
        "failure_action_ids": failure_ids,
        "candidate_id": candidate_id,
        "recovery": {
            key: recovery[key]
            for key in (
                "experiment_id",
                "status",
                "baseline_pass_rate",
                "candidate_pass_rate",
                "improved_cases",
                "regressions",
                "result_sha256",
            )
        },
        "growth_cycle_id": proposal["growth_cycle_id"],
        "skill_id": proposal["skill_id"],
        "validation": {
            key: validation[key]
            for key in (
                "experiment_id",
                "status",
                "passed_cases",
                "candidate_cases",
                "regressions",
                "result_sha256",
            )
        },
        "promotion": promotion,
        "reuse": reuse,
        "rollback": rollback,
        "integrity": integrity,
        "evidence_chain": runtime.ledger.verify(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="wls-evolution-target-001-") as temporary:
        root = Path(temporary)
        retain_case = execute_case(
            root / "retain",
            name="contract-recovery-retain",
            tool="noop",
            arguments={"reason": "repeatable contract failure"},
            acceptance=["output contains impossible"],
            purpose="recover repeatable noop workflow",
            strategy="contract_recovery",
            expect_retain=True,
        )
        rollback_root = root / "rollback"
        rollback_case = execute_case(
            rollback_root,
            name="fixture-recovery-real-task-rollback",
            tool="read_file",
            arguments={
                "path": str(rollback_root / "never-created.txt"),
                "max_bytes": 4096,
            },
            acceptance=[
                "output contains path",
                "output contains text or binary marker",
            ],
            purpose="read a file absent from the real runtime environment",
            strategy="fixture_recovery",
            expect_retain=False,
        )
    report = {
        "target": "EVOLUTION-TARGET-001",
        "scope": "local isolated canonical-runtime verification",
        "generated_at": utc_now(),
        "wls_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "claim_boundary": (
            "Verifies the software lifecycle locally. It does not establish long-term "
            "external effectiveness, consciousness, AGI, or production readiness."
        ),
        "cases": [retain_case, rollback_case],
        "passed": True,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
