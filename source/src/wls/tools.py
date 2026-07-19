from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import hashlib
import json
import mimetypes
import os
import re

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
        self.register(ToolDefinition("inspect_asset", self._inspect_asset, "none"))
        self.register(
            ToolDefinition(
                "inspect_coding_candidate",
                self._inspect_coding_candidate,
                "none",
            )
        )
        self.register(
            ToolDefinition("inspect_git_worktree", self._inspect_git_worktree, "none")
        )
        self.register(
            ToolDefinition(
                "inspect_git_remote_live", self._inspect_git_remote_live, "none"
            )
        )
        self.register(
            ToolDefinition(
                "create_github_pull_request",
                self._create_github_pull_request,
                "external",
            )
        )
        self.register(
            ToolDefinition(
                "inspect_github_pr_status",
                self._inspect_github_pr_status,
                "none",
            )
        )
        self.register(
            ToolDefinition(
                "inspect_github_ci_logs",
                self._inspect_github_ci_logs,
                "none",
            )
        )
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

    def _inspect_asset(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(arguments["path"])).expanduser().resolve(strict=True)
        max_bytes = int(arguments.get("max_bytes", 10 * 1024 * 1024))
        stat = path.stat()
        if stat.st_size > max_bytes:
            raise ValueError("asset exceeds max_bytes")
        data = path.read_bytes()
        mime_type, encoding = mimetypes.guess_type(path.name)
        return {
            "path": str(path),
            "name": path.name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mime_type": mime_type or "application/octet-stream",
            "encoding": encoding,
            "header_hex": data[:64].hex(),
        }

    def _inspect_coding_candidate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from .coding_adapter import CodingTaskContract

        contract = CodingTaskContract(
            task_id=str(arguments["task_id"]),
            base_sha=str(arguments["base_sha"]),
            worktree=Path(str(arguments["path"])).expanduser().resolve(strict=True),
            changed_files=[
                str(item) for item in arguments.get("changed_files", [])
            ],
            tests=[str(item) for item in arguments.get("tests", [])],
            rollback=[str(item) for item in arguments.get("rollback", [])],
        )
        return contract.candidate_artifact()

    def _inspect_git_worktree(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(arguments["path"])).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError("git worktree path must be a directory")
        max_output = max(
            1024,
            min(2 * 1024 * 1024, int(arguments.get("max_output_bytes", 256 * 1024))),
        )
        status = self._run_git_readonly(
            path, ["status", "--short", "--branch"], max_output
        )
        diff_stat = self._run_git_readonly(path, ["diff", "--stat", "--"], max_output)
        diff_names = self._run_git_readonly(
            path, ["diff", "--name-only", "--"], max_output
        )
        remotes = self._run_git_readonly(path, ["remote", "-v"], max_output)
        branches = self._run_git_readonly(path, ["branch", "-vv"], max_output)
        last_commit = self._run_git_readonly(
            path, ["log", "-1", "--pretty=%H%n%s"], max_output
        )
        return {
            "path": str(path),
            "branch_status": status,
            "diff_stat": diff_stat,
            "diff_names": diff_names,
            "remotes": remotes,
            "branches": branches,
            "last_commit": last_commit,
        }

    def _inspect_git_remote_live(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(arguments["path"])).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError("git worktree path must be a directory")
        remote = str(arguments.get("remote", "origin")).strip() or "origin"
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", remote):
            raise ValueError("remote name contains unsupported characters")
        max_output = max(
            1024,
            min(2 * 1024 * 1024, int(arguments.get("max_output_bytes", 256 * 1024))),
        )
        remote_url = self._run_git_readonly(path, ["remote", "get-url", remote], max_output)
        remote_head = self._run_git_readonly(
            path, ["ls-remote", "--symref", remote, "HEAD"], max_output
        )
        remote_heads = self._run_git_readonly(
            path, ["ls-remote", "--heads", remote], max_output
        )
        remote_tags = self._run_git_readonly(
            path, ["ls-remote", "--tags", remote], max_output
        )
        github = self._inspect_public_github_metadata(remote_url.get("stdout", ""))
        return {
            "path": str(path),
            "remote": remote,
            "remote_url": remote_url,
            "remote_head": remote_head,
            "remote_heads": remote_heads,
            "remote_tags": remote_tags,
            "github": github,
            "network_authority": "read_only_remote_inspection",
            "writes_remote": False,
        }

    def _run_git_readonly(
        self, cwd: Path, args: list[str], max_output: int
    ) -> dict[str, Any]:
        command = ["git", *args]
        completed = subprocess.run(  # nosec B603
            command,
            cwd=str(cwd),
            capture_output=True,
            text=False,
            timeout=30,
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

    def _inspect_public_github_metadata(self, remote_url: str) -> dict[str, Any]:
        repo = self._github_repo_from_remote(remote_url.strip())
        if repo is None:
            return {"available": False, "reason": "remote is not a GitHub URL"}
        owner, name = repo
        base = f"https://api.github.com/repos/{owner}/{name}"
        return {
            "available": True,
            "repo": f"{owner}/{name}",
            "repository": self._github_api_get(base),
            "issues": self._github_api_get(f"{base}/issues?state=open&per_page=5"),
            "pulls": self._github_api_get(f"{base}/pulls?state=open&per_page=5"),
            "actions_runs": self._github_api_get(f"{base}/actions/runs?per_page=5"),
        }

    @staticmethod
    def _github_repo_from_remote(remote_url: str) -> tuple[str, str] | None:
        match = re.search(r"github\.com[:/](?P<owner>[^/\s:]+)/(?P<repo>[^/\s]+)", remote_url)
        if not match:
            return None
        repo = match.group("repo")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not repo:
            return None
        return match.group("owner"), repo

    def _github_api_get(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "WLS/1.0",
            },
        )
        try:
            with build_opener(NoRedirect).open(request, timeout=5.0) as response:
                body = response.read(256 * 1024)
                text = body.decode(
                    response.headers.get_content_charset() or "utf-8",
                    errors="replace",
                )
                try:
                    data: Any = json.loads(text)
                except json.JSONDecodeError:
                    data = text
                return {"ok": True, "status": response.status, "url": url, "body": data}
        except Exception as exc:
            return {
                "ok": False,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}"[:1000],
            }

    def _create_github_pull_request(self, arguments: dict[str, Any]) -> dict[str, Any]:
        owner = str(arguments["owner"]).strip()
        repo = str(arguments["repo"]).strip()
        token_env = str(arguments.get("token_env") or "GITHUB_TOKEN").strip()
        token = os.environ.get(token_env)
        if not token:
            raise PermissionError(f"{token_env} is required to create a GitHub PR")
        api_url = str(arguments.get("api_url") or "https://api.github.com").rstrip("/")
        if api_url != "https://api.github.com":
            parsed = urlparse(api_url)
            if parsed.scheme != "https" or parsed.hostname != "api.github.com":
                raise ValueError("api_url must be https://api.github.com")
        body = {
            "title": str(arguments["title"]),
            "body": str(arguments["body"]),
            "base": str(arguments["base"]),
            "head": str(arguments["head"]),
            "draft": bool(arguments.get("draft", True)),
        }
        data = json.dumps(body).encode("utf-8")
        request = Request(
            f"{api_url}/repos/{owner}/{repo}/pulls",
            method="POST",
            data=data,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "WLS/1.0",
            },
        )
        with build_opener(NoRedirect).open(request, timeout=15.0) as response:
            response_body = response.read(256 * 1024)
            text = response_body.decode(
                response.headers.get_content_charset() or "utf-8",
                errors="replace",
            )
            try:
                payload: Any = json.loads(text)
            except json.JSONDecodeError:
                payload = text
        result = payload if isinstance(payload, dict) else {}
        return {
            "status": response.status,
            "owner": owner,
            "repo": repo,
            "number": result.get("number"),
            "url": result.get("html_url") or result.get("url"),
            "api_url": f"{api_url}/repos/{owner}/{repo}/pulls",
            "draft": body["draft"],
        }

    def _inspect_github_pr_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        owner = str(arguments["owner"]).strip()
        repo = str(arguments["repo"]).strip()
        number = int(arguments["number"])
        api_url = str(arguments.get("api_url") or "https://api.github.com").rstrip("/")
        if api_url != "https://api.github.com":
            raise ValueError("api_url must be https://api.github.com")
        token_env = str(arguments.get("token_env") or "").strip()
        token = os.environ.get(token_env) if token_env else None
        base = f"{api_url}/repos/{owner}/{repo}"
        pull_request = self._github_api_get_with_optional_token(
            f"{base}/pulls/{number}", token=token
        )
        pr_body = pull_request.get("body") if pull_request.get("ok") else {}
        if not isinstance(pr_body, dict):
            pr_body = {}
        head = pr_body.get("head") if isinstance(pr_body.get("head"), dict) else {}
        head_sha = str(head.get("sha") or "").strip()
        head_ref = str(head.get("ref") or "").strip()
        statuses: dict[str, Any] = {"ok": False, "reason": "PR head sha unavailable"}
        check_runs: dict[str, Any] = {"ok": False, "reason": "PR head sha unavailable"}
        workflow_runs: dict[str, Any] = {"ok": False, "reason": "PR head ref unavailable"}
        if head_sha:
            statuses = self._github_api_get_with_optional_token(
                f"{base}/commits/{quote(head_sha, safe='')}/status", token=token
            )
            check_runs = self._github_api_get_with_optional_token(
                f"{base}/commits/{quote(head_sha, safe='')}/check-runs?per_page=20",
                token=token,
            )
        if head_ref:
            workflow_runs = self._github_api_get_with_optional_token(
                f"{base}/actions/runs?branch={quote(head_ref, safe='')}&per_page=10",
                token=token,
            )
        return {
            "owner": owner,
            "repo": repo,
            "number": number,
            "url": f"https://github.com/{owner}/{repo}/pull/{number}",
            "pull_request": pull_request,
            "head_sha": head_sha,
            "head_ref": head_ref,
            "statuses": statuses,
            "check_runs": check_runs,
            "workflow_runs": workflow_runs,
            "failure_summary": self._github_pr_failure_summary(
                check_runs=check_runs,
                statuses=statuses,
                workflow_runs=workflow_runs,
            ),
            "network_authority": "read_only_github_pr_status_inspection",
            "writes_remote": False,
        }

    def _github_api_get_with_optional_token(
        self, url: str, *, token: str | None = None
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "WLS/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, method="GET", headers=headers)
        try:
            with build_opener(NoRedirect).open(request, timeout=10.0) as response:
                body = response.read(512 * 1024)
                text = body.decode(
                    response.headers.get_content_charset() or "utf-8",
                    errors="replace",
                )
                try:
                    data: Any = json.loads(text)
                except json.JSONDecodeError:
                    data = text
                return {"ok": True, "status": response.status, "url": url, "body": data}
        except Exception as exc:
            return {
                "ok": False,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}"[:1000],
            }

    def _inspect_github_ci_logs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        owner = str(arguments["owner"]).strip()
        repo = str(arguments["repo"]).strip()
        api_url = str(arguments.get("api_url") or "https://api.github.com").rstrip("/")
        if api_url != "https://api.github.com":
            raise ValueError("api_url must be https://api.github.com")
        token_env = str(arguments.get("token_env") or "").strip()
        token = os.environ.get(token_env) if token_env else None
        max_runs = max(1, min(5, int(arguments.get("max_runs", 3))))
        max_jobs = max(1, min(10, int(arguments.get("max_jobs", 5))))
        max_log_bytes = max(
            1024, min(512 * 1024, int(arguments.get("max_log_bytes", 64 * 1024)))
        )
        workflow_run_ids = [
            int(item)
            for item in arguments.get("workflow_run_ids", [])[:max_runs]
            if str(item).strip().isdigit()
        ]
        base = f"{api_url}/repos/{owner}/{repo}"
        logs: list[dict[str, Any]] = []
        job_count = 0
        for run_id in workflow_run_ids:
            jobs = self._github_api_get_with_optional_token(
                f"{base}/actions/runs/{run_id}/jobs?per_page=20",
                token=token,
            )
            jobs_body = jobs.get("body") if jobs.get("ok") else {}
            if not isinstance(jobs_body, dict):
                logs.append(
                    {
                        "run_id": run_id,
                        "ok": False,
                        "reason": "jobs response was not an object",
                        "jobs_response": jobs,
                    }
                )
                continue
            for job in jobs_body.get("jobs", []) or []:
                if job_count >= max_jobs:
                    break
                if not isinstance(job, dict):
                    continue
                conclusion = str(job.get("conclusion") or "").lower()
                status = str(job.get("status") or "").lower()
                if conclusion not in {
                    "failure",
                    "cancelled",
                    "timed_out",
                    "action_required",
                }:
                    continue
                job_id = job.get("id")
                if job_id is None:
                    continue
                job_count += 1
                log_result = self._github_actions_job_log(
                    f"{base}/actions/jobs/{int(job_id)}/logs",
                    token=token,
                    max_bytes=max_log_bytes,
                )
                excerpt = str(log_result.get("text") or "")
                logs.append(
                    {
                        "run_id": run_id,
                        "job_id": int(job_id),
                        "name": job.get("name"),
                        "status": status,
                        "conclusion": conclusion,
                        "html_url": job.get("html_url"),
                        "log_url": log_result.get("url"),
                        "log_ok": bool(log_result.get("ok")),
                        "log_excerpt": excerpt[:max_log_bytes],
                        "log_truncated": bool(log_result.get("truncated")),
                        "log_error": log_result.get("error"),
                    }
                )
            if job_count >= max_jobs:
                break
        return {
            "owner": owner,
            "repo": repo,
            "workflow_run_ids": workflow_run_ids,
            "logs": logs,
            "log_count": len(logs),
            "failure_clues": self._github_ci_log_failure_clues(logs),
            "network_authority": "read_only_github_ci_log_inspection",
            "writes_remote": False,
        }

    def _github_actions_job_log(
        self, url: str, *, token: str | None, max_bytes: int
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "WLS/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, method="GET", headers=headers)
        try:
            with build_opener().open(request, timeout=15.0) as response:
                body = response.read(max_bytes + 1)
                truncated = len(body) > max_bytes
                data = body[:max_bytes]
                return {
                    "ok": True,
                    "url": response.geturl(),
                    "status": response.status,
                    "text": data.decode("utf-8", errors="replace"),
                    "bytes": len(data),
                    "truncated": truncated,
                }
        except Exception as exc:
            return {
                "ok": False,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}"[:1000],
            }

    @staticmethod
    def _github_ci_log_failure_clues(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clues: list[dict[str, Any]] = []
        patterns = ("error", "failed", "failure", "assert", "traceback", "::")
        for item in logs:
            text = str(item.get("log_excerpt") or "")
            lines: list[str] = []
            for line in text.splitlines():
                lowered = line.lower()
                if any(pattern in lowered for pattern in patterns):
                    lines.append(line.strip()[:240])
                if len(lines) >= 12:
                    break
            if lines:
                clues.append(
                    {
                        "run_id": item.get("run_id"),
                        "job_id": item.get("job_id"),
                        "name": item.get("name"),
                        "lines": lines,
                    }
                )
            if len(clues) >= 10:
                break
        return clues

    @staticmethod
    def _github_pr_failure_summary(
        *,
        check_runs: dict[str, Any],
        statuses: dict[str, Any],
        workflow_runs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        check_body = check_runs.get("body") if check_runs.get("ok") else {}
        if isinstance(check_body, dict):
            for item in check_body.get("check_runs", []) or []:
                if not isinstance(item, dict):
                    continue
                conclusion = str(item.get("conclusion") or "").lower()
                status = str(item.get("status") or "").lower()
                if conclusion in {"failure", "cancelled", "timed_out", "action_required"}:
                    output = item.get("output") if isinstance(item.get("output"), dict) else {}
                    failures.append(
                        {
                            "kind": "check_run",
                            "name": item.get("name"),
                            "status": status,
                            "conclusion": conclusion,
                            "details_url": item.get("details_url"),
                            "title": output.get("title"),
                            "summary": str(output.get("summary") or "")[:1200],
                        }
                    )
        status_body = statuses.get("body") if statuses.get("ok") else {}
        if isinstance(status_body, dict):
            for item in status_body.get("statuses", []) or []:
                if not isinstance(item, dict):
                    continue
                state = str(item.get("state") or "").lower()
                if state not in {"success", "pending"}:
                    failures.append(
                        {
                            "kind": "commit_status",
                            "context": item.get("context"),
                            "state": state,
                            "target_url": item.get("target_url"),
                            "description": item.get("description"),
                        }
                    )
        runs_body = workflow_runs.get("body") if workflow_runs.get("ok") else {}
        if isinstance(runs_body, dict):
            for item in runs_body.get("workflow_runs", []) or []:
                if not isinstance(item, dict):
                    continue
                conclusion = str(item.get("conclusion") or "").lower()
                if conclusion in {"failure", "cancelled", "timed_out", "action_required"}:
                    failure = {
                        "kind": "workflow_run",
                        "name": item.get("name"),
                        "status": item.get("status"),
                        "conclusion": conclusion,
                        "html_url": item.get("html_url"),
                    }
                    if item.get("id") is not None:
                        failure["workflow_run_id"] = item.get("id")
                    failures.append(failure)
        return failures[:20]

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
