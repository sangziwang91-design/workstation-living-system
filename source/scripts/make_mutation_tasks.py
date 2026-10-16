"""R1: real public single-line mutation discovery on a disposable Windows runner.

This is *not* a worker, model evaluation, hidden holdout, or evidence of
improvement. A mutant is admitted only after canonical pytest catches it
three times while the original module's selected tests pass.

All mutated WLS code lives in a disposable, isolated package copy; the
repository checkout, selected tests and grading files remain unchanged.
"""
from __future__ import annotations

import argparse
import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404: fixed Python/pytest argv
import sys
import tempfile
from typing import Any

SCHEMA = "wls.r1.observed_public_mutations.v1"
MAX_CAPTURE = 32_768
MAX_CASES = 500
MAX_PROBES = 2_000
MAX_PER_TEST_SECONDS = 40
BASELINE_TESTS = {
    "coding_adapter.py": "source/tests/test_coding_adapter_cross_platform.py",
    "task_admission.py": "source/tests/test_rsi_risk_cli.py",
    "policy.py": "source/tests/test_security.py",
    "config.py": "source/tests/test_cli_health.py",
    "experiment_decision.py": "source/tests/test_experiment_decision.py",
    "rsi_model_port.py": "source/tests/test_rsi_model_port.py",
    "db.py": "source/tests/test_db_schema.py",
    "skills.py": "source/tests/test_learning_episode.py",
    "tools.py": "source/tests/test_security.py",
    "owner_context.py": "source/tests/test_owner_context.py",
    "autonomy.py": "source/tests/test_autonomous_growth.py",
    "learning.py": "source/tests/test_learning_episode.py",
    "planner.py": "source/tests/test_integration.py",
    "rsi_evolution.py": "source/tests/test_rsi_evolution.py",
    "rsi_artifact_gate.py": "source/tests/test_rsi_artifact_gate.py",
    "memory_ablation.py": "source/tests/test_memory_ablation.py",
    "outcome_learning.py": "source/tests/test_outcome_learning.py",
    "experiment_decision.py": "source/tests/test_experiment_decision.py",
    "owner_context.py": "source/tests/test_owner_context.py",
}
OPERATORS = {
    "==": ("!=",),
    "!=": ("==",),
    "<": (">=", "<="),
    "<=": (">", "<"),
    ">": ("<=", ">="),
    ">=": ("<", ">"),
    "is": ("is not",),
    "is not": ("is",),
    "in": ("not in",),
    "not in": ("in",),
    "and": ("or",),
    "or": ("and",),
    "+": ("-",),
    "-": ("+",),
    "*": ("+",),
}
COMPARE = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
    ast.Gt: ">", ast.GtE: ">=", ast.Is: "is", ast.IsNot: "is not",
    ast.In: "in", ast.NotIn: "not in",
}
BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}
BOOL = {ast.And: "and", ast.Or: "or"}


def sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def candidates(path: str, raw: bytes) -> list[dict[str, Any]]:
    """Generate syntactically valid, exact single-line AST operator changes."""
    data = raw.decode("utf-8")
    lines = data.splitlines(keepends=True)
    try:
        tree = ast.parse(data, filename=path)
    except SyntaxError as exc:
        raise ValueError("baseline Python source is invalid") from exc
    out = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        left: ast.AST
        right: ast.AST
        operation: str
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = node.left, node.comparators[0]
            operation = COMPARE.get(type(node.ops[0]), "")
        elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
            left, right = node.values
            operation = BOOL.get(type(node.op), "")
        elif isinstance(node, ast.BinOp):
            left, right = node.left, node.right
            operation = BIN.get(type(node.op), "")
        else:
            continue
        if (
            operation not in OPERATORS
            or getattr(left, "end_lineno", None) is None
            or getattr(right, "lineno", None) is None
            or left.end_lineno != right.lineno
            or left.end_lineno != getattr(node, "lineno", None)
        ):
            continue
        line_idx = left.end_lineno - 1
        if line_idx < 0 or line_idx >= len(lines):
            continue
        # CPython AST column offsets are UTF-8 *byte* offsets.
        line_bytes = lines[line_idx].encode("utf-8")
        start, end = left.end_col_offset, right.col_offset
        if start < 0 or start >= end or end > len(line_bytes):
            continue
        space = line_bytes[start:end].decode("utf-8")
        m = re.fullmatch(r"(\s*)" + re.escape(operation) + r"(\s*)", space)
        if m is None:
            continue
        for replacement in OPERATORS[operation]:
            mutated_bytes = (
                line_bytes[:start]
                + (m.group(1) + replacement + m.group(2)).encode("utf-8")
                + line_bytes[end:]
            )
            mutated_lines = list(lines)
            mutated_lines[line_idx] = mutated_bytes.decode("utf-8")
            mutated = "".join(mutated_lines).encode("utf-8")
            if mutated == raw or sha(mutated) in seen:
                continue
            try:
                ast.parse(mutated, filename=path)
            except SyntaxError:
                continue
            seen.add(sha(mutated))
            out.append({
                "id": "r1_" + sha((path + "\n" + sha(mutated)).encode())[:24],
                "source_path": "source/src/wls/" + path,
                "source_sha256": sha(raw),
                "mutant_sha256": sha(mutated),
                "mutated_content": mutated,
                "mutation_kind": (
                    "compare" if isinstance(node, ast.Compare)
                    else "boolean" if isinstance(node, ast.BoolOp) else "arithmetic"
                ),
                "mutation_line": line_idx + 1,
                "operator_from": operation,
                "operator_to": replacement,
            })
    # AST walk order depends on source structure; stable order avoids
    # opportunistic task ranking by the evaluator.
    return sorted(out, key=lambda row: (
        row["source_path"], row["mutation_line"], row["operator_to"],
        row["mutant_sha256"],
    ))


