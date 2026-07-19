from __future__ import annotations

from http.client import HTTPConnection
import json
import threading

from test_ui_projection import FakeRuntime, add_action
from wls.ui_server import WLSUIServer


def request(connection, method, path, *, token=None, body=None, headers=None):
    actual_headers = dict(headers or {})
    if token:
        actual_headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        actual_headers["Content-Type"] = "application/json"
        actual_headers.setdefault("X-WLS-UI", "1")
        body = json.dumps(body)
    connection.request(method, path, body=body, headers=actual_headers)
    response = connection.getresponse()
    payload = response.read()
    return response.status, dict(response.getheaders()), payload


def make_server(runtime=None):
    runtime = runtime or FakeRuntime()
    server = WLSUIServer(runtime, port=0)
    server.start_in_thread()
    return runtime, server


def test_ui_server_is_ipv4_loopback_only_and_validates_port() -> None:
    runtime = FakeRuntime()
    for host in ("0.0.0.0", "::1", "192.168.1.2"):
        try:
            WLSUIServer(runtime, host=host)
        except ValueError as exc:
            assert "loopback" in str(exc)
        else:
            raise AssertionError(f"host should fail: {host}")
    try:
        WLSUIServer(runtime, port=70000)
    except ValueError as exc:
        assert "port" in str(exc)
    else:
        raise AssertionError("invalid port should fail")


def test_bootstrap_nonce_is_one_time_and_fragment_session_authenticates() -> None:
    _, server = make_server()
    connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        path = f"/?bootstrap={server.bootstrap_nonce}"
        status, headers, _ = request(connection, "GET", path)
        assert status == 302
        location = headers["Location"]
        assert location.startswith("/#session=")
        session = location.split("=", 1)[1]
        assert session == server.session_token
        assert "Set-Cookie" not in headers

        status, _, _ = request(connection, "GET", path)
        assert status == 401
        status, _, payload = request(connection, "GET", "/api/bootstrap", token=session)
        assert status == 200
        assert json.loads(payload)["home"]["integrity_hint"]["projection_only"] is True
    finally:
        connection.close()
        server.shutdown()


def test_product_projection_endpoint_exposes_read_only_owner_panels() -> None:
    _, server = make_server()
    connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        status, _, payload = request(connection, "GET", "/api/product", token=server.token)
        assert status == 200
        product = json.loads(payload)
        assert product["surface"] == "owner_console"
        assert product["mode"] == "READ_ONLY_PROJECTION"
        assert product["writes_canonical_state"] is False
        assert product["direct_tool_execution"] is False
        assert product["delivery_readiness"]["overall_status"] == "NEEDS_EVIDENCE"
        assert product["delivery_readiness"]["writes_canonical_state"] is False
        assert "organs" in product["panel_ids"]
        assert {panel["panel_id"] for panel in product["panels"]} == set(
            product["panel_ids"]
        )
    finally:
        connection.close()
        server.shutdown()


def test_health_endpoint_uses_bounded_runtime_snapshot() -> None:
    runtime = FakeRuntime()
    runtime.health_snapshot = lambda: {  # type: ignore[attr-defined]
        "ok": False,
        "status": "WARN",
        "warnings": ["database_size_over_daemon_limit"],
        "projection_only": False,
    }
    _, server = make_server(runtime)
    connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        status, _, payload = request(
            connection, "GET", "/api/health", token=server.token
        )
        health = json.loads(payload)
        assert status == 200
        assert health["status"] == "WARN"
        assert health["warnings"] == ["database_size_over_daemon_limit"]
        assert health["cycle_request_active"] is False
        assert health["projection_only"] is False
    finally:
        connection.close()
        server.shutdown()


def test_life_state_endpoint_returns_runtime_life_state() -> None:
    runtime = FakeRuntime()
    runtime.life_state = lambda: {  # type: ignore[attr-defined]
        "schema_version": 1,
        "bounded": True,
        "authority": {
            "source": "canonical life organs",
            "writes_canonical_state": False,
            "creates_evidence_receipt": False,
            "candidate_executes_action": False,
        },
        "active_goals": [],
        "latest_meaningful_observations": [],
        "top_memory_influences": [],
        "self_model_confidence": {"overall": 0.0},
        "pending_owner_approvals": [],
        "last_sleep_consolidation": {"evidence_id": None},
        "next_action_candidate": {"available": False, "reason": "no active goals"},
    }
    _, server = make_server(runtime)
    connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        status, _, payload = request(
            connection, "GET", "/api/life-state", token=server.token
        )
        state = json.loads(payload)
        assert status == 200
        assert state["schema_version"] == 1
        assert state["bounded"] is True
        assert state["authority"]["writes_canonical_state"] is False
        assert state["next_action_candidate"]["available"] is False
    finally:
        connection.close()
        server.shutdown()


