"""Replay genuine pinned-Qwen self-code replies without another model invocation.

The trusted behavior oracle and C1 isolated executor are imported from the
existing self-code experiment. Only model-origin raw bytes from the pinned
real GitHub run can be replayed. No host execution, no scoring changes.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from measure_rsi_self_code_model import (
    TARGET, FROZEN_BLOB_SHA, SCHEMA, ARMS,
    _pin_source, _grade_source, _PROBE_BOOT,
    _PROBE_BEHAVIOR, _PROBE_REGRESSION,
)
from measure_task_judgment_local_model import LARGER_MODEL_ID, LARGER_MODEL_SHA256
from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.experiment_decision import ExperimentPolicy, MetricResult
from wls.rsi_artifact_gate import RsiArtifactGate
from wls.rsi_evolution import RsiEvolutionPilot

REPLAY_SCHEMA = "wls.rsi.real_qwen_self_code_replay.v1"
PINNED_MODEL_RUN = 42486361106


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, item in pairs:
        if name in out:
            raise ValueError("duplicate key in untrusted model JSON")
        out[name] = item
    return out


def _extract_source(raw: str) -> str:
    envelope = raw.strip()
    fence = chr(96) * 3
    if envelope.startswith(fence + "json\n") and envelope.endswith("\n" + fence):
        envelope = envelope[len(fence + "json\n"):-len("\n" + fence)]
    payload = json.loads(
        envelope,
        object_pairs_hook=_unique_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    if (not isinstance(payload, dict) or set(payload) != {"files"}
            or not isinstance(payload["files"], dict)
            or set(payload["files"]) != {TARGET}
            or not isinstance(payload["files"][TARGET], str)):
        raise ValueError("model reply does not contain exactly one allowed source file")
    return payload["files"][TARGET]


def replay(previous_report: Path, image: str, archive_root: Path) -> dict[str, Any]:
    prior = json.loads(previous_report.read_text(encoding="utf-8"))
    if (not isinstance(prior, dict) or prior.get("schema") != SCHEMA
            or prior.get("status") != "MODEL_PAIRED_MEASURED"
            or prior.get("model_calls_observed") != 2
            or prior.get("model_id") != LARGER_MODEL_ID
            or prior.get("model_sha256") != LARGER_MODEL_SHA256
            or prior.get("base_git_blob") != FROZEN_BLOB_SHA
            or prior.get("image") != image
            or set(prior.get("arms", {})) != set(ARMS)):
        raise ValueError("not the exact pinned two-arm genuine model evidence")
    expected_evaluator = sha256(
        (TARGET + FROZEN_BLOB_SHA + _PROBE_BOOT
         + _PROBE_BEHAVIOR + _PROBE_REGRESSION).encode()
    ).hexdigest()
    if prior.get("evaluator_digest") != expected_evaluator:
        raise ValueError("frozen grader bytes changed since authentic inference")
    baseline_source = _pin_source()
    baseline = _grade_source(baseline_source.decode("utf-8"), image)
    if baseline["behavior"] == "EXIT_0" or baseline["regression"] != "EXIT_0":
        raise ValueError("frozen defect no longer measurable")
    archive_root.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    seen: set[str] = set()
    for arm in ARMS:
        previous_arm = prior["arms"][arm]
        receipts = previous_arm.get("model_receipts")
        if not isinstance(receipts, list) or len(receipts) != 1:
            raise ValueError("missing authentic model-call receipt")
        receipt = receipts[0]
        raw = receipt.get("raw_model_reply")
        if (not isinstance(raw, str)
                or sha256(raw.encode("utf-8")).hexdigest() != receipt.get("response_sha256")
                or len(raw.encode("utf-8")) != receipt.get("response_bytes")
                or receipt.get("input_tokens") is None
                or receipt.get("output_tokens") is None):
            raise ValueError("model response bytes or metering changed")
        out: dict[str, Any] = {
            "source_run_id": PINNED_MODEL_RUN,
            "model_response_sha256": receipt["response_sha256"],
            "source_changed": False,
            "behavior_passed": False,
            "regression_passed": False,
            "model_calls_this_replay": 0,
        }
        try:
            candidate_source = _extract_source(raw)
        except (ValueError, TypeError) as exc:
            out["admission"] = "REJECTED_MODEL_JSON"
            out["reason"] = type(exc).__name__
            results[arm] = out
            continue
        sha = sha256(candidate_source.encode("utf-8")).hexdigest()
        out["candidate_source_sha256"] = sha
        out["source_changed"] = sha != baseline["source_sha256"]
        out["distinct_from_other_arm"] = sha not in seen
        seen.add(sha)
        home = archive_root / arm
        home.mkdir(parents=True, exist_ok=True)
        db = Database(home / "state.sqlite")
        try:
            ledger = EvidenceLedger(db, home / "evidence.key")
            gate = RsiArtifactGate(
                home / "candidates", ledger, allowed_files=frozenset({TARGET}),
            )
            policy = ExperimentPolicy(
                "maximize", 1.0, {"regressions": 0.0}, 1, 0, expected_evaluator,
            )
            gate.register(
                artifact_id="seed", parent_id=None, generation=0, branch=0,
                files={TARGET: baseline_source},
                policy_digest=policy.digest(), evaluator_digest=expected_evaluator,
            )
            gate.register(
                artifact_id="model-replay", parent_id="seed", generation=1, branch=0,
                files={TARGET: candidate_source.encode("utf-8")},
                policy_digest=policy.digest(), evaluator_digest=expected_evaluator,
            )
            pilot = RsiEvolutionPilot(db, ledger)
            pilot.start(
                run_id="pinned-model-replay", policy=policy, baseline_id="seed",
                baseline=MetricResult(0.0, {"regressions": 0.0}, expected_evaluator),
                branches=1,
            )

            def evaluated(candidate_id: str) -> MetricResult:
                gate.verify(candidate_id)
                stored = (gate.root / candidate_id / TARGET).read_text(encoding="utf-8")
                grade = _grade_source(stored, image)
                out["behavior_status"] = grade["behavior"]
                out["regression_status"] = grade["regression"]
                out["behavior_passed"] = grade["behavior"] == "EXIT_0"
                out["regression_passed"] = grade["regression"] == "EXIT_0"
                return MetricResult(
                    1.0 if grade["passes"] else 0.0,
                    {"regressions": 0.0 if out["regression_passed"] else 1.0},
                    expected_evaluator,
                )

            state = pilot.advance(
                "pinned-model-replay", policy=policy,
                propose=lambda parent, gen, branch: "model-replay",
                evaluate=evaluated,
            )
            out["admission"] = "REAL_MODEL_SOURCE_ARCHIVED"
            out["decision"] = state["champion_id"]
            out["pilot_generation"] = state["generation"]
            out["ledger_verified"] = ledger.verify()[0]
        finally:
            db.close_all()
        results[arm] = out
    return {
        "schema": REPLAY_SCHEMA,
        "status": "REPLAY_SCORED",
        "authentic_model_run_id": PINNED_MODEL_RUN,
        "authentic_model_calls": 2,
        "model_calls_this_replay": 0,
        "model_id": LARGER_MODEL_ID,
        "base_git_blob": FROZEN_BLOB_SHA,
        "evaluator_digest": expected_evaluator,
        "image": image,
        "baseline": baseline,
        "arms": results,
        "admitted_changed_candidates": sum(
            int(x.get("admission") == "REAL_MODEL_SOURCE_ARCHIVED" and x["source_changed"])
            for x in results.values()
        ),
        "independently_passing_arms": sum(
            int(x["behavior_passed"] and x["regression_passed"] and x["source_changed"])
            for x in results.values()
        ),
        "heldout_gain": "UNMEASURED",
        "rsi": "UNPROVEN",
        "source_promotion": False,
        "claim_ceiling": (
            "authentic fixed-model reply replay; independent C1 behavior on one "
            "public WLS self-code task, no new model calls or generalization"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior", required=True, type=Path)
    parser.add_argument("--archive-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        outcome = replay(
            args.prior, os.environ.get("WLS_C1_PINNED_IMAGE", ""),
            args.archive_root,
        )
    except Exception as exc:
        outcome = {
            "schema": REPLAY_SCHEMA, "status": "BLOCKED",
            "blocker_type": type(exc).__name__,
            "blocker_detail": str(exc)[:200],
            "model_calls_this_replay": 0,
            "rsi": "UNPROVEN",
        }
    args.report.write_text(
        json.dumps(outcome, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "status": outcome["status"],
        "admitted_changed_candidates": outcome.get("admitted_changed_candidates"),
        "independently_passing_arms": outcome.get("independently_passing_arms"),
        "blocker": outcome.get("blocker_detail"),
    }, sort_keys=True))
    return 0 if outcome["status"] == "REPLAY_SCORED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
