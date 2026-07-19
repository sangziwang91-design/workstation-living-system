from __future__ import annotations

from http.client import HTTPConnection
from types import SimpleNamespace
import json
import threading
import time

from wls.server import WLSServer


class ServerLifeStateRuntime:
    def __init__(self, secret_path) -> None:
        self.config = SimpleNamespace(secret_path=secret_path)
        self.feedback_calls = []

    def life_state(self):
        return {
            "schema_version": 1,
            "bounded": True,
            "authority": {
                "source": "canonical life organs",
                "writes_canonical_state": False,
                "creates_evidence_receipt": False,
                "candidate_executes_action": False,
            },
            "next_action_candidate": {"available": False, "reason": "no active goals"},
        }

    def record_owner_outcome_feedback(self, **kwargs):
        self.feedback_calls.append(kwargs)
        return {"recorded": True, **kwargs}

    def start_patch_mission(self, **kwargs):
        return {"status": "STARTED", **kwargs}

    def continue_patch_mission(self, **kwargs):
        return {"status": "STEPPED", **kwargs}

    def review_patch_mission_repair_skill_candidate(self, **kwargs):
        return {
            "status": "REPLAY_PASSED",
            "candidate_only": True,
            "promotion_executed": False,
            **kwargs,
        }

    def sandbox_patch_mission_repair_skill_candidate(self, **kwargs):
        return {
            "status": "SANDBOX_PASSED",
            "candidate_only": True,
            "promotion_executed": False,
            "canonical_repo_write_executed": False,
            **kwargs,
        }

    def validate_patch_mission_repair_skill_candidate(self, **kwargs):
        return {
            "status": "VALIDATED",
            "candidate_only": True,
            "promotion_executed": False,
            "active_skill_created": False,
            **kwargs,
        }

    def propose_patch_mission_repair_skill_from_candidate(self, **kwargs):
        return {
            "status": "SKILL_PROPOSED",
            "skill_status": "PROPOSED",
            "active_skill_created": False,
            "promotion_executed": False,
            **kwargs,
        }

    def start_patch_mission_repair_skill_sandbox(self, **kwargs):
        return {
            "status": "SKILL_SANDBOXED",
            "skill_status_after": "SANDBOXED",
            "active_skill_created": False,
            "promotion_executed": False,
            **kwargs,
        }

    def validate_patch_mission_repair_skill_sandbox(self, **kwargs):
        return {
            "status": "SKILL_VALIDATED",
            "skill_status_after": "VALIDATED",
            "active_skill_created": False,
            "promotion_executed": False,
            **kwargs,
        }

    def approve_patch_mission_repair_skill(self, **kwargs):
        return {
            "status": "SKILL_APPROVED",
            "skill_status_after": "APPROVED",
            "active_skill_matchable": True,
            "promotion_executed": False,
            "command_executed": False,
            **kwargs,
        }

    def approve_patch_mission_repair_skill_promotion_review(self, **kwargs):
        return {
            "status": "PROMOTION_REVIEW_APPROVED",
            "candidate_status_after": "APPROVED",
            "skill_status_after": "APPROVED",
            "promotion_executed": False,
            "command_executed": False,
            **kwargs,
        }

    def promote_patch_mission_repair_skill_from_review(self, **kwargs):
        return {
            "status": "SKILL_PROMOTED",
            "candidate_status_after": "PROMOTED",
            "skill_status_after": "PROMOTED",
            "promotion_executed": True,
            "command_executed": False,
            "repo_write_executed": False,
            **kwargs,
        }


