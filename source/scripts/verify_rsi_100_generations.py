"""Run exactly 100 real, deterministic artifact-selection generations.

This is a harness stress test, NOT autonomous model learning or external RSI.
Each candidate is a distinct JSON artifact, graded by a separate deterministic
oracle. No provider credentials, arbitrary subprocesses, or live WLS writes.
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

TARGETS = {str(i): i * i for i in range(100)}
# Bind the policy to the entire fixed evaluator implementation, not just cases.
SPEC_DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def run_hundred_generations(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    artifact_root = root / "artifacts"
    artifact_root.mkdir(exist_ok=True)
    db = Database(root / "evolution.sqlite")
    ledger = EvidenceLedger(db, root / "evidence.key")
    runner = RsiEvolutionPilot(db, ledger)

    # The external grader is immutable over the entire demonstration.
    policy = ExperimentPolicy(
        direction="maximize", minimum_gain=0.005,
        hard_gates={"incorrect_answers": 0.0},
        max_rounds=100, max_failures=0, evaluator_digest=SPEC_DIGEST,
    )

    def store(artifact_id: str, mapping: dict[str, int]) -> str:
        blob = json.dumps(mapping, sort_keys=True, separators=(",", ":"))
        output = artifact_root / (artifact_id + ".json")
        if output.exists():
            raise ValueError("artifact identity collision")
        output.write_text(blob, encoding="utf-8")
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    digests: dict[str, str] = {"baseline": store("baseline", {})}

    def propose(parent: str, generation: int, branch: int) -> str:
        parent_bytes = (artifact_root / (parent + ".json")).read_bytes()
        if hashlib.sha256(parent_bytes).hexdigest() != digests[parent]:
            raise ValueError("parent artifact mutated after evaluation")
        data = json.loads(parent_bytes)
        if branch == 1:
            item = generation - 1
            data[str(item)] = item * item
        artifact_id = f"g{generation:03d}.b{branch}"
        digests[artifact_id] = store(artifact_id, data)
        return artifact_id

    def evaluate(artifact_id: str) -> MetricResult:
        raw = (artifact_root / (artifact_id + ".json")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digests[artifact_id]:
            raise ValueError("candidate bytes changed before evaluation")
        data = json.loads(raw)
        correct = sum(data.get(key) == answer for key, answer in TARGETS.items())
        incorrect = sum(key not in TARGETS or TARGETS[key] != value for key, value in data.items())
        return MetricResult(
            primary=correct / len(TARGETS),
            gates={"incorrect_answers": float(incorrect)},
            evaluator_digest=SPEC_DIGEST,
        )

    runner.start(
        run_id="rsi-harness-100", policy=policy,
        baseline_id="baseline", baseline=evaluate("baseline"),
        branches=2, max_no_gain_rounds=2,
    )
    finished = runner.run_bounded(
        "rsi-harness-100", policy=policy, propose=propose, evaluate=evaluate
    )

    assert finished["status"] == "COMPLETE", finished["status"]
    assert finished["generation"] == 100
    assert finished["champion_id"] == "g100.b1"
    assert len(finished["history"]) == 100
    assert len(finished["candidate_ids"]) == 201
    assert evaluate(finished["champion_id"]).primary == 1.0
    for generation, receipt in enumerate(finished["history"], start=1):
        assert receipt["generation"] == generation
        assert receipt["champion"] == f"g{generation:03d}.b1"
        assert receipt["candidates"] == [
            f"g{generation:03d}.b0", f"g{generation:03d}.b1"
        ]
        if generation > 1:
            assert receipt["parent"] == f"g{generation - 1:03d}.b1"
    valid, evidence = ledger.verify()
    assert valid, evidence
    assert evidence["records"] == 401, evidence
    db.close_all()

    # Recreate the WLS Database and EvidenceLedger from disk to verify recovery.
    reopened_db = Database(root / "evolution.sqlite")
    reopened_ledger = EvidenceLedger(reopened_db, root / "evidence.key")
    persisted = RsiEvolutionPilot(reopened_db, reopened_ledger).read("rsi-harness-100")
    assert persisted is not None and persisted["champion_id"] == finished["champion_id"]
    assert reopened_ledger.verify()[0]
    reopened_db.close_all()

    return {
        "run_id": "rsi-harness-100",
        "claim_ceiling": "deterministic harness stress test; not autonomous RSI",
        "generations_verified": 100,
        "candidates_measured": 200,
        "distinct_artifacts": len(digests),
        "passed_final_cases": 100,
        "total_final_cases": 100,
        "evidence_records": evidence["records"],
        "evidence_head": evidence["head"],
        "evaluator_digest": SPEC_DIGEST,
        "champion": finished["champion_id"],
        "champion_sha256": digests[finished["champion_id"]],
        "reopened_evidence_verified": True,
        "lineage": [
            {"generation": item["generation"], "parent": item["parent"],
             "winner": item["champion"],
             "sha256": digests[item["champion"]]}
            for item in finished["history"]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="wls-rsi-harness-") as temp:
        summary = run_hundred_generations(Path(temp))
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({k: v for k, v in summary.items() if k != "lineage"}, sort_keys=True))
    print("VERIFIED: 100 distinct inherited generations; 200 measured artifacts; 401 WLS evidence records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
