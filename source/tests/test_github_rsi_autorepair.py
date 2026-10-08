"""Independent scope-selection tests for the scheduled GitHub RSI code repair."""
from __future__ import annotations

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
