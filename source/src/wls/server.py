from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import hmac
import json
import os

from ._version import __version__
from .provider_hub import ProviderHub
from .provider_ui import PROVIDER_HUB_CSS, PROVIDER_HUB_HTML, PROVIDER_HUB_JS
from .runtime import LivingSystem
from .schemas import Event, Goal


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def load_or_create_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise ValueError("server token too short")
        return token
    token = os.urandom(32).hex()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, token.encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


class WLSServer:
    def __init__(
        self,
        runtime: LivingSystem,
        host: str = "127.0.0.1",
        port: int = 8765,
        provider_hub: ProviderHub | None = None,
    ):
        if host not in LOOPBACK_HOSTS:
            raise ValueError("WLS server binds to loopback only")
        self.runtime = runtime
        self.host = host
        self.port = port
        secret_path = Path(runtime.config.secret_path)
        self.token = load_or_create_token(secret_path.with_name("server.token"))
        self.ui_session = os.urandom(32).hex()
        provider_home = Path(getattr(runtime.config, "home_path", secret_path.parent))
        provider_ledger = getattr(runtime, "ledger", None)
        self.providers = provider_hub or ProviderHub(provider_home, provider_ledger)
        self._server: ThreadingHTTPServer | None = None

    def provider_ui_url(self) -> str:
        host = "127.0.0.1" if self.host in {"localhost", "::1"} else self.host
        return f"http://{host}:{self.port}/providers"

    def serve_forever(self) -> None:
        runtime = self.runtime
        token = self.token
        ui_session = self.ui_session
        provider_hub = self.providers

        class Handler(BaseHTTPRequestHandler):
            server_version = f"WLS/{__version__}"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _host_is_loopback(self) -> bool:
                raw = self.headers.get("Host", "")
                host = raw.rsplit(":", 1)[0].strip("[]").lower()
                return host in LOOPBACK_HOSTS

            def _origin_is_local(self) -> bool:
                origin = self.headers.get("Origin")
                if not origin:
                    return True
                return urlparse(origin).hostname in LOOPBACK_HOSTS

            def _authorized(self) -> bool:
                provided = self.headers.get("Authorization", "")
                return self._host_is_loopback() and hmac.compare_digest(
                    provided, f"Bearer {token}"
                )

            def _ui_authorized(self) -> bool:
                provided = self.headers.get("X-WLS-UI-Session", "")
                fetch_site = self.headers.get("Sec-Fetch-Site", "")
                if fetch_site not in {"", "same-origin", "none"}:
                    return False
                return (
                    self._host_is_loopback()
                    and self._origin_is_local()
                    and hmac.compare_digest(provided, ui_session)
                )

            def _headers(self, content_type: str, length: int) -> None:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("X-Frame-Options", "DENY")

            def _json(self, status: int, data: Any) -> None:
                encoded = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self._headers("application/json; charset=utf-8", len(encoded))
                self.end_headers()
                self.wfile.write(encoded)

            def _asset(self, content_type: str, value: str) -> None:
                encoded = value.encode("utf-8")
                self.send_response(200)
                self._headers(content_type, len(encoded))
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
                )
                self.end_headers()
                self.wfile.write(encoded)

            def _require_loopback_host(self) -> bool:
                if self._host_is_loopback():
                    return True
                self._json(421, {"error": "loopback host required"})
                return False

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("invalid content length")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("body must be an object")
                return value

            def _provider_post(self, path: str, body: dict[str, Any]) -> None:
                if {"credential", "api_key", "token"} & body.keys():
                    raise ValueError("provider credentials are accepted only by the secure local CLI")
                provider_id = str(body["provider_id"])
                if path == "/api/providers/configure":
                    result = provider_hub.configure(
                        provider_id,
                        enabled=bool(body.get("enabled", True)),
                        model=body.get("model"),
                        base_url=body.get("base_url"),
                    )
                elif path == "/api/providers/probe":
                    result = provider_hub.probe(provider_id)
                elif path == "/api/providers/select":
                    result = provider_hub.select(provider_id)
                elif path == "/api/providers/remove-credential":
                    result = provider_hub.remove_credential(provider_id)
                else:
                    self._json(404, {"error": "not found"})
                    return
                self._json(200, result)

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/providers":
                    if not self._require_loopback_host():
                        return
                    page = PROVIDER_HUB_HTML.replace("{{SESSION}}", ui_session)
                    self._asset("text/html; charset=utf-8", page)
                    return
                if path == "/assets/provider-hub.css":
                    if not self._require_loopback_host():
                        return
                    self._asset("text/css; charset=utf-8", PROVIDER_HUB_CSS)
                    return
                if path == "/assets/provider-hub.js":
                    if not self._require_loopback_host():
                        return
                    self._asset("application/javascript; charset=utf-8", PROVIDER_HUB_JS)
                    return
                if path == "/api/providers":
                    if not self._ui_authorized():
                        self._json(401, {"error": "provider UI session rejected"})
                        return
                    self._json(200, provider_hub.list())
                    return
                if not self._authorized():
                    self._json(401, {"error": "unauthorized"})
                    return
                if path == "/health":
                    self._json(200, {"ok": True, "version": __version__})
                elif path == "/status":
                    self._json(200, runtime.status())
                elif path == "/world":
                    self._json(200, runtime.world.active_facts(limit=200))
                elif path == "/memories":
                    self._json(200, runtime.memories.recent(limit=100))
                elif path == "/goals":
                    self._json(200, [goal.to_dict() for goal in runtime.goals.active(limit=100)])
                elif path == "/approvals":
                    rows = runtime.db.query_all(
                        "SELECT action_id,plan_id,tool,purpose,risk,status,approval_id FROM actions WHERE status IN ('WAITING_APPROVAL','APPROVED','UNKNOWN_SIDE_EFFECT') ORDER BY rowid DESC"
                    )
                    self._json(200, [dict(row) for row in rows])
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path.startswith("/api/providers/"):
                    if not self._ui_authorized():
                        self._json(401, {"error": "provider UI session rejected"})
                        return
                    try:
                        self._provider_post(path, self._read_json())
                    except Exception as exc:
                        self._json(400, {"error": f"{type(exc).__name__}: {exc}"})
                    return
                if not self._authorized():
                    self._json(401, {"error": "unauthorized"})
                    return
                try:
                    body = self._read_json()
                    if path == "/events":
                        event = Event(
                            event_type=str(body["event_type"]),
                            source=str(body.get("source", "api")),
                            payload=dict(body.get("payload", {})),
                            salience_hint=float(body.get("salience_hint", 0.5)),
                            dedupe_key=body.get("dedupe_key"),
                        )
                        event_id, inserted = runtime.ingest_event(event)
                        self._json(201 if inserted else 200, {"event_id": event_id, "inserted": inserted})
                    elif path == "/goals":
                        goal = Goal(
                            title=str(body["title"]),
                            description=str(body.get("description", "")),
                            priority=float(body.get("priority", 0.5)),
                            success_criteria=[str(item) for item in body.get("success_criteria", [])],
                            source="api",
                            autonomous=False,
                        )
                        self._json(201, {"goal_id": runtime.add_goal(goal)})
                    elif path == "/relationships":
                        relation_id = runtime.relationships.record(
                            subject=str(body["subject"]),
                            relation_type=str(body["relation_type"]),
                            value=body.get("value"),
                            stability=str(body.get("stability", "working")),
                            confidence=float(body.get("confidence", 0.7)),
                            source_ids=[str(item) for item in body.get("source_ids", [])],
                        )
                        self._json(201, {"relation_id": relation_id})
                    elif path == "/skills/transition":
                        from .schemas import CandidateStatus

                        evidence = body.get("evidence", {})
                        if not isinstance(evidence, dict):
                            raise ValueError("evidence must be an object")
                        runtime.skills.transition(
                            str(body["skill_id"]),
                            CandidateStatus(str(body["target"])),
                            evidence,
                            bool(body.get("human_approved", False)),
                        )
                        self._json(200, {"transitioned": True})
                    elif path == "/cycle":
                        self._json(200, runtime.run_cycle())
                    else:
                        self._json(404, {"error": "not found"})
                except Exception as exc:
                    self._json(400, {"error": f"{type(exc).__name__}: {exc}"})

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        server.daemon_threads = True
        self._server = server
        if self.port == 0:
            self.port = int(server.server_address[1])
        try:
            server.serve_forever()
        finally:
            server.server_close()
            self._server = None

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
