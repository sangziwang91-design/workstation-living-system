from __future__ import annotations

from pathlib import Path

import pytest

from wls.coding_adapter import CodingTaskContract
from wls.coding_workers import (
    ClaudeCodeWorker,
    CodexWorker,
    CodingWorkerFactory,
    OpenCodeWorker,
)


@pytest.fixture
def worktree(tmp_path):
    wt = tmp_path / "worktree"
    wt.mkdir()
    (wt / "main.py").write_text("print('hello')\n")
    (wt / "test_main.py").write_text("assert True\n")
    return wt


class TestClaudeCodeWorker:
    def test_worker_id(self):
        w = ClaudeCodeWorker()
        assert w.worker_id == "claude_code"

    def test_build_prompt_includes_files(self, worktree):
        w = ClaudeCodeWorker()
        contract = CodingTaskContract(
            task_id="task-1",
            base_sha="abc123",
            worktree=worktree,
            changed_files=["main.py"],
            tests=["pytest"],
            rollback=["git checkout main.py"],
        )
        prompt = w._build_prompt(contract)
        assert "task-1" in prompt
        assert "abc123" in prompt
        assert "main.py" in prompt
        assert "pytest" in prompt
        assert "Do NOT merge" in prompt

    def test_is_available_detects_cli(self):
        w = ClaudeCodeWorker()
        result = w.is_available()
        assert isinstance(result, bool)


class TestCodexWorker:
    def test_worker_id(self):
        w = CodexWorker()
        assert w.worker_id == "codex"

    def test_build_prompt(self, worktree):
        w = CodexWorker()
        contract = CodingTaskContract(
            task_id="t1",
            base_sha="def456",
            worktree=worktree,
            changed_files=["src/app.py"],
            tests=["npm test"],
            rollback=["git checkout src/app.py"],
        )
        prompt = w._build_prompt(contract)
        assert "t1" in prompt
        assert "def456" in prompt


class TestOpenCodeWorker:
    def test_worker_id(self):
        w = OpenCodeWorker()
        assert w.worker_id == "opencode"

    def test_build_prompt(self, worktree):
        w = OpenCodeWorker()
        contract = CodingTaskContract(
            task_id="oc-1",
            base_sha="ghi789",
            worktree=worktree,
            changed_files=["lib.js"],
            tests=["jest"],
            rollback=["git checkout lib.js"],
        )
        prompt = w._build_prompt(contract)
        assert "oc-1" in prompt
        assert "ghi789" in prompt


class TestCodingWorkerFactory:
    def test_status_returns_all(self):
        factory = CodingWorkerFactory()
        status = factory.status()
        assert "claude_code" in status
        assert "codex" in status
        assert "opencode" in status
        for name, info in status.items():
            assert "available" in info
            assert "worker_id" in info

    def test_available_returns_list(self):
        factory = CodingWorkerFactory()
        avail = factory.available()
        assert isinstance(avail, list)

    def test_get_unknown_returns_none(self):
        factory = CodingWorkerFactory()
        assert factory.get("nonexistent") is None

    def test_best_available_returns_worker_or_none(self):
        factory = CodingWorkerFactory()
        best = factory.best_available()
        assert best is None or isinstance(best, ClaudeCodeWorker)


class TestCodingWorkerExecution:
    def test_unavailable_worker_returns_error(self, worktree):
        w = ClaudeCodeWorker()
        if w.is_available():
            pytest.skip("Claude Code is installed — skipping unavailable test")
        contract = CodingTaskContract(
            task_id="t1",
            base_sha="abc",
            worktree=worktree,
            changed_files=["main.py"],
            tests=["pytest"],
            rollback=["git checkout"],
        )
        result = w.execute(contract)
        assert result.exit_code == -1
        assert "not found" in result.error

    def test_detect_changes_finds_files(self, worktree):
        w = ClaudeCodeWorker()
        contract = CodingTaskContract(
            task_id="t1",
            base_sha="abc",
            worktree=worktree,
            changed_files=["main.py"],
            tests=["pytest"],
            rollback=["git checkout"],
        )
        changes = w._detect_changes(contract)
        assert len(changes) == 1
        assert changes[0]["path"] == "main.py"
        assert changes[0]["bytes"] > 0

    def test_parse_test_results_pass(self):
        w = ClaudeCodeWorker()
        assert w._parse_test_results("tests passed", "")
        assert w._parse_test_results("OK", "")

    def test_parse_test_results_fail(self):
        w = ClaudeCodeWorker()
        assert not w._parse_test_results("FAIL: test_x", "")
        assert not w._parse_test_results("", "error: something")

    def test_worker_result_receipt(self, worktree):
        from wls.coding_workers import CodingWorkerResult

        result = CodingWorkerResult(
            worker_id="test",
            task_id="t1",
            exit_code=0,
            stdout="ok",
            stderr="",
            elapsed_seconds=1.0,
            changed_files=[{"path": "x.py", "sha256": "abc", "bytes": 100}],
            tests=["pytest"],
            tests_passed=True,
            tests_output="all good",
        )
        receipt = result.receipt("wt", "sha1", ["rollback step"])
        assert receipt.task_id == "t1"
        assert receipt.status == "CANDIDATE_ONLY"
        assert len(receipt.changed_files) == 1

    def test_worker_result_succeeded(self):
        from wls.coding_workers import CodingWorkerResult

        r = CodingWorkerResult("w", "t", 0, "", "", 0.5, tests_passed=True)
        assert r.succeeded
        r2 = CodingWorkerResult("w", "t", 1, "", "", 0.5, tests_passed=True)
        assert not r2.succeeded
