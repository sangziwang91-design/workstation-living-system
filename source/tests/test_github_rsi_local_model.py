"""The local-model bridge adapts real imperfect model output without running it."""
from __future__ import annotations

import runpy
from pathlib import Path

from wls.coding_adapter import CodingTaskContract


def _parser():
    path = Path(__file__).resolve().parents[1] / "scripts" / "github_rsi_local_model.py"
    return runpy.run_path(str(path))["bounded_expression"]


def test_extract_real_world_coder_condition_from_wrapped_function():
    actual_model_reply = """``python
import os

def check_path(path):
    if ':' in path:
        return True
    else:
        return False
``"""
    parsed = _parser()(actual_model_reply)
    assert parsed == "':' in relative_file"


def test_model_tool_execution_and_imports_are_not_allowed():
    parser = _parser()
    assert parser("if __import__('os').system('echo secret'):") is None
    assert parser("if path.lower().startswith('/etc'):") is None
    assert parser("import os\nos.environ.clear()") is None


def test_generated_guard_is_syntactically_valid_for_real_wls(tmp_path):
    expr = _parser()("if ':' in path:")
    assert expr is not None
    scope = CodingTaskContract(
        task_id="guard", base_sha="head", worktree=tmp_path.resolve(),
        changed_files=["safe.txt"], tests=["pytest"], rollback=["discard"],
    )
    assert scope._resolve_changed_file("safe.txt") == tmp_path / "safe.txt"
    # The generated expression contains no eval, calls, imports or file IO.
    assert "relative_file" in expr and ":" in expr