def test_server_life_state_endpoint_returns_bounded_payload(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(
                "GET",
                "/life-state",
                headers={"Authorization": f"Bearer {server.token}"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["schema_version"] == 1
            assert payload["bounded"] is True
            assert payload["authority"]["writes_canonical_state"] is False
            assert payload["next_action_candidate"]["available"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_outcome_feedback_endpoint_records_owner_feedback(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "outcome": "helped",
                    "action_id": "act-1",
                    "owner_note": "useful",
                    "evidence": {"owner_review": "ok"},
                }
            )
            connection.request(
                "POST",
                "/outcome-feedback",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["recorded"] is True
            assert payload["outcome"] == "helped"
            assert runtime.feedback_calls[0]["action_id"] == "act-1"
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_endpoint_starts_local_patch_mission(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "repo_path": str(tmp_path),
                    "mission": "fix local issue",
                    "execute_first_action": False,
                }
            )
            connection.request(
                "POST",
                "/patch-mission",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "STARTED"
            assert payload["repo_path"] == str(tmp_path)
            assert payload["mission"] == "fix local issue"
            assert payload["execute_first_action"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_step_endpoint_advances_patch_mission(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "mission_id": "patch_mission_1",
                    "mode": "from-test-result",
                    "target": "tests/test_demo.py",
                    "action_id": "act_test_1",
                    "execute": True,
                }
            )
            connection.request(
                "POST",
                "/patch-mission/step",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "STEPPED"
            assert payload["mission_id"] == "patch_mission_1"
            assert payload["mode"] == "from-test-result"
            assert payload["target"] == "tests/test_demo.py"
            assert payload["action_id"] == "act_test_1"
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_repair_candidate_review_endpoint(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "candidate_id": "candidate_1",
                    "reason": "owner reviews repair candidate",
                }
            )
            connection.request(
                "POST",
                "/patch-mission/repair-candidate/review",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "REPLAY_PASSED"
            assert payload["candidate_id"] == "candidate_1"
            assert payload["candidate_only"] is True
            assert payload["promotion_executed"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_repair_candidate_sandbox_endpoint(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "candidate_id": "candidate_1",
                    "reason": "owner sandboxes repair candidate",
                    "owner_approved": True,
                }
            )
            connection.request(
                "POST",
                "/patch-mission/repair-candidate/sandbox",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "SANDBOX_PASSED"
            assert payload["candidate_id"] == "candidate_1"
            assert payload["owner_approved"] is True
            assert payload["canonical_repo_write_executed"] is False
            assert payload["promotion_executed"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_repair_candidate_validate_endpoint(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "candidate_id": "candidate_1",
                    "reason": "human validates sandbox evidence",
                    "human_approved": True,
                }
            )
            connection.request(
                "POST",
                "/patch-mission/repair-candidate/validate",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "VALIDATED"
            assert payload["candidate_id"] == "candidate_1"
            assert payload["human_approved"] is True
            assert payload["promotion_executed"] is False
            assert payload["active_skill_created"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_repair_candidate_propose_skill_endpoint(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "candidate_id": "candidate_1",
                    "reason": "owner proposes a repair skill",
                }
            )
            connection.request(
                "POST",
                "/patch-mission/repair-candidate/propose-skill",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "SKILL_PROPOSED"
            assert payload["candidate_id"] == "candidate_1"
            assert payload["skill_status"] == "PROPOSED"
            assert payload["active_skill_created"] is False
            assert payload["promotion_executed"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_repair_skill_sandbox_endpoint(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "skill_id": "skill_1",
                    "reason": "owner starts repair skill sandbox",
                }
            )
            connection.request(
                "POST",
                "/patch-mission/repair-skill/sandbox",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "SKILL_SANDBOXED"
            assert payload["skill_id"] == "skill_1"
            assert payload["skill_status_after"] == "SANDBOXED"
            assert payload["active_skill_created"] is False
            assert payload["promotion_executed"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_repair_skill_validate_endpoint(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "skill_id": "skill_1",
                    "reason": "owner validates repair skill sandbox",
                }
            )
            connection.request(
                "POST",
                "/patch-mission/repair-skill/validate",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "SKILL_VALIDATED"
            assert payload["skill_id"] == "skill_1"
            assert payload["skill_status_after"] == "VALIDATED"
            assert payload["active_skill_created"] is False
            assert payload["promotion_executed"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_repair_skill_approve_endpoint(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "skill_id": "skill_1",
                    "reason": "owner approves validated repair skill",
                    "human_approved": True,
                }
            )
            connection.request(
                "POST",
                "/patch-mission/repair-skill/approve",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "SKILL_APPROVED"
            assert payload["skill_id"] == "skill_1"
            assert payload["human_approved"] is True
            assert payload["skill_status_after"] == "APPROVED"
            assert payload["active_skill_matchable"] is True
            assert payload["promotion_executed"] is False
            assert payload["command_executed"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_repair_skill_promotion_review_approve_endpoint(
    tmp_path,
) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "candidate_id": "candidate_1",
                    "reason": "owner approves promotion review",
                    "human_approved": True,
                }
            )
            connection.request(
                "POST",
                "/patch-mission/repair-skill/promotion-review/approve",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "PROMOTION_REVIEW_APPROVED"
            assert payload["candidate_id"] == "candidate_1"
            assert payload["human_approved"] is True
            assert payload["candidate_status_after"] == "APPROVED"
            assert payload["skill_status_after"] == "APPROVED"
            assert payload["promotion_executed"] is False
            assert payload["command_executed"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_server_patch_mission_repair_skill_promote_endpoint(tmp_path) -> None:
    runtime = ServerLifeStateRuntime(tmp_path / "secrets" / "evidence.key")
    server = WLSServer(runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while server._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._server is not None
        port = server._server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            body = json.dumps(
                {
                    "candidate_id": "candidate_1",
                    "reason": "owner promotes repair skill",
                    "human_approved": True,
                }
            )
            connection.request(
                "POST",
                "/patch-mission/repair-skill/promote",
                body=body,
                headers={
                    "Authorization": f"Bearer {server.token}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["status"] == "SKILL_PROMOTED"
            assert payload["candidate_id"] == "candidate_1"
            assert payload["human_approved"] is True
            assert payload["candidate_status_after"] == "PROMOTED"
            assert payload["skill_status_after"] == "PROMOTED"
            assert payload["promotion_executed"] is True
            assert payload["command_executed"] is False
            assert payload["repo_write_executed"] is False
        finally:
            connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
