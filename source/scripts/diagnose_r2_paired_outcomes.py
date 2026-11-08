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



def diagnose_pool_baseline(aggregate: dict[str, Any],
                           public_baseline: dict[str, Any]) -> dict[str, Any]:
    """Cross-check *recorded* public candidate availability, not a hidden oracle.

    This deliberately does not run a scorer or claim the answer can be selected
    in three tests. Original-source presence was already measured by the trusted
    R2 public baseline, whose unmodified task receipts are supplied here.
    """
    if (not isinstance(public_baseline, dict)
            or public_baseline.get("schema") != "wls.r2.public_three_try_baselines.v1"
            or public_baseline.get("status") != "PUBLIC_BASELINE_SCORED"
            or public_baseline.get("source_sha") != aggregate.get("source_sha")
            or public_baseline.get("source_run_id") != aggregate.get("source_run_id")
            or type(public_baseline.get("sample_size")) is not int
            or public_baseline["sample_size"] != 12
            or type(public_baseline.get("fixed_seed")) is not int
            or public_baseline["fixed_seed"] != 20261019
            or type(public_baseline.get("shared_candidate_pool_limit")) is not int
            or public_baseline["shared_candidate_pool_limit"] != 16
            or type(public_baseline.get("per_strategy_per_task_max_pytest_calls")) is not int
            or public_baseline["per_strategy_per_task_max_pytest_calls"] != 3):
        raise ValueError("untrusted, moved or incompatible public baseline")
    cases = public_baseline.get("cases")
    model_cases = aggregate["cases"]
    if not isinstance(cases, list) or len(cases) != 12:
        raise ValueError("public baseline does not contain twelve tasks")
    if [c.get("task_id") if isinstance(c, dict) else None for c in cases] != [
            c["task_id"] for c in model_cases]:
        raise ValueError("public model and baseline task identities/order differ")
    records = []
    for row, model in zip(cases, model_cases):
        if (type(row.get("original_in_shared_pool")) is not bool
                or not isinstance(row.get("mutant_sha256"), str)
                or len(row["mutant_sha256"]) != 64):
            raise ValueError("missing or malformed pool oracle presence")
        strategies = row.get("strategies")
        if not isinstance(strategies, dict) or set(strategies) != {
                "line_order", "seeded_random"}:
            raise ValueError("moved or incomplete baseline strategy set")
        for name in ("line_order", "seeded_random"):
            control = strategies[name]
            if not isinstance(control, dict):
                raise ValueError("malformed public baseline strategy")
            n = control.get("tests_used")
            if (type(n) is not int or not 0 <= n <= 3
                    or not isinstance(control.get("trials"), list)
                    or len(control["trials"]) != n
                    or type(control.get("exact_original_restored")) is not bool
                    or control["exact_original_restored"] is not model[
                        name + "_exact_restoration"]):
                raise ValueError("control differs from the same-run model report")
        records.append({
            "task_id": row["task_id"],
            "mutant_sha256": row["mutant_sha256"],
            "oracle_in_pool": row["original_in_shared_pool"],
            "model_restored": model["model_exact_restoration"],
        })
    available = sum(row["oracle_in_pool"] for row in records)
    if (type(public_baseline.get("tasks_with_original_in_shared_pool")) is not int
            or public_baseline["tasks_with_original_in_shared_pool"] != available):
        raise ValueError("reported public pool coverage contradicts cases")
    results = public_baseline.get("results")
    if not isinstance(results, dict):
        raise ValueError("missing independent fixed-strategy result summaries")
    for name in ("line_order", "seeded_random"):
        result = results.get(name)
        if (not isinstance(result, dict)
                or type(result.get("exact_restorations")) is not int
                or result["exact_restorations"] != aggregate[
                    name + "_exact_restorations"]):
            raise ValueError("baseline result summary contradicts scored tasks")
    from hashlib import sha256
    frozen = json.dumps(
        {"source_sha": aggregate["source_sha"],
         "source_run_id": aggregate["source_run_id"],
         "seed": 20261019, "shared_candidate_pool_limit": 16,
         "mutations": [{"task_id": r["task_id"],
                        "mutant_sha256": r["mutant_sha256"]} for r in records]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return {
        "recorded_oracle_present": available,
        "public_tasks": 12,
        "model_failures_despite_available_oracle": sum(
            r["oracle_in_pool"] and not r["model_restored"] for r in records),
        "task_manifest_sha256": sha256(frozen).hexdigest(),
        "source": "same-run trusted public baseline reported oracle presence",
        "not_measured": [
            "correct candidate within three attempts",
            "independent sealed holdout",
            "actual non-synthetic repair solvability",
        ],
    }


def diagnose(data: dict[str, Any], public_baseline: dict[str, Any] | None = None) -> dict[str, Any]:
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
    pool = diagnose_pool_baseline(data, public_baseline) if public_baseline is not None else "UNMEASURED"
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
        "candidate_pool_solvability": pool,
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
    data["source_sha"] = "a" * 40
    data["source_run_id"] = "42456184114"
    controls = {
        "line_order": {"exact_restorations": 6},
        "seeded_random": {"exact_restorations": 3},
    }
    baseline = {
        "schema": "wls.r2.public_three_try_baselines.v1",
        "status": "PUBLIC_BASELINE_SCORED",
        "source_sha": data["source_sha"],
        "source_run_id": data["source_run_id"],
        "sample_size": 12,
        "fixed_seed": 20261019,
        "shared_candidate_pool_limit": 16,
        "per_strategy_per_task_max_pytest_calls": 3,
        "tasks_with_original_in_shared_pool": 12,
        "results": controls,
        "cases": [
            {"task_id": c["task_id"],
             "mutant_sha256": ("%064x" % (i + 1)),
             "original_in_shared_pool": True,
             "strategies": {
                 key: {"tests_used": 1, "trials": [{}],
                       "exact_original_restored": c[key + "_exact_restoration"]}
                 for key in ("line_order", "seeded_random")
             }} for i, c in enumerate(cases)
        ],
    }
    observed = diagnose(data, baseline)["candidate_pool_solvability"]
    assert observed["recorded_oracle_present"] == 12
    assert observed["model_failures_despite_available_oracle"] == 6
    assert len(observed["task_manifest_sha256"]) == 64
    for change in ("wrong_run", "wrong_task", "false_flag", "moved_strategy",
                   "wrong_budget", "moved_sum", "duplicate_hash"):
        changed = json.loads(json.dumps(baseline))
        if change == "wrong_run":
            changed["source_run_id"] = "other"
        elif change == "wrong_task":
            changed["cases"][0]["task_id"] = "unseen"
        elif change == "false_flag":
            changed["cases"][0]["original_in_shared_pool"] = False
        elif change == "moved_strategy":
            changed["cases"][0]["strategies"]["line_order"]["exact_original_restored"] = False
        elif change == "wrong_budget":
            changed["cases"][0]["strategies"]["line_order"]["tests_used"] = 4
        elif change == "moved_sum":
            changed["results"]["line_order"]["exact_restorations"] = 7
        else:
            changed["cases"][0]["mutant_sha256"] = 7
        try:
            diagnose(data, changed)
        except ValueError:
            pass
        else:
            raise AssertionError("baseline diagnostic accepted " + change)
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
    parser.add_argument("--baseline", type=Path, help="Matching same-run public R2 baseline JSON")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("SELF_TEST_PASS")
        return
    if args.aggregate is None:
        parser.error("--aggregate is required unless --self-test")
    data = json.loads(args.aggregate.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    result = diagnose(data, baseline)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
