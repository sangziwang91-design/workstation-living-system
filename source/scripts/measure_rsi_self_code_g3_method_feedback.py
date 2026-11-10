"""Real offline Qwen G3 method-level self-code study: inherited failure vs fresh.

Both arms use the SAME pinned model, 1 inference each, same output/token
ceiling, immutable owner C1 grader and canonical WLS RSI artifact/pilot path.
The inherited parent is an *actual* failed model source from Actions, hash
verified from a frozen run. Historical metrics are not transfer proof.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from measure_rsi_self_code_model import (
    TARGET, FROZEN_BLOB_SHA, _pin_source,
)
from measure_rsi_self_code_method_edit import (
    IMAGE, GRADE_SHA, SCHEMA as METHOD_SCHEMA, run as method_run,
)
from measure_task_judgment_local_model import (
    LARGER_MODEL_ID, LARGER_MODEL_SHA256,
)

SCHEMA = "wls.rsi.real_qwen_g3_method_feedback_vs_fresh.v1"
PRIOR_RUN_ID = 42552809929
PRIOR_CANDIDATE_SHA = "e476955a9ae9fd7ba1f42245d2990722cbd460214762c16f140c8749af7e6908"
PRIOR_RAW_REPLY_SHA = "a94a13fcdba034197c02e3d2921f42976a76688a33b7fdaad79c09b0fed50c4d"

# Public summary of the measured *outcome*, not the private test implementation.
# Not a code fix or a substitute for model inference: the Qwen proposal still
# must author both method bodies, pass parser/scope checks, and pass C1 tests.
OBSERVED_FAILURE = (
    "Previous model-authored WLS method-edit candidate changed validate() "
    "to retain only truthy outcome evidence but left is_validated() as "
    "return len(self.acceptance_evidence)>0. That version failed an "
    "independent behavior check while preserving old regressions: "
    "mixed passing and failing declared checks could still be labeled "
    "validated. The next proposal must reconsider BOTH methods and "
    "the exact declared test manifest, rather than assuming the "
    "previous edit already fixed the bug."
)


def load_authentic_prior(root: Path) -> tuple[bytes, dict[str, Any]]:
    """Validate the immutable model response and source from the exact run."""
    reports = list(root.rglob("rsi-method-edit-observed.json"))
    if len(reports) != 1 or reports[0].is_symlink():
        raise ValueError("expected one authentic method-edit report")
    record = json.loads(reports[0].read_text(encoding="utf-8"))
    if (not isinstance(record, dict)
            or record.get("schema") != METHOD_SCHEMA
            or record.get("status") != "REAL_MODEL_MEASURED"
            or record.get("model_id") != LARGER_MODEL_ID
            or record.get("model_sha256") != LARGER_MODEL_SHA256
            or record.get("frozen_baseline_blob") != FROZEN_BLOB_SHA
            or record.get("evaluator_digest") != GRADE_SHA
            or record.get("image") != IMAGE
            or record.get("model_calls_observed") != 1
            or record.get("source_changed") is not True
            or record.get("independently_passing_changed_source") is not False
            or record.get("candidate_sha256") != PRIOR_CANDIDATE_SHA
            or record.get("pilot_generation") != 1
            or record.get("pilot_champion") != "seed"):
        raise ValueError("not a known failed real Qwen model candidate")
    receipts = record.get("model_receipts")
    if not isinstance(receipts, list) or len(receipts) != 1:
        raise ValueError("missing original single inference receipt")
    receipt = receipts[0]
    raw = receipt.get("raw_model_reply")
    if (not isinstance(raw, str)
            or sha256(raw.encode("utf-8")).hexdigest() != PRIOR_RAW_REPLY_SHA
            or receipt.get("response_sha256") != PRIOR_RAW_REPLY_SHA
            or len(raw.encode("utf-8")) != receipt.get("response_bytes")
            or not isinstance(receipt.get("input_tokens"), int)
            or not isinstance(receipt.get("output_tokens"), int)):
        raise ValueError("prior model raw reply does not match pinned inference")
    candidate_id = record.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.startswith("rsi-"):
        raise ValueError("unrecognized canonical candidate identity")
    relpath = (
        "candidates/" + candidate_id + "/" + TARGET
    )
    candidates = [
        path for path in root.rglob("skill_compiler.py")
        if path.as_posix().endswith(relpath)
    ]
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise ValueError("missing unique immutable model-authored source")
    source = candidates[0].read_bytes()
    if sha256(source).hexdigest() != PRIOR_CANDIDATE_SHA:
        raise ValueError("prior model candidate source changed")
    if not record.get("ledger_verified"):
        raise ValueError("historical run did not verify its in-run ledger")
    measurements = record.get("independent_measurements")
    if (not isinstance(measurements, list)
            or not any(
                isinstance(m, dict)
                and m.get("candidate_id") == candidate_id
                and m.get("source_sha256") == PRIOR_CANDIDATE_SHA
                and m.get("behavior") == "EXIT_NONZERO"
                and m.get("regression") == "EXIT_0"
                for m in measurements
            )):
        raise ValueError("previous independent failure receipt missing")
    return source, {
        "authentic_run_id": PRIOR_RUN_ID,
        "candidate_id": candidate_id,
        "parent_source_sha256": PRIOR_CANDIDATE_SHA,
        "model_raw_response_sha256": PRIOR_RAW_REPLY_SHA,
        "original_input_tokens": receipt["input_tokens"],
        "original_output_tokens": receipt["output_tokens"],
        "independent_outcome": "BEHAVIOR_FAILED_REGRESSION_PASSED",
    }


def run(
    *, previous_root: Path, model_file: Path, image: str,
    archive_root: Path,
) -> dict[str, Any]:
    if image != IMAGE:
        raise ValueError("frozen independent C1 image mismatch")
    frozen = _pin_source()
    prior_source, prior_receipt = load_authentic_prior(previous_root)
    if prior_source == frozen:
        raise ValueError("historical candidate did not actually change WLS source")
    if archive_root.exists() and any(archive_root.iterdir()):
        raise ValueError("must start from a clean archive root")
    archive_root.mkdir(parents=True, exist_ok=True)
    arms: dict[str, dict[str, Any]] = {}
    for arm, parent, feedback in (
        ("fresh_original", frozen, ""),
        ("inherited_failed_patch", prior_source, OBSERVED_FAILURE),
    ):
        result = method_run(
            model_file, image, archive_root / arm,
            parent_source=parent, prior_failure_summary=feedback,
        )
        # Every real model call, including rejected candidate, is reported.
        if (result.get("status") != "REAL_MODEL_MEASURED"
                or result.get("model_calls_observed") != 1
                or result.get("model_sha256") != LARGER_MODEL_SHA256
                or result.get("evaluator_digest") != GRADE_SHA
                or result.get("parent_source_sha256")
                   != sha256(parent).hexdigest()):
            raise ValueError("model measurement incomplete or frozen identity drift")
        arms[arm] = result
    source_shas = {
        result["candidate_sha256"] for result in arms.values()
        if result.get("candidate_sha256") is not None
    }
    changed = {
        result["candidate_sha256"] for result in arms.values()
        if result.get("source_changed") is True
    }
    passing = {
        arm: bool(row["independently_passing_changed_source"])
        for arm, row in arms.items()
    }
    return {
        "schema": SCHEMA,
        "status": "TWO_REAL_MODEL_ARMS_MEASURED",
        "source_main_lineage": "canonical WLS RsiModelExperiment/RsiEvolutionPilot",
        "authentic_prior": prior_receipt,
        "model_id": LARGER_MODEL_ID,
        "model_sha256": LARGER_MODEL_SHA256,
        "model_calls_observed": 2,
        "call_budget_per_arm": 1,
        "max_output_tokens_per_arm": 2048,
        "evaluators_independent_of_model": True,
        "evaluator_digest": GRADE_SHA,
        "image": image,
        "arms": arms,
        "actual_distinct_output_source_hashes": len(source_shas),
        "actually_changed_source_hashes": len(changed),
        "independently_passing_arms": sum(passing.values()),
        "matched_comparison": {
            "fresh_original": passing["fresh_original"],
            "inherited_failed_patch": passing["inherited_failed_patch"],
            "net_inherited_minus_fresh": (
                int(passing["inherited_failed_patch"])
                - int(passing["fresh_original"])
            ),
        },
        "model_weight_updates": 0,
        "heldout_transfer": "UNMEASURED",
        "rsi": "UNPROVEN",
        "source_promotion": False,
        "claim_ceiling": (
            "one public WLS self-code defect, two genuine fixed-model calls, "
            "changed-parent plus feedback vs frozen-fresh; not a randomized "
            "causal ablation of feedback alone, nor independent transfer"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-root", type=Path, required=True)
    parser.add_argument("--model-file", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        outcome = run(
            previous_root=args.previous_root,
            model_file=args.model_file,
            image=os.environ.get("WLS_C1_PINNED_IMAGE", ""),
            archive_root=args.archive_root,
        )
    except Exception as exc:
        outcome = {
            "schema": SCHEMA, "status": "BLOCKED",
            "blocker_type": type(exc).__name__,
            "blocker_detail": str(exc)[:230],
            "model_calls_observed": "UNKNOWN",
            "rsi": "UNPROVEN",
        }
    args.report.write_text(
        json.dumps(outcome, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": outcome["status"],
        "model_calls_observed": outcome["model_calls_observed"],
        "independently_passing_arms": outcome.get("independently_passing_arms"),
        "blocker": outcome.get("blocker_detail"),
    }, sort_keys=True))
    return 0 if outcome["status"] == "TWO_REAL_MODEL_ARMS_MEASURED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
