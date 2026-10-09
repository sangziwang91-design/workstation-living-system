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
    "source/src/wls/schemas.py",
    "source/src/wls/rsi_artifact_gate.py",
    "source/src/wls/rsi_evolution.py",
    "source/scripts/generate_ci_feedback.py",
    "source/src/wls/task_admission.py",
    "source/src/wls/benchmark.py",
    "source/src/wls/evaluator.py",
    "source/src/wls/experiment_decision.py",
    "source/scripts/github_rsi_replay_model.py",
    "source/scripts/run_external_risk_holdout.py",
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


def test_root_build_inputs_count_as_code(guard):
    for name in ("pyproject.toml", "Dockerfile", "requirements.txt", "evals/checks.py"):
        result = guard.check_files(["source/src/wls/benchmark.py", name])
        assert result["status"] == "REJECT_MIXED_EVALUATOR_AND_CODE"


def test_only_scoring_and_readme_is_not_code_mixing(guard):
    assert guard.check_files([
        "source/src/wls/benchmark.py", "README.md", "CURRENT_STATE.yaml",
    ])["eligible"]


@pytest.mark.parametrize("code_path", [
    "setup.py",
    "scripts/repair.py",
    "tools/candidate_generator.py",
    "docs/hidden_runtime.py",
    "pkg/extensions/agent.ts",
    "ops/pipeline.yml",
    "config/runtime.json",
    "nested/Makefile",
])
def test_scorer_cannot_hide_a_second_code_change_in_any_directory(guard, code_path):
    result = guard.check_files([
        "source/src/wls/benchmark.py", code_path,
    ])
    assert result["status"] == "REJECT_MIXED_EVALUATOR_AND_CODE"
    assert code_path in result["other_code_files"]


def test_scorer_and_prose_only_can_still_be_reviewed(guard):
    result = guard.check_files([
        "source/src/wls/benchmark.py",
        "README.md",
        "docs/review-notes.md",
        "CURRENT_STATE.yaml",
    ])
    assert result["eligible"] is True


@pytest.mark.parametrize("grading_dependency", [
    "source/src/wls/schemas.py",
    "source/src/wls/rsi_artifact_gate.py",
    "source/src/wls/rsi_evolution.py",
    "source/scripts/generate_ci_feedback.py",
])
@pytest.mark.parametrize("implementation", [
    "source/src/wls/runtime.py",
    "source/tests/test_external_risk_holdout.py",
    "ops/rsi_settings.json",
])
def test_indirect_grading_authority_cannot_change_with_candidate(
    guard, grading_dependency, implementation,
):
    """Indirection through schema/grade feedback is still evaluator drift."""
    result = guard.check_files([grading_dependency, implementation])
    assert result["eligible"] is False
    assert result["status"] == "REJECT_MIXED_EVALUATOR_AND_CODE"
    assert grading_dependency in result["evaluator_files"]
    assert implementation in result["other_code_files"]


def test_grading_only_files_can_still_be_reviewed_without_candidate_code(guard):
    grading_only = [
        "source/src/wls/schemas.py",
        "source/src/wls/task_admission.py",
        "source/src/wls/benchmark.py",
    ]
    result = guard.check_files(grading_only)
    assert result["eligible"] is True
    assert result["other_code_files"] == []
