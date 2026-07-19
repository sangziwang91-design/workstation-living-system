from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import hmac
import json
import os

from ._version import __version__
from .runtime import LivingSystem
from .schemas import Event, Goal


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
        self, runtime: LivingSystem, host: str = "127.0.0.1", port: int = 8765
    ):
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("WLS server binds to loopback only")
        self.runtime = runtime
        self.host = host
        self.port = port
        self.token = load_or_create_token(
            runtime.config.secret_path.with_name("server.token")
        )
        self._server: ThreadingHTTPServer | None = None

    def serve_forever(self) -> None:
        runtime = self.runtime
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            server_version = f"WLS/{__version__}"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _authorized(self) -> bool:
                provided = self.headers.get("Authorization", "")
                expected = f"Bearer {token}"
                return hmac.compare_digest(provided, expected)

            def _json(self, status: int, data: Any) -> None:
                encoded = json.dumps(data, ensure_ascii=False, sort_keys=True).encode(
                    "utf-8"
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("invalid content length")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("body must be an object")
                return value

            def do_GET(self) -> None:
                if not self._authorized():
                    self._json(401, {"error": "unauthorized"})
                    return
                path = urlparse(self.path).path
                if path == "/health":
                    self._json(200, {"ok": True, "version": __version__})
                elif path == "/status":
                    self._json(200, runtime.status())
                elif path == "/life-state":
                    self._json(200, runtime.life_state())
                elif path == "/world":
                    self._json(200, runtime.world.active_facts(limit=200))
                elif path == "/memories":
                    self._json(200, runtime.memories.recent(limit=100))
                elif path == "/goals":
                    self._json(
                        200,
                        [goal.to_dict() for goal in runtime.goals.active(limit=100)],
                    )
                elif path == "/approvals":
                    rows = runtime.db.query_all(
                        "SELECT action_id,plan_id,tool,purpose,risk,status,approval_id FROM actions WHERE status IN ('WAITING_APPROVAL','APPROVED','UNKNOWN_SIDE_EFFECT') ORDER BY rowid DESC"
                    )
                    self._json(200, [dict(row) for row in rows])
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self) -> None:
                if not self._authorized():
                    self._json(401, {"error": "unauthorized"})
                    return
                path = urlparse(self.path).path
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
                        self._json(
                            201 if inserted else 200,
                            {"event_id": event_id, "inserted": inserted},
                        )
                    elif path == "/goals":
                        goal = Goal(
                            title=str(body["title"]),
                            description=str(body.get("description", "")),
                            priority=float(body.get("priority", 0.5)),
                            success_criteria=[
                                str(item) for item in body.get("success_criteria", [])
                            ],
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
                            source_ids=[
                                str(item) for item in body.get("source_ids", [])
                            ],
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
                    elif path == "/patch-mission":
                        self._json(
                            200,
                            runtime.start_patch_mission(
                                repo_path=str(body["repo_path"]),
                                mission=str(body["mission"]),
                                execute_first_action=bool(
                                    body.get("execute_first_action", True)
                                ),
                            ),
                        )
                    elif path == "/patch-mission/step":
                        self._json(
                            200,
                            runtime.continue_patch_mission(
                                mission_id=body.get("mission_id"),
                                mode=str(body.get("mode", "auto")),
                                target=body.get("target"),
                                draft=body.get("draft"),
                                action_id=body.get("action_id"),
                                execute=bool(body.get("execute", True)),
                            ),
                        )
                    elif path == "/patch-mission/repair-candidate/review":
                        self._json(
                            200,
                            runtime.review_patch_mission_repair_skill_candidate(
                                candidate_id=str(body["candidate_id"]),
                                reason=str(
                                    body.get(
                                        "reason",
                                        "owner requested Patch Mission repair candidate replay",
                                    )
                                ),
                            ),
                        )
                    elif path == "/patch-mission/repair-candidate/sandbox":
                        self._json(
                            200,
                            runtime.sandbox_patch_mission_repair_skill_candidate(
                                candidate_id=str(body["candidate_id"]),
                                reason=str(
                                    body.get(
                                        "reason",
                                        "owner requested Patch Mission repair candidate sandbox",
                                    )
                                ),
                                owner_approved=bool(body.get("owner_approved", False)),
                            ),
                        )
                    elif path == "/patch-mission/repair-candidate/validate":
                        self._json(
                            200,
                            runtime.validate_patch_mission_repair_skill_candidate(
                                candidate_id=str(body["candidate_id"]),
                                reason=str(
                                    body.get(
                                        "reason",
                                        "owner validates Patch Mission repair candidate sandbox",
                                    )
                                ),
                                human_approved=bool(body.get("human_approved", False)),
                            ),
                        )
                    elif path == "/patch-mission/repair-candidate/propose-skill":
                        self._json(
                            200,
                            runtime.propose_patch_mission_repair_skill_from_candidate(
                                candidate_id=str(body["candidate_id"]),
                                reason=str(
                                    body.get(
                                        "reason",
                                        "owner requests Patch Mission repair skill proposal",
                                    )
                                ),
                            ),
                        )
                    elif path == "/patch-mission/repair-skill/sandbox":
                        self._json(
                            200,
                            runtime.start_patch_mission_repair_skill_sandbox(
                                skill_id=str(body["skill_id"]),
                                reason=str(
                                    body.get(
                                        "reason",
                                        "owner starts Patch Mission repair skill sandbox",
                                    )
                                ),
                            ),
                        )
                    elif path == "/patch-mission/repair-skill/validate":
                        self._json(
                            200,
                            runtime.validate_patch_mission_repair_skill_sandbox(
                                skill_id=str(body["skill_id"]),
                                reason=str(
                                    body.get(
                                        "reason",
                                        "owner validates Patch Mission repair skill sandbox",
                                    )
                                ),
                            ),
                        )
                    elif path == "/patch-mission/repair-skill/approve":
                        self._json(
                            200,
                            runtime.approve_patch_mission_repair_skill(
                                skill_id=str(body["skill_id"]),
                                reason=str(
                                    body.get(
                                        "reason",
                                        "owner approves validated Patch Mission repair skill",
                                    )
                                ),
                                human_approved=bool(body.get("human_approved", False)),
                            ),
                        )
                    elif path == "/patch-mission/repair-skill/promotion-review/approve":
                        self._json(
                            200,
                            runtime.approve_patch_mission_repair_skill_promotion_review(
                                candidate_id=str(body["candidate_id"]),
                                reason=str(
                                    body.get(
                                        "reason",
                                        "owner approves Patch Mission repair skill promotion review",
                                    )
                                ),
                                human_approved=bool(body.get("human_approved", False)),
                            ),
                        )
                    elif path == "/patch-mission/repair-skill/promote":
                        self._json(
                            200,
                            runtime.promote_patch_mission_repair_skill_from_review(
                                candidate_id=str(body["candidate_id"]),
                                reason=str(
                                    body.get(
                                        "reason",
                                        "owner promotes Patch Mission repair skill",
                                    )
                                ),
                                human_approved=bool(body.get("human_approved", False)),
                            ),
                        )
                    elif path == "/outcome-feedback":
                        evidence = body.get("evidence", {})
                        if not isinstance(evidence, dict):
                            raise ValueError("evidence must be an object")
                        self._json(
                            200,
                            runtime.record_owner_outcome_feedback(
                                action_id=body.get("action_id"),
                                outcome=str(body["outcome"]),
                                owner_note=str(body.get("owner_note", "")),
                                evidence=evidence,
                                goal_progress_delta=float(
                                    body.get("goal_progress_delta", 0.0)
                                ),
                            ),
                        )
                    else:
                        self._json(404, {"error": "not found"})
                except Exception as exc:
                    self._json(400, {"error": f"{type(exc).__name__}: {exc}"})

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        server.daemon_threads = True
        self._server = server
        try:
            server.serve_forever()
        finally:
            server.server_close()
            self._server = None

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
