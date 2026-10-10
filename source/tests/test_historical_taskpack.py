"""C2 negative controls using actual temporary Git repositories."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_historical_taskpack.py"


@pytest.fixture
def engine():
    spec = importlib.util.spec_from_file_location("c2_history_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    obj = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = obj
    spec.loader.exec_module(obj)
    return obj


def _git(root: Path, *args: str) -> str:
    x = subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        capture_output=True, text=True,
    )
    return x.stdout.strip()


def _sample(tmp_path: Path, *, new_test: bool = False) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "c2@example.org")
    _git(root, "config", "user.name", "C2 Tester")
    (root / "source/src/wls").mkdir(parents=True)
    (root / "source/tests").mkdir(parents=True)
    (root / "source/src/wls/legacy.py").write_text("def value(): return 0\n")
    if not new_test:
        (root / "source/tests/test_legacy.py").write_text(
            "def test_value(): assert True\n"
        )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-qb", "fix")
    (root / "source/src/wls/legacy.py").write_text("def value(): return 1\n")
    (root / "source/tests/test_legacy.py").write_text(
        "def test_value(): assert 1 == 1\n"
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fix behavior")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "--no-ff", "-qm", "Merge pull request #42 from fix", "fix")
    assert _git(root, "rev-parse", "HEAD") != base
    return root, _git(root, "rev-parse", "HEAD")


def test_real_merge_yields_public_task_with_hidden_oracle(engine, tmp_path):
    repo, head = _sample(tmp_path)
    pack = engine.mine(repo, head=head)
    assert pack["status"] == "PROVISIONAL_HISTORY_MINED"
    assert len(pack["tasks"]) == 1
    assert pack["trusted_oracles"][0]["pr_number"] == 42
    assert pack["trusted_oracles"][0]["oracle_test_paths"] == [
        "source/tests/test_legacy.py"
    ]
    assert "fix_sha" not in pack["tasks"][0]
    assert "test_legacy.py" not in str(pack["tasks"])
    assert pack["independent_successes"] == 0


def test_new_test_is_not_existing_regression_oracle(engine, tmp_path):
    repo, head = _sample(tmp_path, new_test=True)
    pack = engine.mine(repo, head=head)
    assert pack["status"] == "NO_ELIGIBLE_HISTORY"
    assert pack["skips"]["test_added_after_fix"] == 1


def test_moved_head_cannot_be_frozen_taskpack(engine, tmp_path):
    repo, _ = _sample(tmp_path)
    with pytest.raises(ValueError, match="HEAD moved"):
        engine.mine(repo, head="f" * 40)


def test_path_injection_and_budget_fail_closed(engine, tmp_path):
    with pytest.raises(ValueError, match="non-canonical"):
        engine.classify(["../../secret.py"])
    with pytest.raises(ValueError, match="duplicate"):
        engine.classify(["source/src/wls/a.py", "source/src/wls/a.py"])
    with pytest.raises(ValueError, match="scan budgets"):
        engine.mine(tmp_path, head="f" * 40, max_tasks=500)


def test_shallow_clone_cannot_certify_pre_fix_history(engine, tmp_path):
    repo, head = _sample(tmp_path)
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth", "1", "file://" + str(repo), str(shallow)],
        check=True, capture_output=True,
    )
    with pytest.raises(ValueError, match="shallow history"):
        engine.mine(shallow, head=head)
