from __future__ import annotations

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json
import subprocess
import time
import urllib.error
import urllib.request

import pytest

from wls.adaptive_growth import SkillExperimentRunner
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
    assert runtime.status()["version"] == "0.1.0a1"
    assert runtime.status()["self_model"]["identity"]["value"]["subjective_consciousness"] == "UNKNOWN"
    assert runtime.verify_integrity()["ok"] is True


def test_02_filesystem_create_modify_delete(runtime_factory, tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()
    runtime = runtime_factory(
        sensors=[SensorConfig("filesystem", "files", interval_seconds=0.01, settings={"roots": [str(watched)], "recursive": True})]
    )
    target = watched / "note.txt"
    target.write_text("one", encoding="utf-8")
    assert runtime.run_cycle()["selected_events"] >= 1
    target.write_text("two", encoding="utf-8")
    time.sleep(0.02)
    runtime.run_cycle()
    target.unlink()
    time.sleep(0.02)
    runtime.run_cycle()
    assert any(fact["value"] == "deleted" for fact in runtime.world.query(str(target), 20))


def test_03_inbox_durable_exactly_once(runtime_factory, tmp_path):
    runtime = runtime_factory(sensors=[SensorConfig("inbox", "inbox", interval_seconds=0.01)])
    request = runtime.config.inbox_path / "request.json"
    request.write_text(json.dumps({"subject": "user", "predicate": "request", "value": {"action": "inspect_path", "path": str(tmp_path)}}), encoding="utf-8")
    time.sleep(0.02)
    runtime.run_cycle()
    assert (runtime.config.inbox_path / "processed" / "request.json").exists()
    first = runtime.db.query_one("SELECT COUNT(*) AS n FROM observations WHERE source='inbox'")["n"]
    time.sleep(0.02)
    runtime.run_cycle()
    second = runtime.db.query_one("SELECT COUNT(*) AS n FROM observations WHERE source='inbox'")["n"]
    assert first == second == 1


def test_04_attention_overflow_keeps_unselected_pending(runtime_factory):
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
    runtime.planner.provider = FixedProvider({
        "rationale": "test write",
        "actions": [{"tool": "write_file", "arguments": {"path": str(target), "content": "approved"}, "purpose": "write approved sandbox file", "expected_result": "file written", "risk": "REVERSIBLE_WRITE", "goal_id": None, "skill_id": None, "acceptance": ["output contains path"]}],
        "memory_ids": [], "world_fact_ids": [], "unknowns": [],
    })
    runtime.ingest_event(Event(event_type="user.task", source="test", payload={"task": "write"}, salience_hint=1.0))
    runtime.run_cycle()
    action = runtime.db.query_one("SELECT action_id,status FROM actions ORDER BY rowid DESC LIMIT 1")
    assert action["status"] == "WAITING_APPROVAL"
    runtime.approvals.issue(action["action_id"], True, reason="reviewed")
    assert runtime.resume_action(action["action_id"])["success"] is True
    assert target.read_text(encoding="utf-8") == "approved"
    with pytest.raises(ValueError):
        runtime.resume_action(action["action_id"])


def test_06_approval_digest_rejects_tampering(runtime_factory):
    runtime = runtime_factory(read_only=True)
    target = runtime.config.sandbox_path / "original.txt"
    plan = Plan(rationale="tamper", actions=[ActionSpec(tool="write_file", arguments={"path": str(target), "content": "one"}, purpose="original", expected_result="written", risk=RiskLevel.REVERSIBLE_WRITE, acceptance=["output contains path"])])
    runtime._persist_plan_and_ack_events("cycle_tamper", plan, [])
    action = plan.actions[0]
    runtime._execute_action(action)
    runtime.approvals.issue(action.action_id, True)
    runtime.db.execute("UPDATE actions SET arguments_json=? WHERE action_id=?", (json.dumps({"path": str(target), "content": "tampered"}), action.action_id))
    assert runtime.resume_action(action.action_id)["success"] is False
    assert not target.exists()


def test_07_kill_switch_survives_restart(runtime_factory, tmp_path):
    runtime = runtime_factory(home=tmp_path / "home")
    runtime.kill("emergency")
    restarted = LivingSystem(runtime.config)
    assert restarted.run_cycle()["status"] == "KILLED"
    restarted.reset_kill("human evidence")
    assert restarted.run_cycle()["status"] == "SUCCEEDED"


def test_08_unknown_side_effect_is_never_replayed(runtime_factory):
    runtime = runtime_factory()
    plan = Plan(rationale="external", actions=[ActionSpec(tool="run_command", arguments={"command": ["python", "-c", "print(1)"]}, purpose="external", expected_result="done", risk=RiskLevel.HIGH)])
    runtime._persist_plan_and_ack_events("cycle_crash", plan, [])
    runtime.db.execute("UPDATE actions SET status='RUNNING',started_at=? WHERE action_id=?", (datetime.now(UTC).isoformat(), plan.actions[0].action_id))
    restarted = LivingSystem(runtime.config)
    row = restarted.db.query_one("SELECT status FROM actions WHERE action_id=?", (plan.actions[0].action_id,))
    assert row["status"] == "UNKNOWN_SIDE_EFFECT"


def test_09_contradiction_creates_bounded_goal(runtime_factory):
    runtime = runtime_factory(max_actions_per_cycle=0)
    runtime.world.assimilate(Observation(source="a", kind="state", subject="service:x", predicate="healthy", value=True))
    runtime.world.assimilate(Observation(source="b", kind="state", subject="service:x", predicate="healthy", value=False))
    runtime.run_cycle()
    assert any(goal.autonomous and goal.title.startswith("Clarify contradiction") for goal in runtime.goals.active())


def test_10_prediction_error_creates_revision_candidate(runtime_factory):
    runtime = runtime_factory()
    for _ in range(2):
        runtime.world.add_prediction("service:y", "healthy", True, 0.9, "test")
        runtime.world.assimilate(Observation(source="probe", kind="health", subject="service:y", predicate="healthy", value=False))
    candidates = runtime.learning.create_prediction_error_candidates()
    assert len(candidates) == 1
    row = runtime.db.query_one("SELECT candidate_type,status FROM evolution_candidates WHERE candidate_id=?", (candidates[0],))
    assert row["candidate_type"] == "world_model_revision" and row["status"] == "PROPOSED"


def test_11_persisted_experiment_then_human_promotion_changes_behavior(runtime_factory):
    runtime = runtime_factory()
    action_ids = []
    for index in range(3):
        plan = Plan(rationale=f"source {index}", actions=[ActionSpec(tool="noop", arguments={"case": index}, purpose="apply learned alpha response", expected_result="ok", acceptance=["output ok is true"])])
        runtime._persist_plan_and_ack_events(f"alpha_{index}", plan, [])
        assert runtime._execute_plan(plan)[0]["success"] is True
        action_ids.append(plan.actions[0].action_id)
    skill = SkillDefinition(name="respond_alpha", description="Learned bounded response.", trigger_terms=["alpha"], steps=[{"tool": "noop", "arguments": {"learned": True}, "purpose": "apply learned alpha response", "acceptance": ["output ok is true"]}], source_episode_ids=action_ids)
    runtime.skills.add(skill)
    experiment = SkillExperimentRunner(runtime.db, runtime.ledger, runtime.skills, runtime.config).run(skill.skill_id)
    assert experiment["status"] == "PASSED"
    runtime.skills.transition(skill.skill_id, CandidateStatus.APPROVED, {"reviewer": "owner"}, human_approved=True)
    runtime.skills.transition(skill.skill_id, CandidateStatus.PROMOTED, {"release": "test"}, human_approved=True)
    runtime.ingest_event(Event(event_type="external.alpha", source="test", payload={"message": "alpha"}, salience_hint=1.0))
    runtime.run_cycle()
    row = runtime.db.query_one("SELECT use_count,success_rate FROM skills WHERE skill_id=?", (skill.skill_id,))
    assert row["use_count"] == 1 and row["success_rate"] == 1.0


def test_12_self_model_calibrates_success_and_failure(runtime_factory):
    runtime = runtime_factory()
    good = Plan(rationale="good", actions=[ActionSpec(tool="noop", arguments={}, purpose="good", expected_result="ok", acceptance=["output ok is true"])])
    runtime._persist_plan_and_ack_events("good", good, [])
    runtime._execute_plan(good)
    bad = Plan(rationale="bad", actions=[ActionSpec(tool="read_file", arguments={"path": str(runtime.config.home_path / "missing")}, purpose="bad", expected_result="read", acceptance=["output contains path"])])
    runtime._persist_plan_and_ack_events("bad", bad, [])
    runtime._execute_plan(bad)
    model = runtime.self_model.snapshot()
    assert model["capability.tool.noop"]["value"]["observed_success_rate"] == 1.0
    assert model["capability.tool.read_file"]["value"]["observed_success_rate"] == 0.0


def test_13_sleep_consolidates_repeated_experience(runtime_factory):
    runtime = runtime_factory()
    workspace = [{"item_type": "event", "reference_id": "evt", "summary": "same", "salience": 1.0, "reasons": [], "payload": {"payload": {"observation": {"kind": "health", "subject": "svc", "predicate": "healthy", "value": False}}}}]
    for index in range(3):
        runtime.learning.record_episode(f"cycle_{index}", [f"evt_{index}"], None, [], [], workspace)
    assert len(runtime.sleep.run()["semantic_created"]) == 1


def test_14_repeated_success_proposes_unvalidated_skill(runtime_factory):
    runtime = runtime_factory()
    for index in range(3):
        plan = Plan(rationale="repeat", actions=[ActionSpec(tool="noop", arguments={"x": index}, purpose="repeat check", expected_result="ok", acceptance=["output ok is true"])])
        runtime._persist_plan_and_ack_events(f"repeat_{index}", plan, [])
        runtime._execute_plan(plan)
    created = runtime.skills.propose_from_action_sequences(minimum_repeats=3)
    assert len(created) == 1
    assert runtime.db.query_one("SELECT status FROM skills WHERE skill_id=?", (created[0],))["status"] == "PROPOSED"


def test_15_git_sensor_observes_dirty_repository(runtime_factory, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    runtime = runtime_factory(sensors=[SensorConfig("git", "git", interval_seconds=0.01, settings={"repositories": [str(repo)]})])
    runtime.run_cycle()
    (repo / "a.txt").write_text("b", encoding="utf-8")
    time.sleep(0.02)
    runtime.run_cycle()
    assert any(fact["predicate"] == "repository_state" and fact["value"]["dirty"] for fact in runtime.world.query(str(repo), 20))


def test_16_http_sensor_blocks_redirect(runtime_factory):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302); self.send_header("Location", "/ok"); self.end_headers(); return
            body = b'{"ok":true}'; self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        port = server.server_address[1]
        runtime = runtime_factory(sensors=[SensorConfig("http", "http", interval_seconds=0.01, settings={"allowed_hosts": ["127.0.0.1"], "endpoints": [{"name": "ok", "url": f"http://127.0.0.1:{port}/ok"}, {"name": "redirect", "url": f"http://127.0.0.1:{port}/redirect"}]})])
        runtime.run_cycle()
        values = {fact["subject"]: fact["value"] for fact in runtime.world.query("health", 20) if fact["predicate"] == "health"}
        assert values["ok"]["healthy"] is True
        assert values["redirect"]["healthy"] is False
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_17_evidence_tamper_is_detected(runtime_factory):
    runtime = runtime_factory()
    runtime.ledger.append("test", {"x": 1})
    runtime.db.execute("UPDATE evidence SET payload_json='{}' WHERE seq=(SELECT MAX(seq) FROM evidence)")
    assert runtime.verify_integrity(full=True)["ok"] is False
    assert runtime.db.get_runtime("kill_switch") is True


def test_18_restart_preserves_world_memory_and_goals(runtime_factory, tmp_path):
    runtime = runtime_factory(home=tmp_path / "restart")
    runtime.add_goal(Goal(title="Persistent goal", description="survive", priority=0.8))
    runtime.world.assimilate(Observation(source="test", kind="state", subject="x", predicate="y", value=1))
    runtime.memories.add(MemoryItem(memory_type="semantic", content={"fact": "persist"}, importance=0.8, confidence=0.9, source_ids=["source"]))
    restarted = LivingSystem(runtime.config)
    assert any(goal.title == "Persistent goal" for goal in restarted.goals.active())
    assert restarted.world.query("x y")[0]["value"] == 1
    assert restarted.memories.retrieve("persist")[0]["content"]["fact"] == "persist"


def test_19_loopback_api_requires_token(runtime_factory):
    runtime = runtime_factory()
    probe = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler); port = probe.server_address[1]; probe.server_close()
    server = WLSServer(runtime, "127.0.0.1", port)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start(); time.sleep(0.1)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2)
        assert exc.value.code == 401
        request = urllib.request.Request(f"http://127.0.0.1:{port}/status", headers={"Authorization": f"Bearer {server.token}"})
        with urllib.request.urlopen(request, timeout=2) as response:
            assert json.loads(response.read())["version"] == "0.1.0a1"
    finally:
        server.shutdown(); thread.join(timeout=2)


def test_20_observe_model_act_learn_cycle(runtime_factory, tmp_path):
    watched = tmp_path / "life"; watched.mkdir()
    runtime = runtime_factory(sensors=[SensorConfig("filesystem", "life", interval_seconds=0.01, settings={"roots": [str(watched)], "recursive": True})], sleep_after_idle_cycles=1)
    runtime.config.tool_policy["allowed_read_roots"].append(str(watched))
    target = watched / "state.txt"
    for index in range(3):
        target.write_text(f"version {index}", encoding="utf-8"); time.sleep(0.02)
        assert runtime.run_cycle()["status"] == "SUCCEEDED"
    assert runtime.world.query(str(target), 20)
    assert runtime.memories.recent("episodic", 10)
    assert "capability.tool.read_file" in runtime.self_model.snapshot()
    assert runtime.verify_integrity()["ok"] is True
