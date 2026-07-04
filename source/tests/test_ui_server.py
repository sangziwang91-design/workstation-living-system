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
        status, _, js = request(connection, "GET", "/assets/app.js", token=server.token)
        assert status == 200
        assert b'style="' not in js
        assert b".style." not in js
    finally:
        connection.close()
        server.shutdown()
