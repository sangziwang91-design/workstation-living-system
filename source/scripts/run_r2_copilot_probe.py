"""Public R2 Copilot ranking probe. No model executes code or sees the oracle.

Two phases run as separate GitHub Actions steps:
prepare -> one model call with only public broken-source/candidate information;
score -> reconstruct trusted tasks and execute <=3 real pytest calls per task.
Only a development measurement. Never promote source or claim RSI/holdout.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

import make_mutation_tasks as engine
import score_r2_three_try_baselines as baseline
import verify_r1_mutation_replay as replay

SCHEMA = "wls.r2.copilot_public_probe.v1"
MAX_TASKS = 3
MAX_CHOICES = 40
MAX_RESPONSE = 16_384


class ProbeBlocked(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return baseline.encoded(value)


def selected_cases(root: Path, receipt_file: Path, replay_file: Path,
                   baseline_file: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipt = replay.read_receipt(receipt_file.read_bytes())
    stable = set(baseline.read_replay(replay_file.read_bytes(), receipt))
    replay.checked_source(root, receipt)
    prior = baseline.replay.read_receipt(canonical(receipt))
    assert prior["task_digest"] == receipt["task_digest"]
    scored = json.loads(baseline_file.read_bytes())
    if (not isinstance(scored, dict)
            or scored.get("schema") != baseline.SCHEMA
            or scored.get("status") != "PUBLIC_BASELINE_SCORED"
            or scored.get("source_sha") != receipt["runner_provenance"]["github_sha"]
            or scored.get("source_run_id") != receipt["runner_provenance"]["github_run_id"]
            or scored.get("per_strategy_per_task_max_pytest_calls") != 3):
        raise ProbeBlocked("not the same run frozen public baseline")
    cases = scored.get("cases")
    if not isinstance(cases, list) or len(cases) < MAX_TASKS:
        raise ProbeBlocked("insufficient scored public cases")
    seen: set[str] = set()
    for case in cases:
        task_id = case.get("task_id") if isinstance(case, dict) else None
        if not isinstance(task_id, str) or task_id not in stable or task_id in seen:
            raise ProbeBlocked("missing/duplicate/unstable task in public baseline")
        seen.add(task_id)
        strategies = case.get("strategies")
        if (not isinstance(strategies, dict)
                or set(strategies) != {"line_order", "seeded_random"}
                or any(type(v.get("tests_used")) is not int
                       or not 0 <= v["tests_used"] <= 3
                       for v in strategies.values())):
            raise ProbeBlocked("changed benchmark attempts")
    return receipt, cases[:MAX_TASKS]


def rebuild(root: Path, receipt: dict[str, Any], case: dict[str, Any]
            ) -> tuple[str, bytes, bytes, list[dict[str, Any]], str]:
    lookup = {row["id"]: row for row in receipt["tasks"]}
    row = lookup[case["task_id"]]
    rel = row["source_path"]
    module = Path(rel).name
    if (rel != "source/src/wls/" + module
            or module not in engine.BASELINE_TESTS
            or row["selected_test"] != engine.BASELINE_TESTS[module]
            or case["mutant_sha256"] != row["mutant_sha256"]):
        raise ProbeBlocked("task scope/identity was changed")
    original = (root / rel).read_bytes()
    if engine.sha(original) != row["source_sha256"]:
        raise ProbeBlocked("original source moved")
    found = [v for v in engine.candidates(module, original)
             if v["id"] == row["id"] and v["mutant_sha256"] == row["mutant_sha256"]]
    if len(found) != 1:
        raise ProbeBlocked("mutation reconstruction failed")
    broken = found[0]["mutated_content"]
    options = baseline.candidate_pool(module, broken)
    if not options:
        raise ProbeBlocked("no AST choices")
    return module, original, broken, options, row["selected_test"]


def build_prompt(root: Path, receipt: dict[str, Any],
                 cases: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    tasks = []
    for case in cases:
        module, _oracle, broken, options, selected = rebuild(root, receipt, case)
        # ALL options are reconstructed by the trusted scorer; the model sees
        # only the first bounded subset, no golden file/patch/digest or scorer.
        visible = options[:MAX_CHOICES]
        snippets = []
        broken_lines = broken.decode("utf-8").splitlines()
        for option in visible:
            ln = option["mutation_line"]
            candidate_lines = option["mutated_content"].decode("utf-8").splitlines()
            snippets.append({
                "id": option["mutant_sha256"],
                "line": ln,
                "before": broken_lines[ln - 1][:160],
                "after": candidate_lines[ln - 1][:160],
            })
        tests = (root / selected).read_text(encoding="utf-8")[:2000]
        tasks.append({
            "id": case["task_id"], "module": module,
            "broken_source_excerpt": broken.decode("utf-8")[:3000],
            "public_test_excerpt": tests,
            "candidate_options": snippets,
        })
    message = (
        "You are choosing candidate AST edits for PUBLIC development repair tasks. "
        "You have no tools and must not request files. Return ONLY valid JSON: "
        "{\"tasks\":[{\"id\":\"task id\",\"choices\":[\"sha256\",\"sha256\",\"sha256\"]}]}. "
        "Choose up to THREE distinct candidate IDs per task from its listed options; "
        "empty choices are allowed. No explanation. Original correct source, "
        "oracle digests and hidden holdout are NOT provided. "
        "The trusted executor later validates IDs and runs tests under a hard budget.\\n"
        + json.dumps(tasks, sort_keys=True, ensure_ascii=False)
    )
    if len(message) > 30_000:
        raise ProbeBlocked("model prompt too large")
    spec = {
        "schema": SCHEMA, "source_sha": receipt["runner_provenance"]["github_sha"],
        "source_run_id": receipt["runner_provenance"]["github_run_id"],
        "task_ids": [c["task_id"] for c in cases],
        "visible_choice_ids": {
            t["id"]: [v["id"] for v in t["candidate_options"]] for t in tasks
        },
        "prompt_sha256": sha256(message.encode()).hexdigest(),
        "sample_scope": "PUBLIC_DEVELOPMENT_NOT_HOLDOUT",
        "per_task_max_tests": 3,
    }
    return message, spec


def parse_choices(raw: bytes, spec: dict[str, Any]) -> dict[str, list[str]]:
    if not raw or len(raw) > MAX_RESPONSE:
        raise ProbeBlocked("missing or oversized actual model response")
    data = raw.decode("utf-8").strip()
    # Some CLI versions wrap JSON in a Markdown code fence. Strip only fences.
    if data.startswith("```"):
        lines = data.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            data = "\\n".join(lines[1:-1])
    payload = json.loads(data, object_pairs_hook=baseline.read_replay.__globals__.get(
        "unique", lambda pairs: dict(pairs)
    ))
    if not isinstance(payload, dict) or set(payload) != {"tasks"} or not isinstance(payload["tasks"], list):
        raise ProbeBlocked("model did not return task-only JSON")
    chosen: dict[str, list[str]] = {}
    for task in payload["tasks"]:
        if not isinstance(task, dict) or set(task) != {"id", "choices"}:
            raise ProbeBlocked("malformed model task")
        key, choices = task["id"], task["choices"]
        if (not isinstance(key, str) or key not in spec["task_ids"] or key in chosen
                or not isinstance(choices, list) or len(choices) > 3
                or len(choices) != len(set(choices))
                or not all(isinstance(v, str) and v in spec["visible_choice_ids"][key]
                           for v in choices)):
            raise ProbeBlocked("model attempted unauthorized candidate or fourth choice")
        chosen[key] = choices
    if set(chosen) != set(spec["task_ids"]):
        raise ProbeBlocked("model omitted task")
    return chosen


def score(root: Path, receipt: dict[str, Any], cases: list[dict[str, Any]],
          choices: dict[str, list[str]]) -> dict[str, Any]:
    results = []
    with tempfile.TemporaryDirectory(prefix="wls-r2-copilot-trusted-") as folder:
        temp = Path(folder)
        isolate = temp / "package/wls"
        isolate.parent.mkdir(parents=True)
        shutil.copytree(root / "source/src/wls", isolate, symlinks=False,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        for case in cases:
            module, original, broken, options, selected = rebuild(root, receipt, case)
            live = isolate / module
            if live.read_bytes() != original:
                raise ProbeBlocked("scoring isolation polluted across tasks")
            mapping = {o["mutant_sha256"]: o["mutated_content"] for o in options}
            trials = []
            win = None
            live.write_bytes(broken)
            try:
                for candidate_id in choices[case["task_id"]]:
                    candidate = mapping[candidate_id]
                    try:
                        live.write_bytes(candidate)
                        code, reason = engine.pytest_once(
                            root, temp, isolate.parent, selected, 35
                        )
                    finally:
                        live.write_bytes(broken)
                    trials.append({"candidate_digest": candidate_id, "exit_code": code, "status": reason})
                    if code == 0 and reason == "PASS":
                        win = candidate_id
                        break
            finally:
                live.write_bytes(original)
            control = case["strategies"]
            results.append({
                "task_id": case["task_id"], "model_tests_used": len(trials),
                "model_selected_test_pass": win is not None,
                "model_exact_restoration": win == engine.sha(original),
                "line_order_exact_restoration": control["line_order"]["exact_original_restored"],
                "seeded_random_exact_restoration": control["seeded_random"]["exact_original_restored"],
                "trials": trials,
            })
    model_correct = sum(bool(r["model_exact_restoration"]) for r in results)
    linear_correct = sum(bool(r["line_order_exact_restoration"]) for r in results)
    random_correct = sum(bool(r["seeded_random_exact_restoration"]) for r in results)
    return {
        "schema": SCHEMA, "status": "PUBLIC_MODEL_RANKING_SCORED",
        "model_provider": "github_copilot_cli", "model_requested": "gpt-5-mini",
        "model_cli_invocations": 1, "model_inference_count": "NOT_INDEPENDENTLY_ATTESTED",
        "source_sha": receipt["runner_provenance"]["github_sha"],
        "source_run_id": receipt["runner_provenance"]["github_run_id"],
        "public_tasks_scored": len(results),
        "model_exact_restorations": model_correct,
        "line_order_exact_restorations": linear_correct,
        "seeded_random_exact_restorations": random_correct,
        "total_model_pytest_calls": sum(r["model_tests_used"] for r in results),
        "model_beats_both_public_baselines": (
            model_correct > linear_correct and model_correct > random_correct
        ),
        "heldout_gain": "UNMEASURED", "rsi": "UNPROVEN",
        "cases": results,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("prepare", "score"), required=True)
    p.add_argument("--repo", type=Path, default=Path("."))
    p.add_argument("--receipt", type=Path, default=Path("r1-real-mutation-receipt.json"))
    p.add_argument("--replay", type=Path, default=Path("r1-real-mutation-replay.json"))
    p.add_argument("--baseline", type=Path, default=Path("r2-public-three-try-baselines.json"))
    p.add_argument("--prompt", type=Path, default=Path("r2-copilot-prompt.txt"))
    p.add_argument("--spec", type=Path, default=Path("r2-copilot-taskpack.json"))
    p.add_argument("--response", type=Path, default=Path("r2-copilot-response.txt"))
    p.add_argument("--output", type=Path, default=Path("r2-copilot-report.json"))
    args = p.parse_args()
    try:
        receipt, cases = selected_cases(args.repo.resolve(), args.receipt, args.replay, args.baseline)
        prompt, spec = build_prompt(args.repo.resolve(), receipt, cases)
        if args.mode == "prepare":
            args.prompt.write_text(prompt, encoding="utf-8")
            args.spec.write_bytes(canonical(spec))
            print(json.dumps({"status": "MODEL_PROMPT_READY", "tasks": len(cases),
                              "prompt_sha256": spec["prompt_sha256"]}))
            return 0
        saved = json.loads(args.spec.read_bytes())
        if saved != spec or sha256(args.prompt.read_bytes()).hexdigest() != spec["prompt_sha256"]:
            raise ProbeBlocked("prepared task/response boundary changed")
        choices = parse_choices(args.response.read_bytes(), spec)
        out = score(args.repo.resolve(), receipt, cases, choices)
        out["model_output_sha256"] = sha256(args.response.read_bytes()).hexdigest()
        args.output.write_bytes(canonical(out))
        print(json.dumps({k: v for k, v in out.items() if k not in {"cases"}}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        status = "MODEL_NOT_AVAILABLE" if args.mode == "score" and (
            isinstance(exc, OSError) and not args.response.exists()
        ) else "BLOCKED"
        args.output.write_bytes(canonical({"schema": SCHEMA, "status": status,
                                           "error_type": type(exc).__name__,
                                           "heldout_gain": "UNMEASURED", "rsi": "UNPROVEN"}))
        print(json.dumps({"status": status, "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
