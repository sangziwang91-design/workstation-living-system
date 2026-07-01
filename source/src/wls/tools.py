from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json
import os

# Command execution is allowlisted and always uses shell=False.
import subprocess  # nosec B404
import time

from .policy import PolicyEngine
from .schemas import ActionResult, ActionSpec, utc_now


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect blocked", headers, fp)


@dataclass(slots=True)
class ToolDefinition:
    name: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    side_effect_class: str = "none"  # none, reversible, external, irreversible


class ToolRegistry:
    def __init__(self, policy: PolicyEngine):
        self.policy = policy
        self._tools: dict[str, ToolDefinition] = {}
        self.register_defaults()

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def execute(self, action: ActionSpec) -> ActionResult:
        started = utc_now()
        try:
            self.policy.validate_arguments(action)
            definition = self.get(action.tool)
            output = definition.handler(action.arguments)
            return ActionResult(
                action_id=action.action_id,
                success=True,
                output=output,
                started_at=started,
                observed_side_effect=definition.side_effect_class != "none",
            )
        except Exception as exc:
            return ActionResult(
                action_id=action.action_id,
                success=False,
                output={},
                started_at=started,
                error=f"{type(exc).__name__}: {exc}"[:4000],
                observed_side_effect=(
                    self._tools[action.tool].side_effect_class
                    in {"external", "irreversible"}
                    if action.tool in self._tools
                    else False
                ),
            )

    def register_defaults(self) -> None:
        self.register(ToolDefinition("read_file", self._read_file, "none"))
        self.register(ToolDefinition("list_directory", self._list_directory, "none"))
        self.register(ToolDefinition("write_file", self._write_file, "reversible"))
        self.register(ToolDefinition("http_get", self._http_get, "none"))
        self.register(ToolDefinition("run_command", self._run_command, "external"))
        self.register(ToolDefinition("emit_note", self._emit_note, "reversible"))
        self.register(
            ToolDefinition("noop", lambda args: {"ok": True, "arguments": args}, "none")
        )

    def _read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(arguments["path"])).expanduser().resolve(strict=True)
        max_bytes = int(arguments.get("max_bytes", 2 * 1024 * 1024))
        if path.stat().st_size > max_bytes:
            raise ValueError("file exceeds max_bytes")
        data = path.read_bytes()
        try:
            text = data.decode(str(arguments.get("encoding", "utf-8")))
            return {"path": str(path), "text": text, "size": len(data)}
        except UnicodeDecodeError:
            return {
                "path": str(path),
                "hex": data[:4096].hex(),
                "size": len(data),
                "binary": True,
            }

    def _list_directory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(arguments["path"])).expanduser().resolve(strict=True)
        limit = max(1, min(1000, int(arguments.get("limit", 200))))
        items = []
        for entry in sorted(path.iterdir(), key=lambda p: p.name.lower())[:limit]:
            try:
                stat = entry.stat()
                items.append(
                    {
                        "name": entry.name,
                        "is_dir": entry.is_dir(),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                )
            except OSError:
                continue
        return {"path": str(path), "items": items, "truncated": len(items) >= limit}

    def _write_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(arguments["path"])).expanduser().resolve(strict=False)
        content = str(arguments.get("content", ""))
        max_bytes = int(arguments.get("max_bytes", 2 * 1024 * 1024))
        encoded = content.encode("utf-8")
        if len(encoded) > max_bytes:
            raise ValueError("content exceeds max_bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".wls-{os.getpid()}.tmp")
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return {"path": str(path), "bytes": len(encoded)}

    def _http_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments["url"])
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid URL")
        # Policy validates an explicit host field to prevent ambiguity.
        if str(arguments.get("host", "")).lower() != parsed.hostname.lower():
            raise ValueError("host argument must match URL hostname")
        timeout = max(0.2, min(30.0, float(arguments.get("timeout", 5.0))))
        max_bytes = max(
            1, min(5 * 1024 * 1024, int(arguments.get("max_bytes", 1024 * 1024)))
        )
        request = Request(url, method="GET", headers={"User-Agent": "WLS/1.0"})
        opener = build_opener(NoRedirect)
        started = time.monotonic()
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError("response exceeds max_bytes")
            text = body.decode(
                response.headers.get_content_charset() or "utf-8", errors="replace"
            )
            try:
                data: Any = json.loads(text)
            except json.JSONDecodeError:
                data = text
            return {
                "url": url,
                "status": response.status,
                "body": data,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
            }

    def _run_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = list(arguments["command"])
        cwd = arguments.get("cwd")
        timeout = max(0.2, min(300.0, float(arguments.get("timeout", 30.0))))
        max_output = max(
            1024,
            min(2 * 1024 * 1024, int(arguments.get("max_output_bytes", 256 * 1024))),
        )
        # Policy enforces an executable allowlist and shell=False.
        completed = subprocess.run(  # nosec B603
            command,
            cwd=str(Path(cwd).expanduser().resolve()) if cwd else None,
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
            shell=False,
            env=self._safe_env(),
        )
        stdout = completed.stdout[:max_output].decode("utf-8", errors="replace")
        stderr = completed.stderr[:max_output].decode("utf-8", errors="replace")
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": len(completed.stdout) > max_output,
            "stderr_truncated": len(completed.stderr) > max_output,
        }

    def _emit_note(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(arguments["path"])).expanduser().resolve(strict=False)
        note = {
            "title": str(arguments.get("title", "WLS note")),
            "body": str(arguments.get("body", "")),
            "created_at": utc_now(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(note, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, path)
        return {"path": str(path), "note": note}

    @staticmethod
    def _safe_env() -> dict[str, str]:
        allowed = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "PYTHONPATH",
        }
        return {
            key: value for key, value in os.environ.items() if key.upper() in allowed
        }
