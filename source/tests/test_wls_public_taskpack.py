"""C2 historical TaskPack: real local git merge replay and contamination controls."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "source/scripts/build_wls_public_taskpack.py"


@pytest.fixture
def taskpack():
    spec = importlib.util.spec_from_file_location("wls_c2_taskpack", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(root: Path, *args: str) -> str:
    run = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True, timeout=12,
    )
    return run.stdout.strip()


def write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def fixture_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "test-repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "WLS Test")
    git(root, "config", "user.email", "wls@example.invalid")
    write(
        root, "source/scripts/verify_rsi_evaluator_change_boundary.py",
        'EVALUATORS = frozenset({"source/src/wls/scorer.py"})\n',
    )
    write(root, "source/src/wls/repair.py", "def repair():\n    return False\n")
    write(root, "source/src/wls/scorer.py", "VALUE = 1\n")
    write(root, "source/tests/test_repair.py", "def test_repair():\n    assert True\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial pre-fix WLS checkout")
    original = git(root, "rev-parse", "HEAD")
    return root, original


def merge_fix(root: Path, *, name: str, number: int,
              title: str, edit_scorer: bool = False,
              edit_test: bool = True) -> str:
    git(root, "checkout", "-b", name)
    write(root, "source/src/wls/repair.py", f"def repair():\n    return {number}\n")
    if edit_test:
        write(root, "source/tests/test_repair.py",
              f"def test_repair():\n    assert {number} > 0\n")
    if edit_scorer:
        write(root, "source/src/wls/scorer.py", f"VALUE = {number}\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "candidate implementation and regression")
    git(root, "checkout", "main")
    git(root, "merge", "--no-ff", name, "-m",
        f"Merge pull request #{number} from fixture/{name}\n\n{title}")
    return git(root, "rev-parse", "HEAD")


def test_mines_real_two_parent_fix_and_keeps_auditor_solution_separate(taskpack, tmp_path):
    repo, before = fixture_repo(tmp_path)
    merged = merge_fix(repo, name="repair1", number=101, title="Fix replay test regression")
    report = taskpack.build_taskpack(repo, head=merged)
    assert report["status"] == "PUBLIC_TASKS_DISCOVERED"
    assert report["task_count"] == 1
    item = report["tasks"][0]
    assert item["pre_fix_sha"] == before
    assert item["fix_merge_sha"] == merged
    assert item["pr_number"] == 101
    assert item["replay_status"] == "UNMEASURED"
    assert item["preexisting_test_files"] == ["source/tests/test_repair.py"]
    worker = taskpack.candidate_view(item)
    assert worker["checkout_sha"] == before
    assert "fix_merge_sha" not in worker
    assert "pr_number" not in worker
    assert "source_files" not in worker
    assert report["sealing"]["eligible_for_rsi_gain_claim"] is False


def test_score_authority_change_never_becomes_public_practice(taskpack, tmp_path):
    repo, _ = fixture_repo(tmp_path)
    sha = merge_fix(
        repo, name="scorer-mod", number=102, title="Fix changed scorer",
        edit_scorer=True,
    )
    report = taskpack.build_taskpack(repo, head=sha)
    assert report["task_count"] == 0
    assert report["status"] == "NO_ELIGIBLE_HISTORY"


def test_code_only_merge_does_not_invent_regression_task(taskpack, tmp_path):
    repo, _ = fixture_repo(tmp_path)
    sha = merge_fix(
        repo, name="no-regression", number=103,
        title="Feature without test", edit_test=False,
    )
    report = taskpack.build_taskpack(repo, head=sha)
    assert report["task_count"] == 0


def test_reject_changed_head_and_budget_without_weakened_labels(taskpack, tmp_path):
    repo, old = fixture_repo(tmp_path)
    sha = merge_fix(repo, name="repair2", number=104, title="Fix worker repair")
    with pytest.raises(ValueError, match="HEAD changed"):
        taskpack.build_taskpack(repo, head=old)
    with pytest.raises(ValueError, match="task limit"):
        taskpack.build_taskpack(repo, head=sha, limit=0)
    with pytest.raises(ValueError, match="immutable"):
        taskpack.build_taskpack(repo, head="main")


def test_paths_and_merge_messages_are_fail_closed(taskpack):
    with pytest.raises(ValueError, match="unsafe"):
        taskpack._paths("../private.py\n")
    with pytest.raises(ValueError, match="unsafe"):
        taskpack._paths("contains space.py\n")
    assert taskpack._title("Merge pull request #104\n\nFix task") == "Fix task"
    assert taskpack._title("direct main push\n\nFix task") is None


def test_three_way_merge_has_stable_pre_fix_parent(taskpack, tmp_path):
    repo, _ = fixture_repo(tmp_path)
    first = merge_fix(repo, name="branch1", number=105, title="Fix prior issue")
    second = merge_fix(repo, name="branch2", number=106, title="Fix newest issue")
    report = taskpack.build_taskpack(repo, head=second, limit=2)
    assert [item["pr_number"] for item in report["tasks"]] == [106, 105]
    assert report["tasks"][0]["pre_fix_sha"] == first
    assert all(task["split"] == "PUBLIC_DEVELOPMENT_ONLY"
               for task in report["tasks"])


def test_json_output_is_nonauthoritative_and_solution_refs_audit_only(taskpack, tmp_path):
    repo, _ = fixture_repo(tmp_path)
    sha = merge_fix(repo, name="repair3", number=107, title="Fix test")
    report = taskpack.build_taskpack(repo, head=sha)
    payload = json.dumps(report)
    assert "candidate_replay_task_not_verified_red_to_green" in payload
    assert "PUBLIC_DEVELOPMENT_ONLY" in payload
    assert not report["sealing"]["independent_holdout"]
