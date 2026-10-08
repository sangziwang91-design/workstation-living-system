"""A real small coder LLM proposes a restricted WLS code repair on GitHub CI.

No API keys: a locally loaded public GGUF model only proposes code. Trusted
regression tests and the complete WLS test suite decide whether to keep it.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess  # nosec B404
import sys
import urllib.request
from pathlib import Path

SOURCE = Path("source/src/wls/coding_adapter.py")
MODEL_ID = "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M"
EXPECTED_MODEL_SHA256 = "0128e77564e43d40682f82d7ebe8a9abdf0c24c8f55fa85629f8cc156b1b6560"
MODEL_URL = (
    "https://huggingface.co/bartowski/"
    "Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/"
    "Qwen2.5-Coder-0.5B-Instruct-Q4_K_M.gguf"
)
ORACLE_SOURCE = '''import pytest
from wls.coding_adapter import CodingTaskContract


@pytest.mark.parametrize("bad", [
    "module.py:payload", "sub/module.py:$DATA",
    "sub\\\\module.py:payload", ":hidden.py",
])
def test_ads_path_rejected(tmp_path, bad):
    contract = CodingTaskContract(
        task_id="ads", base_sha="head", worktree=tmp_path.resolve(),
        changed_files=[bad], tests=["pytest"], rollback=["discard"],
    )
    with pytest.raises(ValueError, match="relative paths"):
        contract.validate()


@pytest.mark.parametrize("bad", ["module\\nother.py", "module\\rother.py",
                                 "pkg/name\\nmore.py", "pkg/name\\rmore.py"])
def test_log_delimiter_path_rejected(tmp_path, bad):
    contract = CodingTaskContract(
        task_id="logdelimiter", base_sha="head", worktree=tmp_path.resolve(),
        changed_files=[bad], tests=["pytest"], rollback=["discard"],
    )
    with pytest.raises(ValueError, match="relative paths"):
        contract.validate()


@pytest.mark.parametrize("bad", ["module\\x00other.py", "pkg/name\\x00more.py"])
def test_nul_path_rejected_with_consistent_contract_error(tmp_path, bad):
    contract = CodingTaskContract(
        task_id="nul", base_sha="head", worktree=tmp_path.resolve(),
        changed_files=[bad], tests=["pytest"], rollback=["discard"],
    )
    with pytest.raises(ValueError, match="relative paths"):
        contract.validate()


def test_normal_path_accepted(tmp_path):
    contract = CodingTaskContract(
        task_id="good", base_sha="head", worktree=tmp_path.resolve(),
        changed_files=["module.py"], tests=["pytest"], rollback=["discard"],
    )
    contract.validate()
'''


def command(*args: str, timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 -- fixed trusted argv
        list(args), capture_output=True, text=True, check=False, timeout=timeout,
    )


def normalize_bounded_any_guard(expr: str) -> str | None:
    """Conservatively reduce the *observed* Qwen any-equality predicate.

    A pure single-character equality comprehension is mathematically the
    same as string membership. Never execute the untrusted model's call,
    comprehension, imports, function definition or surrounding statements.
    Do not admit .endswith(), which misses embedded NUL characters.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    body = tree.body
    if (
        not isinstance(body, ast.Call)
        or not isinstance(body.func, ast.Name)
        or body.func.id != "any"
        or body.keywords
        or len(body.args) != 1
        or not isinstance(body.args[0], ast.GeneratorExp)
    ):
        return None
    generator = body.args[0]
    if len(generator.generators) != 1:
        return None
    comprehension = generator.generators[0]
    if (
        comprehension.is_async or comprehension.ifs
        or not isinstance(comprehension.target, ast.Name)
        or not isinstance(comprehension.iter, ast.Name)
        or comprehension.iter.id not in {"relative_file", "filename", "path"}
    ):
        return None
    predicate = generator.elt
    if (
        not isinstance(predicate, ast.Compare)
        or not isinstance(predicate.left, ast.Name)
        or predicate.left.id != comprehension.target.id
        or len(predicate.ops) != 1
        or not isinstance(predicate.ops[0], ast.Eq)
        or len(predicate.comparators) != 1
        or not isinstance(predicate.comparators[0], ast.Constant)
        or predicate.comparators[0].value != "\x00"
    ):
        return None
    return ast.unparse(ast.Compare(
        left=ast.Constant(value="\x00"),
        ops=[ast.In()],
        comparators=[ast.Name(id="relative_file", ctx=ast.Load())],
    ))


