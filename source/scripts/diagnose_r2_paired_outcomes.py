"""Read-only paired diagnostic for an existing R2 twelve-case public aggregate.

This is not a new scorer, an oracle, a holdout, or evidence of RSI.
No pytest, model inference, workflow trigger or repository mutation occurs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA = "wls.r2.public_twelve_case_aggregate.v1"
FIELDS = ("model_exact_restoration", "line_order_exact_restoration",
          "seeded_random_exact_restoration")


def diagnose(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("status") != "PUBLIC_TWELVE_CASES_SCORED":
        raise ValueError("not a completed R2 public aggregate")
    cases = data.get("cases")
    if not isinstance(cases, list) or len(cases) != 12:
        raise ValueError("require all twelve cases, including failures")
    if data.get("public_tasks_scored") != 12 or data.get("scored_batch_count") != 4:
        raise ValueError("aggregate task or batch count mismatch")
    if data.get("heldout_gain") != "UNMEASURED" or data.get("rsi") != "UNPROVEN":
        raise ValueError("unsupported evidence claim")
    if data.get("model_inference_count") != 4 or data.get("model_provider") != "offline_qwen_1.5b":
        raise ValueError("missing four offline model batches")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("task_id"), str) or not case["task_id"] or case["task_id"] in ids:
            raise ValueError("missing or repeated task identity")
        ids.add(case["task_id"])
        if any(type(case.get(k)) is not bool for k in FIELDS):
            raise ValueError("non-boolean restoration outcome")
        n = case.get("model_tests_used")
        if type(n) is not int or not 0 <= n <= 3 or not isinstance(case.get("trials"), list) or len(case["trials"]) != n:
            raise ValueError("model pytest budget or trial trace mismatch")
    totals = [sum(int(c[k]) for c in cases) for k in FIELDS]
    if totals != [data.get("model_exact_restorations"),
                  data.get("line_order_exact_restorations"),
                  data.get("seeded_random_exact_restorations")]:
        raise ValueError("aggregate totals differ from raw cases")
    if sum(c["model_tests_used"] for c in cases) != data.get("total_model_pytest_calls"):
        raise ValueError("pytest total mismatch")
    paired = {}
    for field, label in ((FIELDS[1], "line_order"), (FIELDS[2], "seeded_random")):
        wins = sum(c[FIELDS[0]] and not c[field] for c in cases)
        losses = sum(c[field] and not c[FIELDS[0]] for c in cases)
        ties_success = sum(c[field] and c[FIELDS[0]] for c in cases)
        ties_failure = 12 - wins - losses - ties_success
        paired[label] = {"model_only": wins, "baseline_only": losses,
                         "both_success": ties_success, "both_failure": ties_failure,
                         "net_tasks": wins - losses, "discordant_tasks": wins + losses}
    return {
        "schema": "wls.r2.paired_diagnostic.v1",
        "source_sha": data.get("source_sha"),
        "source_run_id": data.get("source_run_id"),
        "public_tasks": 12,
        "exact_restorations": dict(zip(("model", "line_order", "seeded_random"), totals)),
        "paired": paired,
        "model_pytest_calls": data["total_model_pytest_calls"],
        "candidate_pool_solvability": "UNMEASURED",
        "heldout_gain": "UNMEASURED",
        "rsi": "UNPROVEN",
        "claim_ceiling": "paired descriptive comparison on public tasks only; no independent holdout or causal learning evidence",
    }


def self_test() -> None:
    cases = [{"task_id": str(i), "model_exact_restoration": i < 6,
              "line_order_exact_restoration": i in (0, 1, 2, 6, 7, 8),
              "seeded_random_exact_restoration": i in (0, 6, 9),
              "model_tests_used": 1, "trials": [{}]} for i in range(12)]
    data = {"schema": SCHEMA, "status": "PUBLIC_TWELVE_CASES_SCORED",
            "public_tasks_scored": 12, "scored_batch_count": 4,
            "heldout_gain": "UNMEASURED", "rsi": "UNPROVEN",
            "model_provider": "offline_qwen_1.5b", "model_inference_count": 4,
            "cases": cases, "model_exact_restorations": 6,
            "line_order_exact_restorations": 6,
            "seeded_random_exact_restorations": 3, "total_model_pytest_calls": 12}
    out = diagnose(data)
    assert out["paired"]["line_order"] == {
        "model_only": 3, "baseline_only": 3, "both_success": 3,
        "both_failure": 3, "net_tasks": 0, "discordant_tasks": 6}
    assert out["paired"]["seeded_random"]["net_tasks"] == 3
    for mutation in ("duplicate", "budget", "missing", "false_total"):
        broken = json.loads(json.dumps(data))
        if mutation == "duplicate":
            broken["cases"][1]["task_id"] = "0"
        elif mutation == "budget":
            broken["cases"][0]["model_tests_used"] = 4
        elif mutation == "missing":
            broken["cases"].pop()
        else:
            broken["model_exact_restorations"] = 7
        try:
            diagnose(broken)
        except ValueError:
            pass
        else:
            raise AssertionError("failed closed on " + mutation)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("SELF_TEST_PASS")
        return
    if args.aggregate is None:
        parser.error("--aggregate is required unless --self-test")
    data = json.loads(args.aggregate.read_text(encoding="utf-8"))
    result = diagnose(data)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