def safe_env(home: Path, package_root: Path) -> dict[str, str]:
    """No GitHub token, user keys, model credentials or ambient Python path."""
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": str(package_root),
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "WLS_HOME": str(home),
        "HOME": str(home),
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "WLS_RSI_API_KEY": "",
    }
    for name in ("SYSTEMROOT", "TMP", "TEMP", "TMPDIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def pytest_once(root: Path, temp: Path, package_root: Path,
                selected: str, seconds: int) -> tuple[int, str]:
    argv = [
        sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
        "--disable-warnings", selected,
    ]
    try:
        result = subprocess.run(  # nosec B603: Python and file selected by fixed whitelist
            argv, cwd=root, env=safe_env(temp, package_root),
            stdin=subprocess.DEVNULL, capture_output=True, timeout=seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 124, "PROCESS_BLOCKED"
    if len(result.stdout) + len(result.stderr) > MAX_CAPTURE:
        return 125, "OUTPUT_TOO_LARGE"
    if result.returncode == 0:
        return 0, "PASS"
    if result.returncode == 1:
        return 1, "PYTEST_ASSERTION_FAILED"
    return result.returncode, "INFRASTRUCTURE_OR_COLLECTION_ERROR"


def run(root: Path, probes: int, minimum: int,
        seconds: int) -> dict[str, Any]:
    if not 1 <= probes <= MAX_PROBES or not 0 <= minimum <= MAX_CASES:
        raise ValueError("invalid R1 candidate or minimum task budget")
    if not 1 <= seconds <= MAX_PER_TEST_SECONDS:
        raise ValueError("invalid per-test timeout")
    source = root / "source/src/wls"
    if not (source / "__init__.py").is_file():
        raise ValueError("WLS canonical package missing")
    with tempfile.TemporaryDirectory(prefix="r1-real-mutants-") as tmp:
        tmp_root = Path(tmp)
        isolate = tmp_root / "package" / "wls"
        isolate.parent.mkdir(parents=True)
        shutil.copytree(source, isolate, symlinks=False)
        package_root = isolate.parent
        tested_modules: list[str] = []
        pending: list[dict[str, Any]] = []
        baseline = {}
        # Fixed allowlist, never read module/test paths supplied by a candidate.
        for module, selected in BASELINE_TESTS.items():
            target = root / "source/src/wls" / module
            test = root / selected
            if not target.is_file() or not test.is_file():
                raise ValueError(f"missing WLS baseline source or test: {module}")
            trial = candidates(module, target.read_bytes())
            if not trial:
                continue
            # The baseline has to pass with EXACTLY the same isolated package
            # and test selection as subsequent mutated runs.
            exit_code, reason = pytest_once(root, tmp_root, package_root, selected, seconds)
            baseline[module] = {"status": reason, "exit_code": exit_code}
            if exit_code == 0:
                tested_modules.append(module)
                for item in trial:
                    item["selected_test"] = selected
                    pending.append(item)
        pending.sort(key=lambda item: (
            item["source_path"], item["mutation_line"], item["mutant_sha256"],
        ))
        observed = []
        probed = 0
        failures: dict[str, int] = {}
        for item in pending:
            if probed >= probes or len(observed) >= MAX_CASES:
                break
            probed += 1
            module = Path(item["source_path"]).name
            live = isolate / module
            trusted_source = (root / item["source_path"]).read_bytes()
            if sha(live.read_bytes()) != sha(trusted_source):
                raise ValueError("mutant isolation did not restore baseline bytes")
            results = []
            try:
                live.write_bytes(item["mutated_content"])
                for _ in range(3):
                    code, reason = pytest_once(
                        root, tmp_root, package_root, item["selected_test"], seconds
                    )
                    results.append({"exit_code": code, "status": reason})
                    if code != 1:
                        break  # no fabricated three-failure proof
            finally:
                live.write_bytes(trusted_source)
            if len(results) == 3 and all(r["exit_code"] == 1 for r in results):
                observed.append({
                    key: val for key, val in item.items() if key != "mutated_content"
                } | {"three_actual_pytest_failures": True,
                     "test_invocations": len(results), "test_results": results})
            else:
                status = results[-1]["status"]
                failures[status] = failures.get(status, 0) + 1
        return {
            "schema": SCHEMA, "status": (
                "QUALIFIED_REAL_MUTATIONS" if len(observed) >= minimum
                else "BELOW_MINIMUM_REAL_MUTATIONS"
            ),
            "source_scope": "isolated_wls_package_on_GitHub_hosted_runner",
            "baseline_modules": baseline, "baseline_pass_modules": tested_modules,
            "probed_candidates": probed, "observed_task_count": len(observed),
            "minimum_required": minimum, "fixed_pytest_calls_per_admitted_task": 3,
            "not_admitted": failures,
            "tasks": observed,
            "task_digest": sha(json.dumps(observed, sort_keys=True).encode("utf-8")),
            "model_calls": 0,
            "line_order_fix_rate": "UNMEASURED",
            "random_fix_rate": "UNMEASURED",
            "heldout_gain": "UNMEASURED",
            "claim_ceiling": (
                "actual_three_run_pytest_detected_single_line_mutations_only; "
                "NOT candidate repair success, not generalization, not RSI"
            ),
        }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--probes", type=int, default=60)
    p.add_argument("--minimum", type=int, default=5)
    p.add_argument("--timeout", type=int, default=35)
    args = p.parse_args()
    try:
        report = run(args.repo.resolve(), args.probes, args.minimum, args.timeout)
    except (OSError, ValueError, TypeError) as exc:
        report = {
            "schema": SCHEMA, "status": "BLOCKED", "error": type(exc).__name__,
            "reason": str(exc)[:160], "model_calls": 0,
            "heldout_gain": "UNMEASURED",
        }
    args.output.write_text(
        json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "probed_candidates": report.get("probed_candidates", 0),
        "observed_task_count": report.get("observed_task_count", 0),
    }, sort_keys=True))
    return 0 if report["status"] == "QUALIFIED_REAL_MUTATIONS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
