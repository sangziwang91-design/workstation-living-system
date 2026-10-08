"""Execute 50 distinct WLS RSI protocol fault-injection experiments.

Ten fault families times five different baseline values; all use the real
RsiEvolutionPilot, Database and EvidenceLedger. No LLM/provider is invoked,
and no untrusted candidate source code is run on the host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.experiment_decision import ExperimentPolicy, MetricResult
from wls.rsi_evolution import RsiEvolutionPilot

FAMILIES = (
    "gain", "no_gain", "hard_gate", "nan_primary", "inf_gate",
    "digest_mismatch", "duplicate_id", "changed_policy",
    "proposer_exception", "evaluator_exception",
)
DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def policy() -> ExperimentPolicy:
    return ExperimentPolicy(
        direction="maximize",
        minimum_gain=0.03,
        hard_gates={"regressions": 0.0},
        max_rounds=1,
        max_failures=4,
        evaluator_digest=DIGEST,
    )


def metric(value: float, *, regressions: float = 0.0, digest: str = DIGEST) -> MetricResult:
    return MetricResult(
        primary=value,
        gates={"regressions": regressions},
        evaluator_digest=digest,
    )


def run_scenario(
    pilot: RsiEvolutionPilot,
    family: str,
    seed: int,
) -> dict[str, Any]:
    baseline = 0.20 + seed / 100
    run_id = f"fault-{family}-{seed}"
    subject = f"{run_id}-baseline"
    original_policy = policy()
    pilot.start(
        run_id=run_id,
        policy=original_policy,
        baseline_id=subject,
        baseline=metric(baseline),
        branches=2,
    )
    calls = {"proposal": 0, "evaluation": 0}

    def propose(parent: str, generation: int, branch: int) -> str:
        assert parent == subject and generation == 1
        calls["proposal"] += 1
        if family == "proposer_exception" and branch == 1:
            raise RuntimeError("injected proposer exception")
        if family == "duplicate_id":
            return f"{run_id}-repeated"
        return f"{run_id}-candidate-{branch}"

    def evaluate(candidate_id: str) -> MetricResult:
        calls["evaluation"] += 1
        branch = 0 if candidate_id.endswith("-0") or family == "duplicate_id" else 1
        if family == "evaluator_exception" and branch == 0:
            raise RuntimeError("injected independent evaluator exception")
        if family == "gain":
            return metric(baseline + 0.05 + 0.01 * branch)
        if family == "no_gain":
            return metric(baseline + 0.01)
        if family == "hard_gate":
            return metric(baseline + 0.10, regressions=1.0)
        if family == "nan_primary":
            return metric(float("nan")) if branch == 0 else metric(baseline + 0.01)
        if family == "inf_gate":
            return metric(baseline + 0.10, regressions=float("inf")) if branch == 0 else metric(baseline + 0.01)
        if family == "digest_mismatch":
            return metric(baseline + 0.10, digest="b" * 64) if branch == 0 else metric(baseline + 0.01)
        if family == "duplicate_id":
            return metric(baseline + 0.05)
        if family == "proposer_exception":
            return metric(baseline + 0.05)
        raise AssertionError(f"unexpected evaluator scenario: {family}")

    observed_error = ""
    if family == "changed_policy":
        altered = ExperimentPolicy(
            "maximize", 0.9, {"regressions": 0.0}, 1, 4, DIGEST
        )
        try:
            pilot.advance(run_id, policy=altered, propose=propose, evaluate=evaluate)
        except ValueError as exc:
            observed_error = str(exc)
            assert "policy changed" in observed_error
        else:
            raise AssertionError("changed-policy mutation should be rejected")
    else:
        try:
            pilot.advance(run_id, policy=original_policy, propose=propose, evaluate=evaluate)
        except (ValueError, RuntimeError) as exc:
            observed_error = str(exc)
            if family not in {"duplicate_id", "proposer_exception", "evaluator_exception"}:
                raise
        else:
            if family in {"duplicate_id", "proposer_exception", "evaluator_exception"}:
                raise AssertionError(f"{family} must block the generation")

    state = pilot.read(run_id)
    assert state is not None
    expected_status = (
        "READY" if family == "changed_policy" else
        "BLOCKED" if family in {"duplicate_id", "proposer_exception", "evaluator_exception"}
        else "COMPLETE"
    )
    assert state["status"] == expected_status, (family, state["status"])
    expected_champion = (
        f"{run_id}-candidate-1" if family == "gain" else subject
    )
    assert state["champion_id"] == expected_champion
    assert state["live_promotion"] is False
    assert state["policy_digest"] == original_policy.digest()
    assert state["generation"] == (0 if expected_status != "COMPLETE" else 1)

    # Strict JSON serializability is part of the audit contract.
    state_blob = json.dumps(state, allow_nan=False, sort_keys=True)
    assert "NaN" not in state_blob and "Infinity" not in state_blob
    if family == "changed_policy":
        assert calls == {"proposal": 0, "evaluation": 0}
    if family == "duplicate_id":
        assert calls == {"proposal": 2, "evaluation": 1}
    if family == "proposer_exception":
        assert calls == {"proposal": 2, "evaluation": 1}
    if family == "evaluator_exception":
        assert calls == {"proposal": 1, "evaluation": 1}

    return {
        "case": run_id,
        "family": family,
        "seed": seed,
        "passed": True,
        "status": state["status"],
        "champion": state["champion_id"],
        "proposal_calls": calls["proposal"],
        "evaluation_calls": calls["evaluation"],
        "blocked_reason_type": state.get("blocker"),
        "injected_error": observed_error[:150],
        "state_sha256": hashlib.sha256(state_blob.encode("utf-8")).hexdigest(),
    }


def run_fifty(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    db = Database(root / "state.sqlite")
    ledger = EvidenceLedger(db, root / "evidence.key")
    pilot = RsiEvolutionPilot(db, ledger)
    results = [
        run_scenario(pilot, family, seed)
        for family in FAMILIES
        for seed in range(5)
    ]
    assert len(results) == 50
    assert len({item["case"] for item in results}) == 50
    assert all(item["passed"] for item in results)

    # Audit every event's raw JSON: even a signed NaN receipt is a failure.
    rows = db.query_all("SELECT payload_json FROM evidence ORDER BY seq ASC")
    def reject_nonfinite(value: str) -> Any:
        raise ValueError(f"non-finite evidence payload: {value}")
    for row in rows:
        json.loads(row["payload_json"], parse_constant=reject_nonfinite)

    ok, first_verification = ledger.verify()
    assert ok, first_verification
    db.close_all()
    reopened_db = Database(root / "state.sqlite")
    reopened = RsiEvolutionPilot(
        reopened_db, EvidenceLedger(reopened_db, root / "evidence.key")
    )
    for item in results:
        state = reopened.read(item["case"])
        assert state is not None and state["status"] == item["status"]
        assert state["champion_id"] == item["champion"]
    verified_again, second_verification = reopened.ledger.verify()
    assert verified_again, second_verification
    reopened_db.close_all()
    return {
        "claim_ceiling": "50 real protocol fault-injection scenarios; no model RSI evidence",
        "rounds_completed": 50,
        "families": len(FAMILIES),
        "seeds_per_family": 5,
        "passes": sum(item["passed"] for item in results),
        "failures": 0,
        "ledger_records": first_verification["records"],
        "ledger_head": first_verification["head"],
        "ledger_reopen_verified": True,
        "evaluator_digest": DIGEST,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="wls-rsi-50-") as dirname:
        summary = run_fifty(Path(dirname))
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, sort_keys=True))
    print("VERIFIED: 50 actual protocol fault-injection cases and HMAC audit-chain replay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
