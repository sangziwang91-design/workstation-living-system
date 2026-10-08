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


def bounded_expression(reply: str) -> str | None:
    for line in reply.replace(chr(96), "").splitlines():
        expr = line.strip()
        if expr.startswith("if "):
            expr = expr[3:].removesuffix(":").strip()
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
            and all(not isinstance(node, ast.Name) or node.id == "relative_file" for node in nodes)
            and all(
                not isinstance(node, ast.Constant)
                or (isinstance(node.value, str) and len(node.value) < 20)
                for node in nodes
            )
        ):
            return expr
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
        raise RuntimeError("frozen behavior regression does not detect a defect")

    model_file = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "wls-coder.gguf"
    if not model_file.is_file():
        urllib.request.urlretrieve(MODEL_URL, model_file)  # nosec B310 -- fixed HTTPS URL
    if not 300_000_000 <= model_file.stat().st_size <= 480_000_000:
        raise RuntimeError("unexpected downloaded model size")
    model_sha = hashlib.sha256(model_file.read_bytes()).hexdigest()
    from llama_cpp import Llama

    model = Llama(model_path=str(model_file), n_ctx=2048, n_threads=2, verbose=False)
    prompt = (
        "Fix a Python coding-agent vulnerability. Windows paths containing ':' "
        "can use NTFS alternate data streams. Current logic checks "
        "not relative_file, Path(relative_file).is_absolute(), and "
        "PureWindowsPath(relative_file).drive, but fails to reject ':' within "
        "relative_file. Return exactly ONE Python boolean expression using "
        "relative_file which evaluates True for paths containing a colon. "
        "No if statement, markdown, comments or explanation."
    )
    generations: list[dict[str, object]] = []
    for generation, temp in enumerate((0.0, 0.2, 0.4), 1):
        response = model.create_chat_completion(
            messages=[
                {"role": "system", "content": "Return only minimal Python code."},
                {"role": "user", "content": prompt},
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
        "claims": "actual untrusted LLM code generation, independently graded",
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
        with Path(output).open("a", encoding="utf-8") as receipt:
            receipt.write(f"accepted={'true' if accepted else 'false'}\n")
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
    raise SystemExit(0 if result["accepted"] else 1)