def test_invalid_host_and_cross_site_write_are_rejected() -> None:
    _, server = make_server()
    connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        status, _, _ = request(
            connection,
            "GET",
            "/api/bootstrap",
            token=server.token,
            headers={"Host": "evil.example"},
        )
        assert status == 400

        status, _, _ = request(
            connection,
            "POST",
            "/api/goals",
            token=server.token,
            body={"kind": "project", "title": "WLS"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert status == 403
    finally:
        connection.close()
        server.shutdown()


def test_ui_server_creates_canonical_project_and_task() -> None:
    runtime, server = make_server()
    connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        status, _, payload = request(
            connection,
            "POST",
            "/api/goals",
            token=server.token,
            body={"kind": "project", "title": "WLS"},
        )
        assert status == 201
        project_id = json.loads(payload)["goal_id"]

        status, _, payload = request(
            connection,
            "POST",
            "/api/goals",
            token=server.token,
            body={"kind": "task", "project_id": project_id, "title": "UI"},
        )
        assert status == 201
        assert json.loads(payload)["kind"] == "task"
        assert runtime.db.query_one("SELECT COUNT(*) AS n FROM goals")["n"] == 2
    finally:
        connection.close()
        server.shutdown()


def test_action_endpoints_follow_state_specific_operations() -> None:
    runtime = FakeRuntime()
    add_action(runtime, action_id="wait", status="WAITING_APPROVAL")
    add_action(runtime, action_id="approved", status="APPROVED", cycle_id="cycle-2")
    add_action(runtime, action_id="unknown", status="UNKNOWN_SIDE_EFFECT", cycle_id="cycle-3")
    _, server = make_server(runtime)
    connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        status, _, _ = request(
            connection,
            "POST",
            "/api/actions/wait/approve",
            token=server.token,
            body={"reason": "owner"},
        )
        assert status == 200

        status, _, payload = request(
            connection,
            "POST",
            "/api/actions/approved/resume",
            token=server.token,
            body={},
        )
        assert status == 200
        assert json.loads(payload)["status"] == "SUCCEEDED"

        status, _, payload = request(
            connection,
            "POST",
            "/api/actions/unknown/resolve",
            token=server.token,
            body={"resolution": "FAILED", "evidence": {"owner_note": "verified"}},
        )
        assert status == 200
        assert json.loads(payload)["resolved"] is True
    finally:
        connection.close()
        server.shutdown()


def test_garbage_review_endpoints_require_owner_write_gate() -> None:
    runtime = FakeRuntime()
    _, server = make_server(runtime)
    connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        status, _, payload = request(
            connection, "GET", "/api/garbage-audit", token=server.token
        )
        assert status == 200
        assert json.loads(payload)["status"] == "NO_AUDIT"
        assert runtime.garbage_receipts == []

        status, _, payload = request(
            connection,
            "POST",
            "/api/garbage-audit/scan",
            token=server.token,
            body={"reason": "owner scan"},
        )
        audit = json.loads(payload)
        assert status == 200
        assert audit["status"] == "OWNER_REVIEW_REQUIRED"
        assert audit["cleanup_executed"] is False
        assert len(runtime.garbage_receipts) == 1

        status, _, _ = request(
            connection,
            "POST",
            "/api/garbage-audit/cleanup",
            token=server.token,
            body={"reason": "owner cleanup"},
        )
        assert status == 403

        status, _, payload = request(
            connection,
            "POST",
            "/api/garbage-audit/cleanup",
            token=server.token,
            body={
                "reason": "owner cleanup",
                "approval_reference": "pytest://owner-approval",
            },
        )
        cleanup = json.loads(payload)
        assert status == 200
        assert cleanup["cleanup_executed"] is True
        assert cleanup["approval_reference"] == "pytest://owner-approval"

        status, _, payload = request(
            connection,
            "GET",
            f"/api/garbage-audit/{cleanup['audit_id']}",
            token=server.token,
        )
        assert status == 200
        assert json.loads(payload)["audit_id"] == cleanup["audit_id"]

        status, _, payload = request(
            connection,
            "POST",
            "/api/garbage-audit/quarantine-clear",
            token=server.token,
            body={
                "audit_id": cleanup["audit_id"],
                "approval_reference": "pytest://owner-clear",
                "reason": "owner clear",
            },
        )
        clear = json.loads(payload)
        assert status == 200
        assert clear["status"] == "QUARANTINE_CLEARED"
        assert clear["audit_id"] == cleanup["audit_id"]
    finally:
        connection.close()
        server.shutdown()


def test_cycle_endpoint_rejects_concurrent_cycle() -> None:
    runtime = FakeRuntime()
    entered = threading.Event()
    release = threading.Event()

    def slow_cycle():
        entered.set()
        release.wait(timeout=5)
        return {"status": "COMPLETED"}

    runtime.run_cycle = slow_cycle
    _, server = make_server(runtime)
    first_result = {}

    def first_request():
        connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=7)
        try:
            first_result["value"] = request(
                connection, "POST", "/api/cycle", token=server.token, body={}
            )[0]
        finally:
            connection.close()

    thread = threading.Thread(target=first_request)
    thread.start()
    assert entered.wait(timeout=3)
    second = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        status, _, _ = request(second, "POST", "/api/cycle", token=server.token, body={})
        assert status == 409
    finally:
        second.close()
        release.set()
        thread.join(timeout=5)
        server.shutdown()
    assert first_result["value"] == 200


def test_static_ui_respects_strict_csp_without_inline_style() -> None:
    _, server = make_server()
    connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=5)
    try:
        status, headers, payload = request(connection, "GET", "/", token=server.token)
        assert status == 200
        assert "style-src 'self'" in headers["Content-Security-Policy"]
        assert b"style=" not in payload
        assert b'data-view="panels"' in payload
        assert b'data-view="review"' in payload
        status, _, js = request(connection, "GET", "/assets/app.js", token=server.token)
        assert status == 200
        assert b"/api/product" in js
        assert b"/api/garbage-audit" in js
        assert b"/api/garbage-audit/scan" in js
        assert b"Delivery Readiness" in js
        assert b"OPERATIONAL_PREFLIGHT_PASSED" not in js
        assert b"readinessCards" in js
        assert b'style="' not in js
        assert b".style." not in js
    finally:
        connection.close()
        server.shutdown()
