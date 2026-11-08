from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .coding_adapter import (
    CODE_CONSTITUTION,
    ChangedFileReceipt,
    CodingCandidateReceipt,
    CodingTaskContract,
)


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
    tests_passed: bool = False  # untrusted text hint from model output
    independent_tests_passed: bool = False  # populated only by an external grader
    tests_output: str = ""
    error: str = ""
    scope_violations: list[str] = field(default_factory=list)

    def receipt(self, worktree: str, base_sha: str, rollback: list[str]) -> CodingCandidateReceipt:
        return CodingCandidateReceipt(
            task_id=self.task_id,
            base_sha=base_sha,
            worktree=worktree,
            changed_files=self.changed_files,
            tests=self.tests if self.independent_tests_passed and not self.scope_violations else [],
            rollback=rollback,
        )

    @property
    def succeeded(self) -> bool:
        # The worker's own stdout is not a trustworthy test receipt.
        return (self.exit_code == 0 and self.independent_tests_passed
                and not self.scope_violations)


class BaseCodingWorker:
    """Base class for external coding tool adapters."""

    def __init__(self, worker_id: str, *, timeout_seconds: int = 300) -> None:
        self.worker_id = worker_id
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return shutil.which(self._cli_name()) is not None

    def execute(self, contract: CodingTaskContract) -> CodingWorkerResult:
        # Validate workspace containment before invoking any external CLI.
        try:
            contract.validate()
        except ValueError as exc:
            return CodingWorkerResult(
                worker_id=self.worker_id,
                task_id=contract.task_id,
                exit_code=-1,
                stdout="",
                stderr="",
                elapsed_seconds=0.0,
                error=f"invalid coding task contract: {exc}",
            )
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

        try:
            baseline = self._snapshot_worktree(contract.worktree)
        except (OSError, ValueError) as exc:
            return CodingWorkerResult(
                worker_id=self.worker_id, task_id=contract.task_id,
                exit_code=-1, stdout="", stderr="", elapsed_seconds=0,
                error=f"constitution preflight failed: {exc}",
            )
        prompt = CODE_CONSTITUTION + "\n" + self._build_prompt(contract)
        start = time.monotonic()
        try:
            result = subprocess.run(
                self._build_command(prompt),
                cwd=str(contract.worktree),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
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

        try:
            after = self._snapshot_worktree(contract.worktree)
        except (OSError, ValueError) as exc:
            return CodingWorkerResult(
                worker_id=self.worker_id, task_id=contract.task_id,
                exit_code=result.returncode, stdout=result.stdout,
                stderr=result.stderr, elapsed_seconds=round(elapsed, 2),
                error=f"constitution postflight failed: {exc}",
                scope_violations=["worktree_unverifiable"],
            )
        delta = {
            name for name in baseline.keys() | after.keys()
            if baseline.get(name) != after.get(name)
        }
        allowed = {name.replace("\\", "/") for name in contract.changed_files}
        violations = sorted(delta - allowed)
        if any(name not in after for name in delta & allowed):
            violations.append("declared_file_deleted")
        if not delta & allowed:
            violations.append("no_confirmed_code_change")
        changed = [
            item for item in self._detect_changes(contract)
            if item["path"] in delta and item["path"] in allowed
        ]
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
            error="constitution rejected candidate" if violations else "",
            scope_violations=violations,
        )

    @staticmethod
    def _snapshot_worktree(root: Path) -> dict[str, str]:
        """Hash bounded real files, without following links or exposing bytes.

        This blocks candidate acceptance, not the worker's OS-level actions.
        """
        excluded = {
            ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
            ".mypy_cache", ".ruff_cache",
        }
        seen: dict[str, str] = {}
        count_bytes = 0
        for folder, directories, filenames in os.walk(root, followlinks=False):
            directory = Path(folder)
            if any((directory / name).is_symlink() for name in directories):
                raise ValueError("symlink directory cannot be audited")
            directories[:] = sorted(name for name in directories if name not in excluded)
            for filename in sorted(filenames):
                path = directory / filename
                if path.is_symlink() or not path.is_file():
                    raise ValueError("symlink or nonregular file cannot be audited")
                if len(seen) >= 5000:
                    raise ValueError("worktree audit exceeded 5000 files")
                size = path.stat().st_size
                if size > 32_000_000 or count_bytes + size > 256_000_000:
                    raise ValueError("worktree audit byte budget exceeded")
                count_bytes += size
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                seen[path.relative_to(root).as_posix()] = digest
        return seen

    def _cli_name(self) -> str:
        raise NotImplementedError

    def _build_prompt(self, contract: CodingTaskContract) -> str:
        raise NotImplementedError

    def _build_command(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def _detect_changes(self, contract: CodingTaskContract) -> list[ChangedFileReceipt]:
        files: list[ChangedFileReceipt] = []
        for rel in contract.changed_files:
            fp = contract._resolve_changed_file(rel)
            if fp.is_file():
                content = fp.read_bytes()
                files.append({
                    "path": rel.replace("\\", "/"),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                })
        return files

    def _parse_test_results(self, stdout: str, stderr: str) -> bool:
        """Conservative hint only; independent evaluator evidence is still required."""
        combined = stdout + "\n" + stderr
        if re.search(r"\b(fail(?:ed|ures)?|errors?|traceback)\b", combined, re.IGNORECASE):
            return False
        return bool(
            re.search(r"\b\d+\s+passed\b", combined, re.IGNORECASE)
            or re.search(r"\btests?\s+passed\b", combined, re.IGNORECASE)
            or re.search(r"(?m)^\s*(?:OK|PASS)\s*$", combined)
        )


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
