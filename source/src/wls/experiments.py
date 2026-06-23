from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
import hashlib
import json
import os
import re

from .config import RuntimeConfig
from .evaluator import Evaluator
from .policy import PolicyEngine
from .schemas import ActionSpec, RiskLevel
from .tools import ToolRegistry


DEFAULT_ACCEPTANCE: dict[str, list[str]] = {
    "noop": ["output ok is true"],
    "read_file": ["output contains path", "output contains text or binary marker"],
    "list_directory": ["output contains items"],
    "write_file": ["output contains path"],
    "emit_note": ["output contains path"],
}


class IsolatedToolHarness:
    """Run declarative actions inside a path-rewritten filesystem sandbox."""

    def __init__(self, sandbox_root: Path, allowed_tools: list[str]):
        self.root = sandbox_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_tools = set(allowed_tools)
        config = RuntimeConfig(
            home=str(self.root / ".runtime"),
            read_only=False,
            allow_autonomous_read_actions=True,
            allow_autonomous_reversible_writes=True,
            sensors=[],
            tool_policy={
                "allowed_read_roots": [str(self.root)],
                "allowed_write_roots": [str(self.root)],
                "allowed_hosts": [],
                "allowed_commands": [],
            },
        )
        config.ensure_directories()
        self.tools = ToolRegistry(PolicyEngine(config))
        self.evaluator = Evaluator()

    def execute(
        self,
        tool: str,
        arguments: dict[str, Any],
        purpose: str,
        acceptance: list[str] | None = None,
        risk: RiskLevel = RiskLevel.READ,
        fixtures: list[dict[str, Any]] | None = None,
        auto_fixture: bool = True,
    ) -> dict[str, Any]:
        if tool not in self.allowed_tools:
            return {
                "passed": False,
                "tool": tool,
                "reason": "tool_not_allowed_for_isolated_validation",
            }
        self.prepare_fixtures(fixtures or [])
        rewritten = self.rewrite_arguments(tool, arguments)
        if auto_fixture:
            self.ensure_default_fixture(tool, rewritten)
        action = ActionSpec(
            tool=tool,
            arguments=rewritten,
            purpose=purpose,
            expected_result="isolated contract passes",
            acceptance=list(acceptance or DEFAULT_ACCEPTANCE.get(tool, [])),
            risk=risk,
        )
        result = self.tools.execute(action)
        evaluation = self.evaluator.evaluate(action, result)
        postconditions = self.postconditions(tool, rewritten, result.output)
        passed = bool(evaluation["accepted"]) and all(
            item["passed"] for item in postconditions
        )
        return {
            "passed": passed,
            "tool": tool,
            "evaluation": evaluation,
            "postconditions": postconditions,
            "output_summary": self.summarize_output(result.output),
            "error": result.error,
        }

    def prepare_fixtures(self, fixtures: list[dict[str, Any]]) -> None:
        for fixture in fixtures:
            if not isinstance(fixture, dict) or "path" not in fixture:
                continue
            path = self.map_path(str(fixture["path"]))
            if fixture.get("directory"):
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(fixture.get("content", "")), encoding="utf-8")

    def rewrite_arguments(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        rewritten = dict(arguments)
        for key in ("path", "cwd"):
            if key in rewritten and rewritten[key] not in {None, ""}:
                rewritten[key] = str(self.map_path(str(rewritten[key])))
        if tool == "write_file":
            rewritten["max_bytes"] = min(
                int(rewritten.get("max_bytes", 2 * 1024 * 1024)),
                2 * 1024 * 1024,
            )
        return rewritten

    def map_path(self, original: str) -> Path:
        normalized = original.replace("\\", "/")
        drive = ""
        if re.match(r"^[A-Za-z]:", normalized):
            drive = f"drive_{normalized[0].lower()}"
            normalized = normalized[2:]
        prefix = "absolute" if normalized.startswith("/") else "relative"
        parts: list[str] = []
        for part in PurePosixPath(normalized).parts:
            if part in {"/", "", "."}:
                continue
            if part == "..":
                part = "_parent_"
            parts.append(re.sub(r"[^A-Za-z0-9._-]+", "_", part)[:120] or "_")
        return self.root.joinpath(*(filter(None, [drive, prefix, *(parts or ["root"])])))

    def ensure_default_fixture(self, tool: str, arguments: dict[str, Any]) -> None:
        if tool == "read_file":
            path = Path(str(arguments["path"]))
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("WLS isolated validation fixture\n", encoding="utf-8")
        elif tool == "list_directory":
            path = Path(str(arguments["path"]))
            path.mkdir(parents=True, exist_ok=True)
            sentinel = path / "wls-fixture.txt"
            if not sentinel.exists():
                sentinel.write_text("fixture\n", encoding="utf-8")

    @staticmethod
    def postconditions(
        tool: str, arguments: dict[str, Any], output: dict[str, Any]
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        if tool == "write_file":
            path = Path(str(arguments["path"]))
            expected = str(arguments.get("content", "")).encode("utf-8")
            actual = path.read_bytes() if path.exists() else b""
            checks.append(
                {
                    "name": "written_content_matches",
                    "passed": actual == expected,
                    "expected_sha256": hashlib.sha256(expected).hexdigest(),
                    "actual_sha256": hashlib.sha256(actual).hexdigest(),
                }
            )
        elif tool == "emit_note":
            path = Path(str(arguments["path"]))
            try:
                note = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                note = None
            checks.append(
                {
                    "name": "note_contract",
                    "passed": bool(
                        isinstance(note, dict)
                        and note.get("title")
                        == str(arguments.get("title", "WLS note"))
                        and note.get("body") == str(arguments.get("body", ""))
                    ),
                }
            )
        elif tool == "read_file":
            checks.append(
                {
                    "name": "read_output_has_size",
                    "passed": isinstance(output.get("size"), int),
                }
            )
        elif tool == "list_directory":
            checks.append(
                {
                    "name": "directory_output_is_list",
                    "passed": isinstance(output.get("items"), list),
                }
            )
        elif tool == "noop":
            checks.append({"name": "noop_ok", "passed": output.get("ok") is True})
        return checks

    @staticmethod
    def summarize_output(output: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key in (
            "path",
            "bytes",
            "size",
            "binary",
            "returncode",
            "ok",
            "status",
            "latency_ms",
            "truncated",
        ):
            if key in output:
                summary[key] = output[key]
        if "items" in output and isinstance(output["items"], list):
            summary["items_count"] = len(output["items"])
        if "text" in output:
            encoded = str(output["text"]).encode("utf-8")
            summary["text_bytes"] = len(encoded)
            summary["text_sha256"] = hashlib.sha256(encoded).hexdigest()
        if "note" in output:
            encoded = json.dumps(
                output["note"], ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
            summary["note_sha256"] = hashlib.sha256(encoded).hexdigest()
        return summary


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)
