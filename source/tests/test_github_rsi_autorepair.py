"""Independent scope-selection tests for the scheduled GitHub RSI code repair."""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def repair_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "github_rsi_autorepair.py"
    spec = importlib.util.spec_from_file_location("tested_rsi_repair", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selects_next_two_safe_fixable_tracked_source_files_only(
    repair_module, monkeypatch, tmp_path,
):
    monkeypatch.chdir(tmp_path)
    names = [
        "source/scripts/first.py",
        "source/scripts/second.py",
        "source/src/wls/third.py",
        "source/scripts/github_rsi_autorepair.py",
        "source/tests/hidden_owner_test.py",
    ]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def f():\n    return 1\n", encoding="utf-8")

    def fake_command(*argv, **kwargs):
        assert argv[:3] == ("git", "ls-files", "--")
        return subprocess.CompletedProcess(argv, 0, "\n".join(names), "")

    def entry(name, fix="safe"):
        return {
            "filename": str((tmp_path / name).resolve()),
            "fix": {"applicability": fix},
        }

    diagnostics = [
        entry("source/scripts/first.py"),
        entry("source/scripts/first.py"),
        entry("source/scripts/second.py"),
        entry("source/src/wls/third.py"),
        entry("source/src/wls/third.py", "unsafe"),
        entry("source/scripts/github_rsi_autorepair.py"),
        entry("source/tests/hidden_owner_test.py"),
        entry("../outside.py"),
    ]
    monkeypatch.setattr(repair_module, "call", fake_command)
    monkeypatch.setattr(repair_module, "diagnostics", lambda *args: diagnostics)
    assert repair_module.select_scope() == (
        "source/scripts/first.py",
        "source/scripts/second.py",
    )


def test_no_safe_fix_is_explicit_verified_no_gain_without_candidate(
    repair_module, monkeypatch, tmp_path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    result = repair_module.no_gain("b" * 40, (), 0)
    assert result["status"] == "NO_GAIN"
    assert result["eligible"] is False
    assert result["gain"] == 0
    assert not (tmp_path / "rsi-repair-candidate.patch").exists()
    stored = json.loads((tmp_path / "rsi-repair-report.json").read_text())
    assert stored == result


def test_signed_candidate_cannot_select_benchmark_or_promotion_code(
    repair_module, monkeypatch, tmp_path,
):
    """Positive and negative scopes use the existing deterministic selector."""
    protected = {
        "source/src/wls/benchmark.py",
        "source/src/wls/evaluator.py",
        "source/src/wls/experiment_decision.py",
        "source/src/wls/rsi_evolution.py",
    }
    assert protected <= repair_module.BLOCKED_FILES
    allowed = "source/src/wls/other_feature.py"
    paths = sorted(protected | {allowed})
    monkeypatch.chdir(tmp_path)
    for name in paths:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("x=1\n", encoding="utf-8")

    def fake_call(*argv, **kwargs):
        assert argv[:3] == ("git", "ls-files", "--")
        return subprocess.CompletedProcess(argv, 0, "\n".join(paths), "")

    monkeypatch.setattr(repair_module, "call", fake_call)
    monkeypatch.setattr(
        repair_module, "diagnostics",
        lambda *args: [
            {"filename": str((tmp_path / name).resolve()),
             "fix": {"applicability": "safe"}}
            for name in paths
        ],
    )
    assert repair_module.select_scope() == (allowed,)


def test_all_trusted_scorers_protected_by_both_autorepair_gates(repair_module):
    """Every grader input stays frozen in candidate selection and write-token job."""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    spec = importlib.util.spec_from_file_location(
        "trusted_scorer_catalog", scripts / "verify_rsi_evaluator_change_boundary.py"
    )
    assert spec is not None and spec.loader is not None
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    protected = set(guard.EVALUATORS)
    assert protected <= repair_module.BLOCKED_FILES

    # Statically parse the token-bearing promotion job; do not execute YAML.
    workflow_path = Path(__file__).resolve().parents[2] / (
        ".github/workflows/hosted-rsi-autorepair.yml"
    )
    workflow = workflow_path.read_text(encoding="utf-8")
    start_marker = "          blocked = {"
    assert workflow.count(start_marker) == 1
    source = workflow.split(start_marker, 1)[1].split("          }", 1)[0]
    trusted_blocked = ast.literal_eval("{" + source + "}")
    assert isinstance(trusted_blocked, set)
    assert protected <= trusted_blocked


def test_grade_files_are_not_selected_even_if_ruff_fixes_them(
    repair_module, monkeypatch, tmp_path,
):
    monkeypatch.chdir(tmp_path)
    protected = [
        "source/src/wls/schemas.py",
        "source/src/wls/task_admission.py",
        "source/scripts/generate_ci_feedback.py",
        "source/scripts/run_external_risk_holdout.py",
    ]
    allowed = "source/src/wls/ordinary_module.py"
    paths = protected + [allowed]
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x=1\n", encoding="utf-8")

    def fake_call(*argv, **kwargs):
        assert argv[:3] == ("git", "ls-files", "--")
        return subprocess.CompletedProcess(argv, 0, "\n".join(paths), "")

    monkeypatch.setattr(repair_module, "call", fake_call)
    monkeypatch.setattr(repair_module, "diagnostics", lambda *args: [
        {"filename": str((tmp_path / name).resolve()),
         "fix": {"applicability": "safe"}}
        for name in paths
    ])
    assert repair_module.select_scope() == (allowed,)
