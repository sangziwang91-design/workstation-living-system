"""R1-A: qualify REAL one-token WLS mutations with three independent pytest processes.

Run only inside a disposable GitHub-hosted worktree. Source edits are always
restored; neither the owner machine nor real owner state is accessed.
This produces a PUBLIC DEVELOPMENT corpus, not a private holdout or RSI gain.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import platform
import subprocess  # nosec B404 -- fixed git/pytest argv, no shell
import sys
import tempfile
import time
import tokenize
from xml.etree import ElementTree

SCHEMA = "wls.r1.public_taskpack.v1"
RECEIPT_SCHEMA = "wls.r1.mutation_qualification.v1"
MIN_TARGET = 100
MAX_TARGET = 300
MAX_PROBES = 3_000
MAX_FILE_BYTES = 60_000
EXCLUDE = frozenset({
    "benchmark", "evaluator", "experiment_decision", "task_admission",
    "rsi_artifact_gate", "rsi_evolution", "db", "evidence", "schemas",
    "runtime", "architecture_validation", "ui_projection", "ui_server",
    "cli", "rsi_model_port",
})
COMPARE = {"==": "!=", "!=": "==", "<": ">=", "<=": ">",
           ">": "<=", ">=": "<"}
BOOLEAN = {"and": "or", "or": "and"}
ARITHMETIC = {"+": "-", "-": "+", "*": "//", "//": "*"}


def sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def encoded(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


@dataclass(frozen=True)
class Edit:
    line: int
    column: int
    before: str
    after: str
    kind: str


def token_between(tokens: list[tokenize.TokenInfo], line: int,
                  lo: int, hi: int, expected: str) -> Edit | None:
    matches = [
        t for t in tokens if t.start[0] == line
        and lo <= t.start[1] and t.end[1] <= hi
        and t.string == expected
    ]
    if len(matches) != 1:
        return None
    t = matches[0]
    return Edit(line, t.start[1], expected, "", "")


def mutations(raw: bytes) -> list[Edit]:
    """Generate deterministic syntactically valid single-token mutations."""
    if len(raw) > MAX_FILE_BYTES:
        return []
    try:
        source = raw.decode("utf-8")
        tree = ast.parse(source)
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (UnicodeError, SyntaxError, tokenize.TokenError):
        return []
    lines = source.splitlines(keepends=True)
    result: set[Edit] = set()
    for node in ast.walk(tree):
        edit: Edit | None = None
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = node.ops[0]
            expected = next((name for cls, name in (
                (ast.Eq, "=="), (ast.NotEq, "!="), (ast.Lt, "<"),
                (ast.LtE, "<="), (ast.Gt, ">"), (ast.GtE, ">=")
            ) if isinstance(op, cls)), None)
            lhs, rhs = node.left, node.comparators[0]
            if expected and lhs.end_lineno == rhs.lineno:
                edit = token_between(tokens, rhs.lineno, lhs.end_col_offset,
                                     rhs.col_offset, expected)
                if edit:
                    edit = Edit(edit.line, edit.column, expected,
                                COMPARE[expected], "compare")
        elif isinstance(node, ast.BoolOp) and len(node.values) >= 2:
            expected = "and" if isinstance(node.op, ast.And) else "or"
            for lhs, rhs in zip(node.values, node.values[1:]):
                if lhs.end_lineno != rhs.lineno:
                    continue
                edit = token_between(tokens, rhs.lineno, lhs.end_col_offset,
                                     rhs.col_offset, expected)
                if edit:
                    result.add(Edit(edit.line, edit.column, expected,
                                    BOOLEAN[expected], "boolean"))
            edit = None
        elif isinstance(node, ast.BinOp):
            expected = next((name for cls, name in (
                (ast.Add, "+"), (ast.Sub, "-"), (ast.Mult, "*"),
                (ast.FloorDiv, "//")
            ) if isinstance(node.op, cls)), None)
            if expected and node.left.end_lineno == node.right.lineno:
                edit = token_between(tokens, node.right.lineno,
                                     node.left.end_col_offset,
                                     node.right.col_offset, expected)
                if edit:
                    edit = Edit(edit.line, edit.column, expected,
                                ARITHMETIC[expected], "arithmetic")
        elif isinstance(node, ast.Constant):
            value = node.value
            if type(value) is bool:
                before = "True" if value else "False"
                after = "False" if value else "True"
            elif type(value) is int and 0 <= value <= 100:
                before = str(value)
                after = str(value + 1 if value != 1 else 0)
            else:
                continue
            edit = Edit(node.lineno, node.col_offset, before, after, "literal")
        if edit:
            result.add(edit)
    qualified: list[Edit] = []
    for edit in sorted(result, key=lambda e: (e.line, e.column, e.kind, e.after)):
        if not 1 <= edit.line <= len(lines):
            continue
        line = lines[edit.line - 1]
        if not line[:edit.column].isascii():
            continue  # AST columns count UTF-8 bytes, tokenize counts chars.
        if line[edit.column:edit.column + len(edit.before)] != edit.before:
            continue
        qualified.append(edit)
    return qualified


def apply(raw: bytes, edit: Edit) -> bytes | None:
    try:
        source = raw.decode("utf-8")
        lines = source.splitlines(keepends=True)
        old = lines[edit.line - 1]
        if old[edit.column:edit.column + len(edit.before)] != edit.before:
            return None
        lines[edit.line - 1] = (
            old[:edit.column] + edit.after + old[edit.column + len(edit.before):]
        )
        modified = "".join(lines)
        ast.parse(modified)
        if modified == source:
            return None
        return modified.encode("utf-8")
    except (UnicodeError, SyntaxError, IndexError):
        return None


def run_test(root: Path, test: Path, junit: Path, timeout: int) -> dict:
    junit.unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "source/src") + os.pathsep + env.get("PYTHONPATH", "")
    # Never read a real WLS home, even if caller inherited an owner environment.
    env["WLS_HOME"] = str(junit.parent / "synthetic-wls-home")
    cmd = [sys.executable, "-m", "pytest", "-q", "-x",
           "--tb=no", "--disable-warnings", f"--junitxml={junit}",
           str(test.relative_to(root))]
    start = time.monotonic()
    try:
        result = subprocess.run(  # nosec B603 -- fixed Python/pytest invocation
            cmd, cwd=root, env=env, capture_output=True, timeout=timeout,
            check=False,
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        return {"valid": False, "exit_code": "TIMEOUT", "tests": 0,
                "failures": 0, "errors": 0, "elapsed_ms": int((time.monotonic() - start) * 1000)}
    elapsed_ms = int((time.monotonic() - start) * 1000)
    try:
        xml = ElementTree.parse(junit).getroot()
        suite = xml.find("testsuite") if xml.tag != "testsuite" else xml
        if suite is None:
            raise ValueError("missing test suite")
        tests = int(suite.attrib.get("tests", "0"))
        failures = int(suite.attrib.get("failures", "0"))
        errors = int(suite.attrib.get("errors", "0"))
        skipped = int(suite.attrib.get("skipped", "0"))
    except (OSError, ValueError, ElementTree.ParseError):
        return {"valid": False, "exit_code": exit_code, "tests": 0,
                "failures": 0, "errors": 0, "elapsed_ms": elapsed_ms}
    valid = tests > 0 and errors == 0 and skipped < tests and exit_code in (0, 1)
    return {
        "valid": valid, "exit_code": exit_code, "tests": tests,
        "failures": failures, "errors": errors, "elapsed_ms": elapsed_ms,
    }


def is_baseline_pass(result: dict) -> bool:
    return result["valid"] and result["exit_code"] == 0 and result["failures"] == 0


def is_killed(result: dict) -> bool:
    return result["valid"] and result["exit_code"] == 1 and result["failures"] > 0


def source_test_pairs(root: Path) -> list[tuple[Path, Path]]:
    base = root / "source/src/wls"
    tests = root / "source/tests"
    pairs = []
    for src in sorted(base.glob("*.py")):
        if src.stem in EXCLUDE or src.is_symlink() or src.stat().st_size > MAX_FILE_BYTES:
            continue
        test = tests / f"test_{src.stem}.py"
        if test.is_file() and not test.is_symlink():
            pairs.append((src, test))
    return pairs


def qualify(root: Path, *, target: int, max_probes: int,
            timeout: int, deadline_seconds: int,
            max_files: int = 100) -> tuple[dict, dict]:
    if not MIN_TARGET <= target <= MAX_TARGET:
        raise ValueError("R1 requires at least 100 genuinely killed mutations")
    if not target <= max_probes <= MAX_PROBES or not 3 <= timeout <= 180:
        raise ValueError("unsafe mutation qualification budget")
    if deadline_seconds < 60:
        raise ValueError("deadline must allow actual independent tests")
    root = root.resolve(strict=True)
    if not (root / ".git").exists() and not (root / ".git").is_file():
        raise ValueError("must run in a disposable Git worktree")
    head = subprocess.run(  # nosec B603 -- fixed git rev-parse
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
        text=True, check=True, timeout=10,
    ).stdout.strip()
    if len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise ValueError("invalid immutable source commit")
    started = time.monotonic()
    tasks: list[dict] = []
    evidence: list[dict] = []
    stats: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(prefix="wls-r1-public-") as tmp:
        junit = Path(tmp) / "result.xml"
        for source, test in source_test_pairs(root)[:max_files]:
            if len(tasks) >= target or stats["probes"] >= max_probes:
                break
            if time.monotonic() - started > deadline_seconds:
                stats["deadline_reached"] += 1
                break
            original = source.read_bytes()
            baseline = run_test(root, test, junit, timeout)
            stats["baseline_test_invocations"] += 1
            if not is_baseline_pass(baseline):
                stats["invalid_baselines"] += 1
                continue
            candidates = mutations(original)
            stats["candidate_mutations"] += len(candidates)
            for edit in candidates:
                if len(tasks) >= target or stats["probes"] >= max_probes:
                    break
                if time.monotonic() - started > deadline_seconds:
                    stats["deadline_reached"] += 1
                    break
                mutant = apply(original, edit)
                if mutant is None:
                    stats["invalid_mutations"] += 1
                    continue
                # No changes to source outside the disposable checkout.
                trials: list[dict] = []
                try:
                    source.write_bytes(mutant)
                    for _ in range(3):
                        trial = run_test(root, test, junit, timeout)
                        stats["mutant_test_invocations"] += 1
                        trials.append(trial)
                        if not is_killed(trial):
                            break
                finally:
                    source.write_bytes(original)
                if source.read_bytes() != original:
                    raise RuntimeError("failed to restore source bytes")
                stats["probes"] += 1
                if len(trials) != 3 or not all(is_killed(t) for t in trials):
                    stats["not_three_times_killed"] += 1
                    continue
                source_rel = source.relative_to(root).as_posix()
                test_rel = test.relative_to(root).as_posix()
                mutant_hash = sha(mutant)
                task_id = f"r1_{source.stem}_{edit.line}_{edit.column}_{mutant_hash[:12]}"
                task = {
                    "id": task_id,
                    "source_path": source_rel,
                    "source_sha256": sha(original),
                    "mutant_sha256": mutant_hash,
                    "mutation_kind": edit.kind,
                    "mutation_line": edit.line,
                    "test_paths": [test_rel],
                    # Candidate ordering is NOT yet measured. This one digest
                    # is a recovery reference, not a line-order baseline score.
                    "candidate_patch_sha256s": [sha(original)],
                }
                tasks.append(task)
                evidence.append({
                    "id": task_id, "source_path": source_rel,
                    "mutation": {
                        "line": edit.line, "column": edit.column,
                        "before": edit.before, "after": edit.after,
                        "kind": edit.kind,
                    },
                    "mutant_sha256": mutant_hash,
                    "test_path": test_rel,
                    "baseline": baseline,
                    "independent_mutant_trials": trials,
                    "three_repeat_killed": True,
                })
                stats["three_repeat_killed"] += 1
                if len(tasks) % 10 == 0:
                    print(f"R1_REAL_KILLS {len(tasks)}/{target} probes={stats['probes']}",
                          flush=True)
            # Fail closed on source pollution even when all mutants are rejected.
            if source.read_bytes() != original:
                raise RuntimeError("mutation contaminated checkout")
    pack = {
        "schema": SCHEMA, "source_head": head,
        "max_test_invocations": 3, "baseline_seed": 20261011,
        "tasks": tasks,
    }
    receipt = {
        "schema": RECEIPT_SCHEMA, "source_head": head,
        "platform": platform.system().lower(),
        "python": platform.python_version(),
        "public_development_only": True,
        "hidden_holdout_used": False,
        "model_calls": 0,
        "model_gain": "UNMEASURED",
        "line_order_baseline": "UNMEASURED",
        "qualification": "THREE_INDEPENDENT_PYTEST_RUNS" if len(tasks) >= target
                         else "INSUFFICIENT_KILLED_MUTATIONS",
        "taskpack_sha256": sha(encoded(pack)),
        "task_count": len(tasks),
        "target": target, "max_probes": max_probes,
        "stats": dict(stats),
        "evidence": evidence,
    }
    return pack, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--target", type=int, default=100)
    parser.add_argument("--max-probes", type=int, default=1600)
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--deadline-seconds", type=int, default=4200)
    args = parser.parse_args()
    try:
        pack, receipt = qualify(
            args.repo, target=args.target, max_probes=args.max_probes,
            timeout=args.timeout, deadline_seconds=args.deadline_seconds,
        )
        args.tasks.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.tasks.write_bytes(encoded(pack))
        args.receipt.write_bytes(encoded(receipt))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps({
        "status": receipt["qualification"], "task_count": receipt["task_count"],
        "taskpack_sha256": receipt["taskpack_sha256"],
        "stats": receipt["stats"], "learning_gain": "UNMEASURED",
    }, sort_keys=True))
    return 0 if receipt["task_count"] >= args.target else 1


if __name__ == "__main__":
    raise SystemExit(main())
