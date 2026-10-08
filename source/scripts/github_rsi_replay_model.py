"""Regrade an archived *actual* model answer after a trusted parser repair.

Failed model responses are durable GitHub Actions evidence. Reuse those bytes
without redownloading the LLM, but never promote without independent fresh
WLS owner-oracle and complete source tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import runpy
import subprocess  # nosec B404 -- static independent verifier commands
import sys
from pathlib import Path

SOURCE = Path("source/src/wls/coding_adapter.py")
ARCHIVED = Path("recovered-model-evidence/rsi-model-report.json")
EXPECTED_RUN_SHA = "72917ef2fd0762c895ef79debf2adc56feceb65b"
EXPECTED_MODEL_SHA = "0128e77564e43d40682f82d7ebe8a9abdf0c24c8f55fa85629f8cc156b1b6560"
MODEL_ID = "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M"
OWNER_GOAL = "safe_relative_control_characters_v2"
NEEDLE = "            not relative_file\n"


def checked_command(*argv: str, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 -- exact fixed binary argv
        list(argv), capture_output=True, text=True, timeout=timeout, check=False,
    )


def output_bool(value: bool) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with Path(path).open("a", encoding="utf-8") as writer:
            writer.write(f"accepted={'true' if value else 'false'}\n")


def run() -> dict[str, object]:
    if not ARCHIVED.is_file() or ARCHIVED.is_symlink() or ARCHIVED.stat().st_size > 16000:
        raise ValueError("missing or oversized original model evidence")
    previous = json.loads(ARCHIVED.read_text(encoding="utf-8"))
    if (
        not isinstance(previous, dict)
        or previous.get("schema") != "wls.hosted_small_llm_coding.v1"
        or previous.get("base_sha") != EXPECTED_RUN_SHA
        or previous.get("model_sha256") != EXPECTED_MODEL_SHA
        or previous.get("model_id") != MODEL_ID
        or previous.get("goal_id") != OWNER_GOAL
        or previous.get("accepted") is not False
        or not isinstance(previous.get("generations"), list)
        or not 1 <= len(previous["generations"]) <= 3
    ):
        raise ValueError("archived model record identity mismatch")

    script = runpy.run_path("source/scripts/github_rsi_local_model.py")
    parser = script["bounded_expression"]
    oracle_text = script["ORACLE_SOURCE"]
    baseline_source = SOURCE.read_text(encoding="utf-8")
    if SOURCE.is_symlink() or baseline_source.count(NEEDLE) != 1:
        raise ValueError("bounded WLS code insertion point changed")
    base_sha = checked_command("git", "rev-parse", "HEAD").stdout.strip()
    oracle = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "test_rsi_replayed_owner_oracle.py"
    oracle.write_text(oracle_text, encoding="utf-8")
    baseline = checked_command(sys.executable, "-m", "pytest", "-q", str(oracle), timeout=90)
    if baseline.returncode == 0:
        result = {"status": "NO_GAIN", "accepted": False, "model_called": False}
        output_bool(False)
        print(json.dumps(result), flush=True)
        return result

    measured: list[dict[str, object]] = []
    for source_generation in previous["generations"]:
        raw = source_generation.get("model_reply")
        if not isinstance(raw, str) or len(raw) > 1200:
            continue
        expression = parser(raw)
        entry: dict[str, object] = {
            "generation": int(source_generation["generation"]),
            "model_reply": raw[:400],
            "proposed_expression": expression,
            "accepted": False,
        }
        measured.append(entry)
        if expression is None:
            continue
        SOURCE.write_text(
            baseline_source.replace(NEEDLE, NEEDLE + f"            or ({expression})\n"),
            encoding="utf-8",
        )
        grade = checked_command(sys.executable, "-m", "pytest", "-q", str(oracle), timeout=90)
        entry["oracle_exit"] = grade.returncode
        if grade.returncode == 0:
            suite = checked_command(
                sys.executable, "-m", "pytest", "-q", "source/tests",
                "--maxfail=1", timeout=1200,
            )
            entry["full_suite_exit"] = suite.returncode
            entry["test_output_tail"] = (suite.stdout + suite.stderr)[-800:]
            if suite.returncode == 0:
                entry["accepted"] = True
                break
        SOURCE.write_text(baseline_source, encoding="utf-8")

    accepted = any(item["accepted"] is True for item in measured)
    result = {
        "schema": "wls.hosted_small_llm_coding.v1",
        "base_sha": base_sha,
        "model_id": MODEL_ID,
        "model_sha256": EXPECTED_MODEL_SHA,
        "goal_id": OWNER_GOAL,
        "original_hidden_test_exit": baseline.returncode,
        "generations": measured,
        "accepted": accepted,
        "model_called": False,
        "replayed_from_run": 37758577790,
        "source": str(SOURCE),
        "claims": "previous true model output, regraded after parser fix, no inference",
    }
    if accepted:
        diff = checked_command("git", "diff", "--binary", "--", str(SOURCE))
        if diff.returncode or not diff.stdout.strip():
            raise RuntimeError("replayed passing source has no candidate diff")
        patch = diff.stdout.encode("utf-8")
        Path("rsi-model-candidate.patch").write_bytes(patch)
        result["patch_sha256"] = hashlib.sha256(patch).hexdigest()
    else:
        SOURCE.write_text(baseline_source, encoding="utf-8")
    Path("rsi-model-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output_bool(accepted)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return result


if __name__ == "__main__":
    result = run()
    raise SystemExit(0 if result.get("accepted") or result.get("status") == "NO_GAIN" else 1)
