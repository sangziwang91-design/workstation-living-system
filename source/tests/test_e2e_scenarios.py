from __future__ import annotations

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import json
import subprocess
import time
import urllib.error
import urllib.request

import pytest

from wls.config import SensorConfig
from wls.planner import PlanningProvider
from wls.runtime import LivingSystem
from wls.schemas import (
    ActionSpec,
    CandidateStatus,
    Event,
    Goal,
    MemoryItem,
    Observation,
    Plan,
    RiskLevel,
    SkillDefinition,
)
from wls.server import WLSServer


class FixedProvider(PlanningProvider):
    def __init__(self, payload):
        self.payload = payload

    def create_plan(self, context):
        return self.payload(context) if callable(self.payload) else self.payload


def test_01_boot_identity_and_integrity(runtime_factory):
    runtime = runtime_factory()
    status = runtime.status()
    assert status["version"] == "0.1.0a1"
    assert status["self_model"]["identity"]["value"]["subjective_consciousness"] == "UNKNOWN"
    assert runtime.verify_integrity()["ok"] is True


def test_02_filesystem_observe_create_modify_delete(runtime_factory, tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()
    sensor = SensorConfig(
        sensor_type="filesystem",
        name="files",
        interval_seconds=0.01,
        settings={"roots": [str(watched)], "recursive": True, "max_files": 100},
    )
    runtime = runtime_factory(sensors=[sensor])
    target = watched / "note.txt"
    target.write_text("one", encoding="utf-8")
    result1 = runtime.run_cycle()
    assert result1["selected_events"] >= 1
    target.write_text("two", encoding="utf-8")
    time.sleep(0.02)
    result2 = runtime.run_cycle()
    assert result2["status"] == "SUCCEEDED"
    target.unlink()
    time.sleep(0.02)
    runtime.run_cycle()
    facts = runtime.world.query(str(target), 20)
    assert any(fact["value"] == "deleted" for fact in facts)


def test_03_inbox_durable_exactly_once_ack(runtime_factory, tmp_path):
    sensor = SensorConfig(sensor_type="inbox", name="inbox", interval_seconds=0.01, settings={})
    runtime = runtime_factory(sensors=[sensor])
    inbox = runtime.config.inbox_path
    payload = {"subject": "user", "predicate": "request", "value": {"action": "inspect_path", "path": str(tmp_path)}}
    (inbox / "request.json").write_text(json.dumps(payload), encoding="utf-8")
    time.sleep(0.02)
    runtime.run_cycle()
    assert (inbox / "processed" / "request.json").exists()
    count1 = runtime.db.query_one("SELECT COUNT(*) AS n FROM observations WHERE source='inbox'")["n"]
    time.sleep(0.02)
    runtime.run_cycle()
    count2 = runtime.db.query_one("SELECT COUNT(*) AS n FROM observations WHERE source='inbox'")["n"]
    assert count1 == count2 == 1


def test_04_attention_overflow_does_not_lose_events(runtime_factory):
    runtime = runtime_factory(workspace_capacity=2, max_events_per_cycle=10, max_actions_per_cycle=0)
    for index in range(10):
        runtime.ingest_event(Event(event_type="external.signal", source="test", payload={"index": index}, salience_hint=1.0))
    result = runtime.run_cycle()
    counts = runtime.events.counts()
    assert 0 < result["selected_events"] < 10
    assert counts.get("PENDING", 0) > 0
    assert counts.get("PROCESSED", 0) == result["selected_events"]


def test_05_exact_approval_resume_executes_once(runtime_factory):
    runtime = runtime_factory(read_only=True)
    target = runtime.config.sandbox_path / "approved.txt"
    runtime.planner.provider = FixedProvider(
        {
            "rationale": "test write",
            "actions": [{
                "tool": "write_file",
                "arguments": {"path": str(target), "content": "approved"},
                "purpose": "write approved sandbox file",
                "expected_result": "file written",
                "risk": "REVERSIBLE_WRITE",
                "goal_id": None,
                "skill_id": None,
                "acceptance": ["output contains path"],
            }],
            "memory_ids": [], "world_fact_ids": [], "unknowns": [],
        }
    )
    runtime.ingest_event(Event(event_type="user.task", source="test", payload={"task": "write"}, salience_hint=1.0))
    runtime.run_cycle()
    row = runtime.db.query_one("SELECT action_id,status FROM actions ORDER BY rowid DESC LIMIT 1")
    assert row["status"] == "WAITING_APPROVAL"
    approval_id = runtime.approvals.issue(row["action_id"], True, reason="test")
    result = runtime.resume_action(row["action_id"])
    assert result["success"] is True
    assert target.read_text(encoding="utf-8") == "approved"
    with pytest.raises(ValueError):
        runtime.resume_action(row["action_id"])
    consumed = runtime.db.query_one("SELECT consumed_at FROM approvals WHERE approval_id=?", (approval_id,))
    assert consumed["consumed_at"] is not None


def test_06_approval_digest_rejects_tampered_action(runtime_factory):
    runtime = runtime_factory(read_only=True)
    target = runtime.config.sandbox_path / "original.txt"
    plan = Plan(
        rationale="tamper test",
        actions=[ActionSpec(
            tool="write_file", arguments={"path": str(target), "content": "one"},
            purpose="original", expected_result="written", risk=RiskLevel.REVERSIBLE_WRITE,
            acceptance=["output contains path"],
        )],
    )
    runtime._persist_plan_and_ack_events("cycle_manual", plan, [])
    action = plan.actions[0]
    runtime._execute_action(action)
    runtime.approvals.issue(action.action_id, True)
    runtime.db.execute(
        "UPDATE actions SET arguments_json=? WHERE action_id=?",
        (json.dumps({"path": str(target), "content": "tampered"}), action.action_id),
    )
    result = runtime.resume_action(action.action_id)
    assert result["success"] is False
    assert not target.exists()


def test_07_kill_switch_survives_restart(runtime_factory, tmp_path):
    home = tmp_path / "persistent"
    runtime = runtime_factory(home=home)
    runtime.kill("emergency")
    restarted = LivingSystem(runtime.config)
    assert restarted.run_cycle()["status"] == "KILLED"
    restarted.reset_kill("human evidence")
    assert restarted.run_cycle()["status"] == "SUCCEEDED"


def test_08_crash_recovery_never_replays_unknown_side_effect(runtime_factory):
    runtime = runtime_factory()
    plan = Plan(
        rationale="external command",
        actions=[ActionSpec(
            tool="run_command", arguments={"command": ["python", "-c", "print(1)"]},
            purpose="external", expected_result="done", risk=RiskLevel.HIGH,
        )],
    )
    runtime._persist_plan_and_ack_events("cycle_crash", plan, [])
    runtime.db.execute(
        "UPDATE actions SET status='RUNNING',started_at=? WHERE action_id=?",
        (datetime.now(UTC).isoformat(), plan.actions[0].action_id),
    )
    restarted = LivingSystem(runtime.config)
    row = restarted.db.query_one("SELECT status FROM actions WHERE action_id=?", (plan.actions[0].action_id,))
    assert row["status"] == "UNKNOWN_SIDE_EFFECT"


def test_09_world_contradiction_creates_bounded_autonomous_goal(runtime_factory):
    runtime = runtime_factory(max_actions_per_cycle=0)
    runtime.world.assimilate(Observation(source="a", kind="state", subject="service:x", predicate="healthy", value=True))
    runtime.world.assimilate(Observation(source="b", kind="state", subject="service:x", predicate="healthy", value=False))
    runtime.run_cycle()
    goals = runtime.goals.active()
    assert any(goal.autonomous and goal.title.startswith("Clarify contradiction") for goal in goals)


def test_10_prediction_error_becomes_revision_candidate(runtime_factory):
    runtime = runtime_factory()
    for _ in range(2):
        runtime.world.add_prediction("service:y", "healthy", True, 0.9, "test")
        runtime.world.assimilate(Observation(source="probe", kind="health", subject="service:y", predicate="healthy", value=False))
    candidates = runtime.learning.create_prediction_error_candidates()
    assert len(candidates) == 1
    row = runtime.db.query_one("SELECT candidate_type,status FROM evolution_candidates WHERE candidate_id=?", (candidates[0],))
    assert row["candidate_type"] == "world_model_revision"
    assert row["status"] == "PROPOSED"


def test_11_promoted_skill_changes_future_behavior(runtime_factory):
    runtime = runtime_factory()
    skill = SkillDefinition(
        name="respond_alpha",
        description="Perform a learned no-op when alpha appears.",
        trigger_terms=["alpha"],
        steps=[{"tool": "noop", "arguments": {"learned": True}, "purpose": "apply learned alpha response", "acceptance": ["output ok is true"]}],
        source_episode_ids=["episode_1", "episode_2", "episode_3"],
    )
    runtime.skills.add(skill)
    runtime.skills.transition(skill.skill_id, CandidateStatus.SANDBOXED, {"tests": 1})
    runtime.skills.transition(skill.skill_id, CandidateStatus.VALIDATED, {"passed": True})
    runtime.skills.transition(skill.skill_id, CandidateStatus.APPROVED, {"human": True}, human_approved=True)
    runtime.skills.transition(skill.skill_id, CandidateStatus.PROMOTED, {"release": "test"}, human_approved=True)
    runtime.ingest_event(Event(event_type="external.alpha", source="test", payload={"message": "alpha"}, salience_hint=1.0))
    runtime.run_cycle()
    row = runtime.db.query_one("SELECT use_count,success_rate FROM skills WHERE skill_id=?", (skill.skill_id,))
    assert row["use_count"] == 1
    assert row["success_rate"] == 1.0


def test_12_self_model_calibrates_success_and_failure(runtime_factory):
    runtime = runtime_factory()
    good = Plan(rationale="good", actions=[ActionSpec(tool="noop", arguments={}, purpose="good", expected_result="ok", acceptance=["output ok is true"])])
    runtime._persist_plan_and_ack_events("c1", good, [])
    assert runtime._execute_plan(good)[0]["success"] is True
    missing = runtime.config.home_path / "missing.txt"
    bad = Plan(rationale="bad", actions=[ActionSpec(tool="read_file", arguments={"path": str(missing)}, purpose="bad", expected_result="read", acceptance=["output contains path"])])
    runtime._persist_plan_and_ack_events("c2", bad, [])
    assert runtime._execute_plan(bad)[0]["success"] is False
    model = runtime.self_model.snapshot()
    assert model["capability.tool.noop"]["value"]["observed_success_rate"] == 1.0
    assert model["capability.tool.read_file"]["value"]["observed_success_rate"] == 0.0


def test_13_sleep_consolidates_repeated_experience(runtime_factory):
    runtime = runtime_factory()
    workspace = [{
        "item_type": "event",
        "reference_id": "evt",
        "summary": "same",
        "salience": 1.0,
        "reasons": [],
        "payload": {"payload": {"observation": {"kind": "health", "subject": "svc", "predicate": "healthy", "value": False}}},
    }]
    for index in range(3):
        runtime.learning.record_episode(f"cycle_{index}", [f"evt_{index}"], None, [], [], workspace)
    result = runtime.sleep.run()
    assert len(result["semantic_created"]) == 1
    assert runtime.memories.recent("semantic", 10)[0]["content"]["occurrences"] == 3


def test_14_repeated_success_proposes_skill(runtime_factory):
    runtime = runtime_factory()
    for index in range(3):
        plan = Plan(
            rationale="repeat",
            actions=[ActionSpec(tool="noop", arguments={"x": index}, purpose="repeat check", expected_result="ok", acceptance=["output ok is true"])],
        )
        runtime._persist_plan_and_ack_events(f"repeat_{index}", plan, [])
        runtime._execute_plan(plan)
    created = runtime.skills.propose_from_action_sequences(minimum_repeats=3)
    assert len(created) == 1
    row = runtime.db.query_one("SELECT status FROM skills WHERE skill_id=?", (created[0],))
    assert row["status"] == "PROPOSED"


def test_15_git_sensor_observes_repository_change(runtime_factory, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    sensor = SensorConfig(sensor_type="git", name="git", interval_seconds=0.01, settings={"repositories": [str(repo)]})
    runtime = runtime_factory(sensors=[sensor])
    runtime.run_cycle()
    (repo / "a.txt").write_text("b", encoding="utf-8")
    time.sleep(0.02)
    runtime.run_cycle()
    facts = runtime.world.query(str(repo), 20)
    assert any(fact["predicate"] == "repository_state" and fact["value"]["dirty"] for fact in facts)


def test_16_http_sensor_health_and_redirect_block(runtime_factory):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/ok")
                self.end_headers()
            else:
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        sensor = SensorConfig(
            sensor_type="http", name="http", interval_seconds=0.01,
            settings={
                "allowed_hosts": ["127.0.0.1"],
                "endpoints": [
                    {"name": "ok", "url": f"http://127.0.0.1:{port}/ok"},
                    {"name": "redirect", "url": f"http://127.0.0.1:{port}/redirect"},
                ],
            },
        )
        runtime = runtime_factory(sensors=[sensor])
        runtime.run_cycle()
        facts = runtime.world.query("health", 20)
        values = {fact["subject"]: fact["value"] for fact in facts if fact["predicate"] == "health"}
        assert values["ok"]["healthy"] is True
        assert values["redirect"]["healthy"] is False
        assert "redirect blocked" in values["redirect"]["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_17_evidence_tamper_detected_and_kills_runtime(runtime_factory):
    runtime = runtime_factory()
    runtime.ledger.append("test", {"x": 1})
    runtime.db.execute("UPDATE evidence SET payload_json='{}' WHERE seq=(SELECT MAX(seq) FROM evidence)")
    result = runtime.verify_integrity(full=True)
    assert result["ok"] is False
    assert runtime.db.get_runtime("kill_switch") is True


def test_18_restart_preserves_world_memory_and_goals(runtime_factory, tmp_path):
    home = tmp_path / "restart-home"
    runtime = runtime_factory(home=home)
    runtime.add_goal(Goal(title="Persistent goal", description="survive restart", priority=0.8))
    runtime.world.assimilate(Observation(source="test", kind="state", subject="x", predicate="y", value=1))
    runtime.memories.add(MemoryItem(memory_type="semantic", content={"fact": "persist"}, importance=0.8, confidence=0.9, source_ids=["source"]))
    restarted = LivingSystem(runtime.config)
    assert any(goal.title == "Persistent goal" for goal in restarted.goals.active())
    assert restarted.world.query("x y")[0]["value"] == 1
    assert restarted.memories.retrieve("persist")[0]["content"]["fact"] == "persist"


def test_19_loopback_api_requires_token_and_returns_status(runtime_factory):
    runtime = runtime_factory()
    server_wrapper = WLSServer(runtime, "127.0.0.1", 0)
    temp_server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = temp_server.server_address[1]
    temp_server.server_close()
    server_wrapper = WLSServer(runtime, "127.0.0.1", port)
    thread = Thread(target=server_wrapper.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2)
        assert exc.value.code == 401
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/status",
            headers={"Authorization": f"Bearer {server_wrapper.token}"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            data = json.loads(response.read())
        assert data["version"] == "0.1.0a1"
    finally:
        server_wrapper.shutdown()
        thread.join(timeout=2)


def test_20_full_observe_model_act_learn_grow_cycle(runtime_factory, tmp_path):
    watched = tmp_path / "life"
    watched.mkdir()
    sensor = SensorConfig(
        sensor_type="filesystem", name="life_files", interval_seconds=0.01,
        settings={"roots": [str(watched)], "recursive": True, "max_files": 100},
    )
    runtime = runtime_factory(sensors=[sensor], sleep_after_idle_cycles=1)
    runtime.config.tool_policy["allowed_read_roots"].append(str(watched))
    target = watched / "state.txt"
    for index in range(3):
        target.write_text(f"version {index}", encoding="utf-8")
        time.sleep(0.02)
        result = runtime.run_cycle()
        assert result["status"] == "SUCCEEDED"
    assert runtime.world.query(str(target), 20)
    assert runtime.memories.recent("episodic", 10)
    assert "capability.tool.read_file" in runtime.self_model.snapshot()
    runtime.sleep.run()
    all_skills = runtime.db.query_all("SELECT * FROM skills")
    assert all_skills
    assert runtime.verify_integrity()["ok"] is True