def bounded_expression(reply: str) -> str | None:
    for line in reply.replace(chr(96), "").splitlines():
        expr = line.strip()
        if expr.startswith("if "):
            expr = expr[3:].removesuffix(":").strip()
        elif expr.startswith("return "):
            # Genuine GGUF round 3 wrapped the correct pure predicate in
            # a function-level return. Extract the expression, never execute
            # the surrounding function, imports, or its statements.
            expr = expr[7:].strip()
        recovered = normalize_bounded_any_guard(expr)
        if recovered is not None:
            return recovered
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            continue
        allowed = (
            ast.Expression, ast.Compare, ast.Name, ast.Constant, ast.In,
            ast.NotIn, ast.Load, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
        )
        nodes = list(ast.walk(tree))
        if (
            0 < len(expr) < 160
            and all(isinstance(node, allowed) for node in nodes)
            and all(
                not isinstance(node, ast.Name)
                or node.id in {"relative_file", "path"}
                for node in nodes
            )
            and all(
                not isinstance(node, ast.Constant)
                or (isinstance(node.value, str) and len(node.value) < 20)
                for node in nodes
            )
        ):
            # A real 0.5B coder tends to wrap a correct guard in a function
            # with parameter "path". Extract ONLY its pure if-condition,
            # normalize the parameter name, and let the frozen tests decide.
            class CanonicalizeParameter(ast.NodeTransformer):
                def visit_Name(self, node: ast.Name) -> ast.Name:
                    if node.id == "path":
                        return ast.copy_location(
                            ast.Name(id="relative_file", ctx=node.ctx), node
                        )
                    return node

            safe_tree = CanonicalizeParameter().visit(tree)
            return ast.unparse(safe_tree)
    return None


