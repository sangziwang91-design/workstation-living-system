from __future__ import annotations

from pathlib import Path
import argparse
import json
import tempfile

from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.schemas import ActionSpec, Event, Plan, RiskLevel, utc_now
from wls.shadow_causal import ShadowCausalAnalyzer


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    db = Database(output / "wls.db")
    ledger = EvidenceLedger(db, output / "evidence.key")
    analyzer = ShadowCausalAnalyzer(db, ledger)

    cycle_id = "local_shadow_check"
    event = Event(
        event_type="service_failure",
        source="local_verifier",
        payload={"status": "failed", "error": "signature mismatch"},
    )
    db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    db.execute(
        """INSERT INTO events(event_id,event_type,source,payload_json,salience_hint,
        occurred_at,dedupe_key,status,attempts) VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            event.event_id, event.event_type, event.source,
            json.dumps(event.payload, sort_keys=True), event.salience_hint,
            event.occurred_at, event.dedupe_key, "PROCESSED", 0,
        ),
    )
    plan = Plan(
        rationale="local verification",
        actions=[ActionSpec(
            tool="read_file",
            arguments={"path": "status.txt"},
            purpose="read status",
            expected_result="readable",
            risk=RiskLevel.READ,
        )],
    )
    frozen = analyzer.freeze(
        cycle_id=cycle_id,
        plan=plan,
        event_ids=[event.event_id],
        contradictions=[],
    )
    with db.transaction() as cx:
        cx.execute(
            "INSERT INTO plans(plan_id,cycle_id,plan_json,status,created_at) VALUES (?,?,?,?,?)",
            (plan.plan_id, cycle_id, json.dumps(plan.to_dict()), "PLANNED", plan.created_at),
        )
        action = plan.actions[0]
        cx.execute(
            """INSERT INTO actions(action_id,plan_id,tool,arguments_json,purpose,
            expected_result,risk,acceptance_json,idempotency_key,status,side_effect_class)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                action.action_id, plan.plan_id, action.tool,
                json.dumps(action.arguments), action.purpose, action.expected_result,
                action.risk.value, json.dumps(action.acceptance),
                action.idempotency_key, "SUCCEEDED", "none",
            ),
        )
        ledger.append(
            "action_completed",
            {"action_id": action.action_id, "status": "SUCCEEDED", "payload": {}},
            cx,
        )
    resolved = analyzer.resolve(
        plan_id=plan.plan_id,
        outcomes=[{"action_id": plan.actions[0].action_id, "success": True}],
    )
    integrity_ok, integrity_detail = db.integrity_check()
    ledger_ok, ledger_detail = ledger.verify()
    report = {
        "result": "PASS" if (
            resolved["frozen_before_outcome"] and integrity_ok and ledger_ok
        ) else "FAIL",
        "active_spaces": list(frozen.active_spaces),
        "candidate_count": frozen.candidate_count,
        "frozen_evidence_seq": resolved["frozen_evidence_seq"],
        "first_action_outcome_seq": resolved["first_action_outcome_seq"],
        "outcome_class": resolved["outcome_class"],
        "sqlite_integrity": integrity_detail,
        "evidence_chain": ledger_detail,
        "claim_ceiling": "LOCAL_OFFLINE_FIXTURE_ONLY",
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        report = run(args.output)
    else:
        with tempfile.TemporaryDirectory(prefix="wls-shadow-") as directory:
            report = run(Path(directory))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
