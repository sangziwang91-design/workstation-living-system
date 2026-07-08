from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import hashlib
import shutil
import subprocess
import time

from .coding_adapter import (
    ChangedFileReceipt,
    CodingCandidateReceipt,
    CodingTaskContract,
)
from .schemas import new_id, utc_now


@dataclass(slots=True)
class CodingWorkerResult:
    worker_id: str
    task_id: str
    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    changed_files: list[ChangedFileReceipt] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    tests_passed: bool = False
    tests_output: str = ""
    error: str = ""

    def receipt(self, worktree: str, base_sha: str, rollback: list[str]) -> CodingCandidateReceipt:
        return CodingCandidateReceipt(
            task_id=self.task_id,
            base_sha=base_sha,
            worktree=worktree,
            changed_files=self.changed_files,
            tests=self.tests if self.tests_passed else [],
            rollback=rollback,
        )

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.tests_passed


class BaseCodingWorker:
    """Base class for external coding tool adapters."""

    def __init__(self, worker_id: str, *, timeout_seconds: int = 300) -> None:
        self.worker_id = worker_id
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return shutil.which(self._cli_name()) is not None

    def execute(self, contract: CodingTaskContract) -> CodingWorkerResult:
        if not self.is_available():
            return CodingWorkerResult(
                worker_id=self.worker_id,
                task_id=contract.task_id,
                exit_code=-1,
                stdout="",
                stderr="",
                elapsed_seconds=0.0,
                tests=[],
                error=f"{self._cli_name()} not found on PATH",
            )

        prompt = self._build_prompt(contract)
        start = time.monotonic()
        try:
            result = subprocess.run(
                self._build_command(prompt),
                cwd=str(contract.worktree),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            elapsed = time.monotonic() - start
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return CodingWorkerResult(
                worker_id=self.worker_id,
                task_id=contract.task_id,
                exit_code=-1,
                stdout="",
                stderr="",
                elapsed_seconds=round(elapsed, 2),
                tests=[],
                error=f"timeout after {self.timeout_seconds}s",
            )
        except (FileNotFoundError, OSError) as exc:
            elapsed = time.monotonic() - start
            return CodingWorkerResult(
                worker_id=self.worker_id,
                task_id=contract.task_id,
                exit_code=-1,
                stdout="",
                stderr="",
                elapsed_seconds=round(elapsed, 2),
                tests=[],
                error=f"{self._cli_name()} execution failed: {exc}",
            )

        changed = self._detect_changes(contract)
        test_passed = self._parse_test_results(result.stdout, result.stderr)

        return CodingWorkerResult(
            worker_id=self.worker_id,
            task_id=contract.task_id,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            elapsed_seconds=round(elapsed, 2),
            changed_files=changed,
            tests=list(contract.tests),
            tests_passed=test_passed,
            tests_output=result.stdout[-2000:] if result.stdout else "",
        )

    def _cli_name(self) -> str:
        raise NotImplementedError

    def _build_prompt(self, contract: CodingTaskContract) -> str:
        raise NotImplementedError

    def _build_command(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def _detect_changes(self, contract: CodingTaskContract) -> list[ChangedFileReceipt]:
        files: list[ChangedFileReceipt] = []
        for rel in contract.changed_files:
            fp = contract.worktree / rel
            if fp.is_file():
                content = fp.read_bytes()
                files.append({
                    "path": rel.replace("\\", "/"),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                })
        return files

    def _parse_test_results(self, stdout: str, stderr: str) -> bool:
        combined = stdout + stderr
        if "PASS" in combined or "passed" in combined or "ok" in combined.lower():
            return True
        if "FAIL" in combined or "error" in combined.lower():
            return False
        return True


class ClaudeCodeWorker(BaseCodingWorker):
    """Adapter for Claude Code CLI."""

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        super().__init__("claude_code", timeout_seconds=timeout_seconds)

    def _cli_name(self) -> str:
        return "claude"

    def _build_prompt(self, contract: CodingTaskContract) -> str:
        files = "\n".join(f"  - {f}" for f in contract.changed_files)
        tests = "\n".join(f"  - {t}" for t in contract.tests)
        return (
            f"Task: {contract.task_id}\n"
            f"Base commit: {contract.base_sha}\n\n"
            f"Modified files:\n{files}\n\n"
            f"Run these tests to verify:\n{tests}\n\n"
            f"After making changes, run the tests and report results. "
            f"Do NOT merge, push, or deploy."
        )

    def _build_command(self, prompt: str) -> list[str]:
        return ["claude", "-p", prompt, "--no-git"]


class CodexWorker(BaseCodingWorker):
    """Adapter for Codex CLI."""

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        super().__init__("codex", timeout_seconds=timeout_seconds)

    def _cli_name(self) -> str:
        return "codex"

    def _build_prompt(self, contract: CodingTaskContract) -> str:
        files = "\n".join(f"  - {f}" for f in contract.changed_files)
        tests = "\n".join(f"  - {t}" for t in contract.tests)
        return (
            f"# Task: {contract.task_id}\n"
            f"# Base: {contract.base_sha}\n\n"
            f"Files changed:\n{files}\n\n"
            f"Tests to run:\n{tests}\n\n"
            f"Make the necessary changes, run the tests, and report results. "
            f"No merge, push, or deploy."
        )

    def _build_command(self, prompt: str) -> list[str]:
        return ["codex", "exec", prompt]


class OpenCodeWorker(BaseCodingWorker):
    """Adapter for OpenCode CLI."""

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        super().__init__("opencode", timeout_seconds=timeout_seconds)

    def _cli_name(self) -> str:
        return "opencode"

    def _build_prompt(self, contract: CodingTaskContract) -> str:
        files = "\n".join(f"  - {f}" for f in contract.changed_files)
        tests = "\n".join(f"  - {t}" for t in contract.tests)
        return (
            f"Task: {contract.task_id} (base: {contract.base_sha})\n"
            f"Files: {files}\n"
            f"Tests: {tests}\n"
            f"Implement the required changes, run tests, report results. "
            f"Candidate only — no merge/push/deploy."
        )

    def _build_command(self, prompt: str) -> list[str]:
        return ["opencode", "exec", prompt]


@dataclass(slots=True)
class CodingWorkerFactory:
    """Factory that selects the best available coding worker."""

    _workers: dict[str, BaseCodingWorker] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._workers = {
            "claude_code": ClaudeCodeWorker(),
            "codex": CodexWorker(),
            "opencode": OpenCodeWorker(),
        }

    def available(self) -> list[str]:
        return [name for name, w in self._workers.items() if w.is_available()]

    def get(self, name: str) -> BaseCodingWorker | None:
        w = self._workers.get(name)
        if w is None:
            return None
        if not w.is_available():
            return None
        return w

    def best_available(self) -> BaseCodingWorker | None:
        for name in ("claude_code", "codex", "opencode"):
            w = self._workers.get(name)
            if w and w.is_available():
                return w
        return None

    def status(self) -> dict[str, Any]:
        return {
            name: {
                "available": w.is_available(),
                "worker_id": w.worker_id,
            }
            for name, w in self._workers.items()
        }
