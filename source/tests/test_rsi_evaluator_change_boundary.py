"""Adversarial proof the trusted PR boundary rejects intentional test/score mixing."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_rsi_evaluator_change_boundary.py"


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location("wls_scoring_guard", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_intentionally_mixed_pr_is_rejected_by_real_cli(tmp_path):
    paths = [
        "source/src/wls/benchmark.py",
        "source/src/wls/runtime.py",
        "source/tests/test_benchmark.py",
    ]
    file = tmp_path / "changed.json"
    file.write_text(json.dumps(paths), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--fixture-file", str(file)],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 2
    verdict = json.loads(result.stdout)
    assert verdict["status"] == "REJECT_MIXED_EVALUATOR_AND_CODE"
    assert verdict["evaluator_files"] == ["source/src/wls/benchmark.py"]
    assert "source/src/wls/runtime.py" in verdict["other_code_files"]


@pytest.mark.parametrize("paths", [
    ["source/src/wls/benchmark.py", "source/src/wls/evaluator.py"],
    ["source/src/wls/runtime.py", "source/tests/test_runtime.py"],
    ["CURRENT_STATE.yaml", "README.md"],
])
def test_evaluator_only_code_only_and_documents_are_legal(guard, paths):
    assert guard.check_files(paths)["eligible"] is True


@pytest.mark.parametrize("scorer", [
    "source/src/wls/task_admission.py",
    "source/src/wls/benchmark.py",
    "source/src/wls/evaluator.py",
    "source/src/wls/experiment_decision.py",
    "source/scripts/github_rsi_replay_model.py",
])
def test_every_scoring_source_protected(guard, scorer):
    out = guard.check_files([scorer, "source/src/wls/runtime.py"])
    assert out["status"] == "REJECT_MIXED_EVALUATOR_AND_CODE"


@pytest.mark.parametrize("other", [
    "source/tests/test_rsi_evolution.py",
    "source/scripts/github_rsi_local_model.py",
    ".github/workflows/ci.yml",
    ".github/CODEOWNERS",
    "agent/improver.json",
])
def test_benchmark_change_and_test_or_workflow_is_rejected(guard, other):
    assert guard.check_files(["source/src/wls/benchmark.py", other])["eligible"] is False


def test_missing_or_oversized_pr_fails_closed(guard):
    assert guard.check_files([])["status"] == "UNMEASURED"
    assert guard.check_files(["test.py"] * 3001)["status"] == "UNMEASURED"


def test_renamed_old_evaluator_path_blocks_mix(guard):
    # GitHub file metadata reader includes both rename endpoints.
    assert not guard.check_files([
        "source/src/wls/benchmark.py", "source/src/wls/renamed_runtime.py",
    ])["eligible"]
