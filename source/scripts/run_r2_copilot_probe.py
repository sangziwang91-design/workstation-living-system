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
MAX_BATCHES = 4
MAX_CHOICES = 16
MAX_RESPONSE = 16_384


class ProbeBlocked(ValueError):
    pass


class ModelUnavailable(ProbeBlocked):
    """The model transport did not return any response to grade."""


def canonical(value: Any) -> bytes:
    return baseline.encoded(value)


def selected_cases(root: Path, receipt_file: Path, replay_file: Path,
                   baseline_file: Path, offset: int = 0) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipt = replay.read_receipt(receipt_file.read_bytes())
    stable = set(baseline.read_replay(replay_file.read_bytes(), receipt))
    replay.checked_source(root, receipt)
    prior = baseline.replay.read_receipt(canonical(receipt))
    if prior["task_digest"] != receipt["task_digest"]:
        raise ProbeBlocked("receipt digest changed across independent read")
    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise ProbeBlocked("duplicate key in trusted baseline receipt")
            obj[key] = value
        return obj
    scored = json.loads(baseline_file.read_bytes(), object_pairs_hook=no_duplicate_keys)
    if (not isinstance(scored, dict)
            or scored.get("schema") != baseline.SCHEMA
            or scored.get("status") != "PUBLIC_BASELINE_SCORED"
            or scored.get("source_sha") != receipt["runner_provenance"]["github_sha"]
            or scored.get("source_run_id") != receipt["runner_provenance"]["github_run_id"]
            or scored.get("per_strategy_per_task_max_pytest_calls") != 3):
        raise ProbeBlocked("not the same run frozen public baseline")
    cases = scored.get("cases")
    if (type(offset) is not int or offset < 0 or offset % MAX_TASKS != 0
            or offset + MAX_TASKS > len(cases)
            or offset >= MAX_TASKS * MAX_BATCHES):
        raise ProbeBlocked("public cohort offset outside four fixed three-task batches")
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
                or any(not isinstance(v, dict)
                       or type(v.get("tests_used")) is not int
                       or not 0 <= v["tests_used"] <= 3
                       or type(v.get("exact_original_restored")) is not bool
                       for v in strategies.values())):
            raise ProbeBlocked("changed benchmark attempts")
    return receipt, cases[offset:offset + MAX_TASKS]


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
    choice_aliases: dict[str, dict[str, str]] = {}
    for case in cases:
        module, _oracle, broken, options, selected = rebuild(root, receipt, case)
        # ALL options are reconstructed by the trusted scorer; the model sees
        # only the first bounded subset, no golden file/patch/digest or scorer.
        visible = options[:MAX_CHOICES]
        snippets = []
        broken_lines = broken.decode("utf-8").splitlines()
        aliases = choice_aliases[case["task_id"]] = {}
        for index, option in enumerate(visible):
            alias = chr(ord("A") + index)
            aliases[alias] = option["mutant_sha256"]
            ln = option["mutation_line"]
            candidate_lines = option["mutated_content"].decode("utf-8").splitlines()
            snippets.append({
                "id": alias,
                "line": ln,
                "before": broken_lines[ln - 1][:120],
                "after": candidate_lines[ln - 1][:120],
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
        "You have no tools and must not request files. Return only a JSON object "
        "with a tasks array. For each input task, include its exact id and a choices array. "
        "Copy up to THREE distinct option letters from that task's candidate_options, best first; "
        "empty choices are allowed. No explanation. Original correct source, "
        "oracle digests and hidden holdout are NOT provided. "
        "The trusted executor validates choices and runs tests under a hard budget.\n"
        + json.dumps(tasks, sort_keys=True, ensure_ascii=False)
    )
    if len(message.encode("utf-8")) > 30_000:
        raise ProbeBlocked("model prompt too large")
    spec = {
        "schema": SCHEMA, "source_sha": receipt["runner_provenance"]["github_sha"],
        "source_run_id": receipt["runner_provenance"]["github_run_id"],
        "task_ids": [c["task_id"] for c in cases],
        "visible_choice_ids": {key: list(aliases.values()) for key, aliases in choice_aliases.items()},
        "choice_aliases": choice_aliases,
        "prompt_sha256": sha256(message.encode()).hexdigest(),
        "sample_scope": "PUBLIC_DEVELOPMENT_NOT_HOLDOUT",
        "per_task_max_tests": 3,
    }
    return message, spec


def parse_choices(raw: bytes, spec: dict[str, Any]) -> dict[str, list[str]]:
    if not raw:
        raise ModelUnavailable("Copilot CLI produced no model response")
    if len(raw) > MAX_RESPONSE:
        raise ProbeBlocked("oversized actual model response")
    data = raw.decode("utf-8").strip()
    # Some CLI versions wrap JSON in a Markdown code fence. Strip only fences.
    if data.startswith("```"):
        lines = data.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            data = "\n".join(lines[1:-1])
    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProbeBlocked("duplicate JSON key in model reply")
            result[key] = value
        return result

    payload = json.loads(data, object_pairs_hook=unique_keys)
    if not isinstance(payload, dict) or set(payload) != {"tasks"} or not isinstance(payload["tasks"], list):
        raise ProbeBlocked("model did not return task-only JSON")
    chosen: dict[str, list[str]] = {}
    for task in payload["tasks"]:
        if not isinstance(task, dict) or set(task) != {"id", "choices"}:
            raise ProbeBlocked("malformed model task")
        key, choices = task["id"], task["choices"]
        if (not isinstance(key, str) or key not in spec["task_ids"] or key in chosen
                or not isinstance(choices, list) or len(choices) > 3
                or not all(isinstance(v, str) for v in choices)
                or len(choices) != len(set(choices))):
            raise ProbeBlocked("model attempted unauthorized candidate or fourth choice")
        visible = spec["visible_choice_ids"][key]
        aliases = spec.get("choice_aliases")
        mapping = aliases[key] if aliases is not None else {v: v for v in visible}
        if any(v not in mapping or mapping[v] not in visible for v in choices):
            raise ProbeBlocked("model attempted unauthorized candidate or fourth choice")
        chosen[key] = [mapping[v] for v in choices]
    if set(chosen) != set(spec["task_ids"]):
        raise ProbeBlocked("model omitted task")
    return chosen


def measured_choices(raw: bytes, spec: dict[str, Any]
                     ) -> tuple[dict[str, list[str]], bool, str | None]:
    """Grade a malformed model answer as zero repairs, not a tool failure.

    The strict parser remains the authorization boundary: no unauthorized
    candidate is ever executed. A model-format failure is still a real
    observed failure and cannot be silently excluded from comparisons.
    """
    try:
        return parse_choices(raw, spec), True, None
    except (ProbeBlocked, ValueError, UnicodeError) as exc:
        if not raw:
            raise ModelUnavailable("model returned no bytes") from exc
        return {task_id: [] for task_id in spec["task_ids"]}, False, type(exc).__name__


def score(root: Path, receipt: dict[str, Any], cases: list[dict[str, Any]],
          choices: dict[str, list[str]]) -> dict[str, Any]:
    expected_ids = {case["task_id"] for case in cases}
    if (set(choices) != expected_ids or len(expected_ids) != len(cases)
            or any(not isinstance(v, list) or len(v) > 3
                   or any(not isinstance(x, str) for x in v)
                   or len(v) != len(set(v)) for v in choices.values())):
        raise ProbeBlocked("untrusted direct scorer choice set or budget")
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
            if any(key not in mapping for key in choices[case["task_id"]]):
                raise ProbeBlocked("candidate not present in trusted AST reconstruction")
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
        "model_provider": "github_copilot_cli", "model_requested": "auto",
        "model_actual_id": "UNVERIFIED",
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



def aggregate_public_reports(
    reports: list[dict[str, Any]], source_sha: str, source_run_id: str
) -> dict[str, Any]:
    """Aggregate four disjoint, independently scored 3-task public batches.

    Trusted individual reports already bind their prompts, inference proof and
    real pytest calls. Never drop invalid model replies or duplicate cases.
    """
    if not isinstance(reports, list) or len(reports) != MAX_BATCHES:
        raise ProbeBlocked("public aggregate requires exactly four scored batches")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid = 0
    model_sha: str | None = None
    for report in reports:
        if not isinstance(report, dict):
            raise ProbeBlocked("malformed scored batch")
        if (report.get("schema") != SCHEMA
                or report.get("status") not in {
                    "PUBLIC_MODEL_RANKING_SCORED", "PUBLIC_MODEL_INVALID_OUTPUT_SCORED"
                }
                or report.get("source_sha") != source_sha
                or report.get("source_run_id") != source_run_id
                or report.get("model_provider") != "offline_qwen_1.5b"
                or report.get("model_inference_proof") != "SAME_RUN_TRUSTED_PROCESS_DIGEST"
                or type(report.get("model_inference_count")) is not int
                or report["model_inference_count"] != 1
                or report.get("model_output_valid") not in (True, False)
                or type(report.get("public_tasks_scored")) is not int
                or report["public_tasks_scored"] != MAX_TASKS
                or not isinstance(report.get("model_sha256"), str)
                or len(report["model_sha256"]) != 64):
            raise ProbeBlocked("batch source, model or budget not verified")
        if model_sha is None:
            model_sha = report["model_sha256"]
        elif model_sha != report["model_sha256"]:
            raise ProbeBlocked("model identity changed across public batches")
        cases = report.get("cases")
        if not isinstance(cases, list) or len(cases) != MAX_TASKS:
            raise ProbeBlocked("scored batch does not contain three cases")
        if report["model_output_valid"] is False:
            invalid += 1
        expected = {
            "model_exact_restorations": 0,
            "line_order_exact_restorations": 0,
            "seeded_random_exact_restorations": 0,
            "total_model_pytest_calls": 0,
        }
        for case in cases:
            if not isinstance(case, dict):
                raise ProbeBlocked("malformed batch case")
            task_id = case.get("task_id")
            n = case.get("model_tests_used")
            if (not isinstance(task_id, str) or not task_id or task_id in seen
                    or type(n) is not int or not 0 <= n <= 3
                    or not isinstance(case.get("trials"), list)
                    or len(case["trials"]) != n
                    or any(type(case.get(k)) is not bool for k in (
                        "model_exact_restoration", "line_order_exact_restoration",
                        "seeded_random_exact_restoration"))):
                raise ProbeBlocked("duplicate task or malformed independent attempts")
            if report["model_output_valid"] is False and (
                n != 0 or case["model_exact_restoration"]
            ):
                raise ProbeBlocked("invalid model reply was not scored as failure")
            seen.add(task_id)
            expected["model_exact_restorations"] += int(case["model_exact_restoration"])
            expected["line_order_exact_restorations"] += int(case["line_order_exact_restoration"])
            expected["seeded_random_exact_restorations"] += int(case["seeded_random_exact_restoration"])
            expected["total_model_pytest_calls"] += n
            rows.append(case)
        if any(type(report.get(k)) is not int or report[k] != v
               for k, v in expected.items()):
            raise ProbeBlocked("batch summaries disagree with scored cases")
    if len(rows) != MAX_TASKS * MAX_BATCHES:
        raise ProbeBlocked("public trial has fewer than twelve unique cases")
    model = sum(int(r["model_exact_restoration"]) for r in rows)
    line = sum(int(r["line_order_exact_restoration"]) for r in rows)
    random = sum(int(r["seeded_random_exact_restoration"]) for r in rows)
    return {
        "schema": "wls.r2.public_twelve_case_aggregate.v1",
        "status": "PUBLIC_TWELVE_CASES_SCORED",
        "source_sha": source_sha, "source_run_id": source_run_id,
        "model_provider": "offline_qwen_1.5b", "model_sha256": model_sha,
        "model_inference_count": MAX_BATCHES, "scored_batch_count": MAX_BATCHES,
        "public_tasks_scored": len(rows), "invalid_model_batches": invalid,
        "model_exact_restorations": model,
        "line_order_exact_restorations": line,
        "seeded_random_exact_restorations": random,
        "total_model_pytest_calls": sum(r["model_tests_used"] for r in rows),
        "model_beats_both_public_baselines": model > line and model > random,
        "heldout_gain": "UNMEASURED", "rsi": "UNPROVEN",
        "cases": rows,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("prepare", "generate-local", "score", "aggregate"), required=True)
    p.add_argument("--provider", choices=("copilot", "local"), default="copilot")
    p.add_argument("--offset", type=int, default=0, help="three-task cohort index: 0, 3, 6 or 9")
    p.add_argument("--batch-reports", nargs="*", type=Path, default=[])
    p.add_argument("--model-file", type=Path, default=Path("wls-qwen-1.5b.gguf"))
    p.add_argument("--model-proof", type=Path, default=Path("r2-local-model-proof.json"))
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
        if args.mode == "aggregate":
            if args.offset != 0 or len(args.batch_reports) != MAX_BATCHES:
                raise ProbeBlocked("aggregate requires four reports and no cohort offset")
            source = replay.read_receipt(args.receipt.read_bytes())
            replay.checked_source(args.repo.resolve(), source)
            def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                out: dict[str, Any] = {}
                for key, value in pairs:
                    if key in out:
                        raise ProbeBlocked("duplicate JSON key in scored batch")
                    out[key] = value
                return out
            reports = [json.loads(path.read_bytes(), object_pairs_hook=unique_keys)
                       for path in args.batch_reports]
            result = aggregate_public_reports(
                reports, source["runner_provenance"]["github_sha"],
                source["runner_provenance"]["github_run_id"],
            )
            args.output.write_bytes(canonical(result))
            print(json.dumps({k: v for k, v in result.items() if k != "cases"}))
            return 0
        if args.batch_reports:
            raise ProbeBlocked("batch reports are accepted only in aggregate mode")
        receipt, cases = selected_cases(
            args.repo.resolve(), args.receipt, args.replay, args.baseline, offset=args.offset,
        )
        prompt, spec = build_prompt(args.repo.resolve(), receipt, cases)
        if args.mode == "prepare":
            args.prompt.write_bytes(prompt.encode("utf-8"))
            args.spec.write_bytes(canonical(spec))
            print(json.dumps({"status": "MODEL_PROMPT_READY", "tasks": len(cases),
                              "prompt_sha256": spec["prompt_sha256"]}))
            return 0
        saved = json.loads(args.spec.read_bytes())
        if saved != spec or sha256(args.prompt.read_bytes()).hexdigest() != spec["prompt_sha256"]:
            raise ProbeBlocked("prepared task/response boundary changed")
        if args.mode == "generate-local":
            if args.provider != "local":
                raise ProbeBlocked("local generation must be expressly selected")
            from measure_task_judgment_local_model import (
                LARGER_MODEL_ID, LARGER_MODEL_URL, LARGER_MODEL_SHA256,
                LARGER_MODEL_MIN_BYTES, LARGER_MODEL_MAX_BYTES, verify_model,
                download_pinned_model, PinnedModelDownloadError,
            )
            try:
                verified = (verify_model(
                    args.model_file, expected_sha=LARGER_MODEL_SHA256,
                    min_bytes=LARGER_MODEL_MIN_BYTES,
                    max_bytes=LARGER_MODEL_MAX_BYTES,
                ) if args.model_file.is_file() else download_pinned_model(
                    args.model_file, url=LARGER_MODEL_URL,
                    expected_sha=LARGER_MODEL_SHA256,
                    min_bytes=LARGER_MODEL_MIN_BYTES,
                    max_bytes=LARGER_MODEL_MAX_BYTES,
                ))
            except PinnedModelDownloadError as exc:
                raise ModelUnavailable("pinned offline model transport failed") from exc
            from llama_cpp import Llama
            model = Llama(model_path=str(args.model_file),
                          n_ctx=12288, n_threads=2, verbose=False)
            response = model.create_chat_completion(
                messages=[
                    {"role": "system", "content": "Return only the requested task JSON, no tools."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0, max_tokens=1024,
            )
            raw = str(response["choices"][0]["message"]["content"] or "").encode("utf-8")
            if not raw or len(raw) > MAX_RESPONSE:
                raise ModelUnavailable("actual local model returned no usable bounded bytes")
            args.response.write_bytes(raw)
            proof = {
                "schema": "wls.r2.local_model_inference_proof.v1",
                "model_id": LARGER_MODEL_ID,
                "model_sha256": verified,
                "model_inference_count": 1,
                "source_sha": spec["source_sha"],
                "source_run_id": spec["source_run_id"],
                "prompt_sha256": spec["prompt_sha256"],
                "response_sha256": sha256(raw).hexdigest(),
                "promote_source": False,
            }
            args.model_proof.write_bytes(canonical(proof))
            print(json.dumps({"status": "ACTUAL_LOCAL_MODEL_CALLED",
                              "model": LARGER_MODEL_ID, "calls": 1,
                              "response_sha256": proof["response_sha256"]}))
            return 0
        raw = args.response.read_bytes()
        if args.provider == "local":
            proof = json.loads(args.model_proof.read_bytes())
            from measure_task_judgment_local_model import (
                LARGER_MODEL_ID, LARGER_MODEL_SHA256,
            )
            if (not isinstance(proof, dict)
                    or set(proof) != {
                        "schema", "model_id", "model_sha256",
                        "model_inference_count", "source_sha", "source_run_id",
                        "prompt_sha256", "response_sha256", "promote_source",
                    }
                    or proof["schema"] != "wls.r2.local_model_inference_proof.v1"
                    or proof["model_id"] != LARGER_MODEL_ID
                    or proof["model_sha256"] != LARGER_MODEL_SHA256
                    or type(proof["model_inference_count"]) is not int
                    or proof["model_inference_count"] != 1
                    or proof["source_sha"] != spec["source_sha"]
                    or proof["source_run_id"] != spec["source_run_id"]
                    or proof["prompt_sha256"] != spec["prompt_sha256"]
                    or proof["response_sha256"] != sha256(raw).hexdigest()
                    or proof["promote_source"] is not False):
                raise ProbeBlocked("local model identity or inference provenance changed")
        choices, valid_output, invalid_type = measured_choices(raw, spec)
        out = score(args.repo.resolve(), receipt, cases, choices)
        out["model_output_valid"] = valid_output
        out["invalid_output_type"] = invalid_type
        if not valid_output:
            out["status"] = "PUBLIC_MODEL_INVALID_OUTPUT_SCORED"
            out["model_beats_both_public_baselines"] = False
            out["model_invalid_reply_scored_as_zero_repairs"] = True
        if args.provider == "local":
            out.update({
                "model_provider": "offline_qwen_1.5b",
                "model_requested": LARGER_MODEL_ID,
                "model_actual_id": LARGER_MODEL_ID,
                "model_sha256": LARGER_MODEL_SHA256,
                "model_cli_invocations": 0,
                "model_inference_count": 1,
                "model_inference_proof": "SAME_RUN_TRUSTED_PROCESS_DIGEST",
            })
        out["model_output_sha256"] = sha256(args.response.read_bytes()).hexdigest()
        args.output.write_bytes(canonical(out))
        print(json.dumps({k: v for k, v in out.items() if k not in {"cases"}}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, RuntimeError) as exc:
        status = "MODEL_NOT_AVAILABLE" if (
            isinstance(exc, ModelUnavailable)
            or (args.mode == "score" and isinstance(exc, OSError)
                and not args.response.exists())
        ) else "BLOCKED"
        args.output.write_bytes(canonical({"schema": SCHEMA, "status": status,
                                           "error_type": type(exc).__name__,
                                           "heldout_gain": "UNMEASURED", "rsi": "UNPROVEN"}))
        print(json.dumps({"status": status, "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