def trial() -> dict[str, object]:
    if command("git", "status", "--porcelain").stdout.strip():
        raise RuntimeError("trial requires a clean GitHub feature-branch checkout")
    base_sha = command("git", "rev-parse", "HEAD").stdout.strip()
    baseline_source = SOURCE.read_text(encoding="utf-8")
    needle = '            not relative_file\n'
    if SOURCE.is_symlink() or baseline_source.count(needle) != 1:
        raise RuntimeError("WLS coding task implementation changed")
    oracle = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "test_wls_ads_oracle.py"
    oracle.write_text(ORACLE_SOURCE, encoding="utf-8")
    baseline = command(sys.executable, "-m", "pytest", "-q", str(oracle), timeout=90)
    if baseline.returncode == 0:
        # A previous model-generated and verified code promotion already
        # closed this goal. Do not redownload the model or re-execute it.
        record: dict[str, object] = {
            "schema": "wls.hosted_small_llm_coding.v1",
            "base_sha": base_sha,
            "model_id": MODEL_ID,
            "status": "NO_GAIN",
            "accepted": False,
            "source": str(SOURCE),
            "goal_id": "safe_relative_embedded_nul_v3",
            "model_called": False,
            "reason": "frozen ADS owner oracle already passes",
        }
        Path("rsi-model-report.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a", encoding="utf-8") as github_output_file:
                github_output_file.write("accepted=false\n")
        print(json.dumps(record, ensure_ascii=False), flush=True)
        return record

    model_file = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "wls-coder.gguf"
    if not model_file.is_file():
        urllib.request.urlretrieve(MODEL_URL, model_file)  # nosec B310 -- fixed HTTPS URL
    if not 300_000_000 <= model_file.stat().st_size <= 480_000_000:
        raise RuntimeError("unexpected downloaded model size")
    model_sha = hashlib.sha256(model_file.read_bytes()).hexdigest()
    if model_sha != EXPECTED_MODEL_SHA256:
        raise RuntimeError("downloaded model bytes changed from independently verified GGUF")
    from llama_cpp import Llama

    model = Llama(model_path=str(model_file), n_ctx=2048, n_threads=2, verbose=False)
    prompt = (
        "You are repairing a real Python coding-task path validation defect. "
        "An attacker can put a NUL U+0000 character INSIDE relative_file, "
        "causing embedded-NUL operating system errors instead of a clean "
        "ValueError('changed files must be relative paths'). Existing code "
        "already rejects drive names, ADS colons, CR and LF. "
        "Return exactly one Python boolean expression using relative_file "
        "which is true when the filename contains a NUL codepoint and false "
        "for normal filenames. Use Python escaped string literal membership, "
        "not a regex or a function call. "
        "No imports, function declarations, if statements or explanation."
    )
    feedback = ""
    generations: list[dict[str, object]] = []
    for generation, temp in enumerate((0.0, 0.2, 0.4), 1):
        response = model.create_chat_completion(
            messages=[
                {"role": "system", "content": "Return only minimal Python code."},
                {"role": "user", "content": prompt + feedback},
            ],
            temperature=temp, max_tokens=96,
        )
        raw = str(response["choices"][0]["message"]["content"] or "")
        expr = bounded_expression(raw)
        receipt: dict[str, object] = {
            "generation": generation, "model_reply": raw[:400],
            "proposed_expression": expr, "accepted": False,
        }
        generations.append(receipt)
        if expr is None:
            feedback = (
                "\nPrevious candidate could not be parsed as a pure safe "
                "membership predicate; try a minimal expression without "
                "any wrapper or explanations."
            )
            continue
        candidate_source = baseline_source.replace(
            needle, needle + f"            or ({expr})\n",
        )
        SOURCE.write_text(candidate_source, encoding="utf-8")
        behavior = command(sys.executable, "-m", "pytest", "-q", str(oracle), timeout=90)
        receipt["oracle_exit"] = behavior.returncode
        if behavior.returncode == 0:
            full = command(
                sys.executable, "-m", "pytest", "-q", "source/tests",
                "--maxfail=1", timeout=1200,
            )
            receipt["full_suite_exit"] = full.returncode
            receipt["test_output_tail"] = (full.stdout + full.stderr)[-800:]
            if full.returncode == 0:
                receipt["accepted"] = True
                break
        # Next generation sees only bounded independent failure evidence,
        # not the model's self-assessment or private evaluator source.
        feedback = (
            "\nThe prior proposed guard did not pass the independent tests. "
            "Fix the literal or logic. Failure excerpt (untrusted test data): "
            + (behavior.stdout + behavior.stderr)[-360:].replace("\x00", "")
        )
        SOURCE.write_text(baseline_source, encoding="utf-8")

    accepted = any(bool(item["accepted"]) for item in generations)
    report: dict[str, object] = {
        "schema": "wls.hosted_small_llm_coding.v1",
        "base_sha": base_sha,
        "model_id": MODEL_ID,
        "model_sha256": model_sha,
        "original_hidden_test_exit": baseline.returncode,
        "generations": generations,
        "accepted": accepted,
        "source": str(SOURCE),
            "goal_id": "safe_relative_embedded_nul_v3",
        "claims": "third bounded real-model source repair, independently graded",
    }
    if accepted:
        diff = command("git", "diff", "--binary", "--", str(SOURCE))
        if diff.returncode or not diff.stdout.strip():
            raise RuntimeError("passing model candidate has no real source patch")
        patch = diff.stdout.encode("utf-8")
        Path("rsi-model-candidate.patch").write_bytes(patch)
        report["patch_sha256"] = hashlib.sha256(patch).hexdigest()
    Path("rsi-model-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as github_output_file:
            github_output_file.write(f"accepted={'true' if accepted else 'false'}\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    try:
        result = trial()
    except Exception as exc:
        Path("rsi-model-report.json").write_text(
            json.dumps({"accepted": False, "blocker": f"{type(exc).__name__}: {exc}"}),
            encoding="utf-8",
        )
        raise
    raise SystemExit(0 if result["accepted"] or result.get("status") == "NO_GAIN" else 1)
