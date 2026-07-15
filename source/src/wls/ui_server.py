from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse
import argparse
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import webbrowser

from .config import default_config
from .runtime import LivingSystem
from .ui_projection import OwnerConsoleProductProjection, UIProjection


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}
_RUN_DETAIL = re.compile(r"^/api/runs/([^/]+)$")
_PROJECT_DETAIL = re.compile(r"^/api/projects/([^/]+)$")
_ACTION_APPROVAL = re.compile(r"^/api/actions/([^/]+)/(approve|reject)$")
_ACTION_RESUME = re.compile(r"^/api/actions/([^/]+)/resume$")
_ACTION_RESOLVE = re.compile(r"^/api/actions/([^/]+)/resolve$")
_GARBAGE_AUDIT_DETAIL = re.compile(r"^/api/garbage-audit/([^/]+)$")
LOGGER = logging.getLogger("wls.ui")

_STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


def default_config_path() -> Path:
    configured = os.environ.get("WLS_CONFIG")
    if configured:
        return Path(configured)
    home = os.environ.get("WLS_HOME")
    if home:
        return Path(home) / "config.json"
    return default_config().home_path / "config.json"


class WLSUIServer:
    """Loopback-only owner console over the canonical LivingSystem runtime."""

    def __init__(
        self,
        runtime: LivingSystem,
        host: str = "127.0.0.1",
        port: int = 8766,
    ) -> None:
        if host not in _LOOPBACK_HOSTS:
            raise ValueError("WLS UI binds to IPv4 loopback only")
        if not 0 <= int(port) <= 65535:
            raise ValueError("port must be within [0, 65535]")
        self.runtime = runtime
        self.host = host
        self.port = int(port)
        self.session_token = secrets.token_urlsafe(48)
        self.token = self.session_token
        self.bootstrap_nonce = secrets.token_urlsafe(32)
        self.projection = UIProjection(runtime)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._cycle_lock = threading.Lock()

    @property
    def bound_port(self) -> int:
        if self._server is None:
            return self.port
        return int(self._server.server_address[1])

    @property
    def bootstrap_url(self) -> str:
        return f"http://{self.host}:{self.bound_port}/?bootstrap={self.bootstrap_nonce}"

    def build(self) -> ThreadingHTTPServer:
        projection = self.projection
        product_projection = OwnerConsoleProductProjection()
        session_token = self.session_token
        cycle_lock = self._cycle_lock
        bootstrap_lock = threading.Lock()
        bootstrap_state: dict[str, str | None] = {"nonce": self.bootstrap_nonce}

        class Handler(BaseHTTPRequestHandler):
            server_version = "WLS-UI/1.1"
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _common_headers(self) -> None:
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                )

            def _valid_host_header(self) -> bool:
                host = self.headers.get("Host", "").strip().lower()
                server_address = cast(tuple[str, int], self.server.server_address)
                port = int(server_address[1])
                return host in {f"127.0.0.1:{port}", f"localhost:{port}"}

            def _authorized(self) -> bool:
                provided = self.headers.get("Authorization", "")
                return bool(
                    provided.startswith("Bearer ")
                    and hmac.compare_digest(provided[7:], session_token)
                )

            def _same_origin_write(self) -> bool:
                if self.headers.get("X-WLS-UI") != "1":
                    return False
                fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
                if fetch_site and fetch_site not in {"same-origin", "none"}:
                    return False
                origin = self.headers.get("Origin")
                if not origin:
                    return True
                server_address = cast(tuple[str, int], self.server.server_address)
                port = int(server_address[1])
                return origin.lower() in {
                    f"http://localhost:{port}",
                    f"http://127.0.0.1:{port}",
                }

            def _json(self, status: int, data: Any) -> None:
                encoded = json.dumps(
                    data, ensure_ascii=False, sort_keys=True, default=str
                ).encode("utf-8")
                self.send_response(status)
                self._common_headers()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def _error(self, status: int, exc: Exception | str) -> None:
                if status >= 500:
                    request_id = secrets.token_hex(8)
                    if isinstance(exc, Exception):
                        LOGGER.error(
                            "WLS UI request failed request_id=%s",
                            request_id,
                            exc_info=(type(exc), exc, exc.__traceback__),
                        )
                    data = {
                        "error": "InternalServerError",
                        "message": "The local WLS UI request failed.",
                        "request_id": request_id,
                    }
                else:
                    name = type(exc).__name__ if isinstance(exc, Exception) else "Error"
                    data = {"error": name, "message": str(exc)}
                self._json(status, data)

            def _read_json(self) -> dict[str, Any]:
                raw_length = self.headers.get("Content-Length", "0")
                try:
                    length = int(raw_length)
                except ValueError as exc:
                    raise ValueError("invalid content length") from exc
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("invalid content length")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("body must be a JSON object")
                return value

            def _serve_asset(self, name: str) -> None:
                if "/" in name or "\\" in name or name.startswith("."):
                    self._json(404, {"error": "not found"})
                    return
                package_root = resources.files("wls.ui_static")
                target = package_root.joinpath(name)
                try:
                    data = target.read_bytes()
                except (FileNotFoundError, IsADirectoryError):
                    self._json(404, {"error": "not found"})
                    return
                self.send_response(200)
                self._common_headers()
                self.send_header(
                    "Content-Type",
                    _STATIC_CONTENT_TYPES.get(
                        Path(name).suffix, "application/octet-stream"
                    ),
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _bootstrap_session(self, supplied: str) -> bool:
                with bootstrap_lock:
                    expected = bootstrap_state["nonce"]
                    if expected is None or not hmac.compare_digest(supplied, expected):
                        return False
                    bootstrap_state["nonce"] = None
                self.send_response(302)
                self._common_headers()
                self.send_header("Location", f"/#session={session_token}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return True

            def _preflight_request(self) -> bool:
                if not self._valid_host_header():
                    self._error(400, "invalid Host header")
                    return False
                return True

            def do_GET(self) -> None:
                if not self._preflight_request():
                    return
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)
                if path == "/" and query.get("bootstrap"):
                    if self._bootstrap_session(query["bootstrap"][0]):
                        return
                    self._error(401, "invalid or already-consumed bootstrap nonce")
                    return
                if path == "/":
                    self._serve_asset("index.html")
                    return
                if path.startswith("/assets/"):
                    self._serve_asset(path.removeprefix("/assets/"))
                    return
                if not self._authorized():
                    self._error(401, "open the one-time bootstrap URL from wls ui")
                    return
                try:
                    if path == "/api/bootstrap":
                        self._json(200, projection.bootstrap())
                    elif path == "/api/home":
                        self._json(200, projection.home())
                    elif path == "/api/projects":
                        self._json(200, projection.projects())
                    elif path == "/api/tasks":
                        self._json(
                            200,
                            projection.tasks(
                                project_id=query.get("project_id", [None])[0]
                            ),
                        )
                    elif path == "/api/runs":
                        self._json(200, projection.runs())
                    elif path == "/api/inbox":
                        self._json(200, projection.inbox())
                    elif path == "/api/library":
                        self._json(200, projection.library())
                    elif path == "/api/product":
                        self._json(
                            200, product_projection.project(projection.runtime.status())
                        )
                    elif path == "/api/health":
                        health = (
                            projection.runtime.health_snapshot()
                            if hasattr(projection.runtime, "health_snapshot")
                            else {"ok": True, "projection_only": True}
                        )
                        self._json(
                            200,
                            {
                                **health,
                                "cycle_request_active": cycle_lock.locked(),
                            },
                        )
                    elif path == "/api/garbage-audit":
                        receipts = projection.runtime.garbage_audit_receipts(limit=1)
                        latest = receipts[0] if receipts else None
                        self._json(
                            200,
                            latest
                            or {
                                "status": "NO_AUDIT",
                                "candidate_count": 0,
                                "candidates": [],
                                "cleanup_executed": False,
                                "owner_review_required": False,
                            },
                        )
                    elif match := _GARBAGE_AUDIT_DETAIL.match(path):
                        audit_id = match.group(1)
                        receipts = projection.runtime.garbage_audit_receipts(limit=100)
                        found = [
                            item for item in receipts if item.get("audit_id") == audit_id
                        ]
                        if not found:
                            raise KeyError(audit_id)
                        self._json(200, found[0])
                    elif match := _RUN_DETAIL.match(path):
                        self._json(200, projection.run_detail(match.group(1)))
                    elif match := _PROJECT_DETAIL.match(path):
                        self._json(200, projection.project_detail(match.group(1)))
                    else:
                        self._json(404, {"error": "not found"})
                except KeyError as exc:
                    self._error(404, exc)
                except ValueError as exc:
                    self._error(400, exc)
                except Exception as exc:
                    self._error(500, exc)

            def do_POST(self) -> None:
                if not self._preflight_request():
                    return
                if not self._authorized():
                    self._error(401, "unauthorized")
                    return
                if not self._same_origin_write():
                    self._error(403, "write request failed origin check")
                    return
                path = urlparse(self.path).path
                try:
                    body = self._read_json()
                    if path == "/api/goals":
                        self._json(201, projection.create_goal(body))
                    elif path == "/api/garbage-audit/scan":
                        max_candidates = int(body.get("max_candidates", 500))
                        self._json(
                            200,
                            projection.runtime.garbage_audit(
                                max_candidates=max_candidates,
                                reason=str(
                                    body.get("reason", "owner UI garbage review scan")
                                ),
                            ),
                        )
                    elif path == "/api/cycle":
                        if not cycle_lock.acquire(blocking=False):
                            self._json(409, {"error": "cycle already running"})
                            return
                        try:
                            self._json(200, projection.run_cycle())
                        finally:
                            cycle_lock.release()
                    elif match := _ACTION_APPROVAL.match(path):
                        action_id, decision = match.groups()
                        self._json(
                            200,
                            projection.decide_action(
                                action_id,
                                approved=decision == "approve",
                                reason=str(body.get("reason", "")),
                                minutes=int(body.get("minutes", 30)),
                            ),
                        )
                    elif match := _ACTION_RESUME.match(path):
                        self._json(200, projection.resume_action(match.group(1)))
                    elif match := _ACTION_RESOLVE.match(path):
                        evidence = body.get("evidence", {})
                        if not isinstance(evidence, dict):
                            raise ValueError("evidence must be an object")
                        self._json(
                            200,
                            projection.resolve_unknown_action(
                                match.group(1),
                                resolution=str(body.get("resolution", "")),
                                evidence=evidence,
                            ),
                        )
                    elif path == "/api/garbage-audit/cleanup":
                        approval_reference = str(body.get("approval_reference", "")).strip()
                        reason = str(body.get("reason", "")).strip()
                        if not approval_reference:
                            raise PermissionError("approval_reference is required")
                        if not reason:
                            raise ValueError("reason is required")
                        max_candidates = int(body.get("max_candidates", 500))
                        self._json(
                            200,
                            projection.runtime.garbage_audit(
                                max_candidates=max_candidates,
                                reason=reason,
                                execute_cleanup=True,
                                approval_reference=approval_reference,
                            ),
                        )
                    elif path == "/api/garbage-audit/quarantine-clear":
                        self._json(
                            200,
                            projection.runtime.clear_garbage_quarantine(
                                audit_id=str(body.get("audit_id", "")).strip(),
                                approval_reference=str(
                                    body.get("approval_reference", "")
                                ).strip(),
                                reason=str(body.get("reason", "")).strip(),
                            ),
                        )
                    else:
                        self._json(404, {"error": "not found"})
                except KeyError as exc:
                    self._error(404, exc)
                except PermissionError as exc:
                    self._error(403, exc)
                except (ValueError, json.JSONDecodeError) as exc:
                    self._error(400, exc)
                except Exception as exc:
                    self._error(500, exc)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        return self._server

    def serve_forever(self) -> None:
        server = self._server or self.build()
        try:
            server.serve_forever()
        finally:
            server.server_close()
            self._server = None

    def start_in_thread(self) -> threading.Thread:
        server = self._server or self.build()
        thread = threading.Thread(
            target=server.serve_forever,
            name="wls-ui",
            daemon=True,
        )
        thread.start()
        self._thread = thread
        return thread

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wls-ui",
        description="Loopback-only WLS owner console",
    )
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--host", default="127.0.0.1", choices=sorted(_LOOPBACK_HOSTS))
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--app-mode", action="store_true",
                        help="Open in app/windowed mode (no address bar)")
    return parser


def _open_browser(url: str, *, app_mode: bool = False) -> None:
    if not app_mode:
        webbrowser.open(url)
        return
    edge = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
           "Microsoft" / "Edge" / "Application" / "msedge.exe"
    chrome = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"
    if edge.exists():
        subprocess.Popen(
            [str(edge), f"--app={url}", "--new-window",
             "--window-size=1400,900", f"--user-data-dir={os.environ.get('TEMP', os.environ.get('TMP', '.'))}\\wls-edge-profile"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    elif chrome.exists():
        subprocess.Popen(
            [str(chrome), f"--app={url}", "--new-window", "--window-size=1400,900"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = LivingSystem.from_config_path(Path(args.config))
    server = WLSUIServer(runtime, args.host, args.port)
    server.build()
    print(
        json.dumps(
            {
                "url": server.bootstrap_url,
                "session_scope": "ephemeral-process-and-browser-tab",
                "authority": "canonical LivingSystem",
                "projection_only": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not args.no_browser:
        _open_browser(server.bootstrap_url, app_mode=args.app_mode)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
