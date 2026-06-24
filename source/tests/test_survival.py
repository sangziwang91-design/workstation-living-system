from __future__ import annotations

from typing import Any, cast
import json

from wls.config import BUILTIN_SURVIVAL_PLUGIN, default_config, load_config, save_config
from wls.runtime import LivingSystem
from wls.schemas import Event, new_id, utc_now


def _runtime(tmp_path, name: str = "home") -> LivingSystem:
    config = default_config(tmp_path / name)
    config.sensors = []
    config.cycle_seconds = 0.001
    config.sleep_after_idle_cycles = 1000
    config.daemon_failure_backoff_seconds = 0.0
    config.daemon_failure_backoff_max_seconds = 0.0
    return LivingSystem(config)


def test_daemon_contains_transient_failures_and_completes(tmp_path, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    target = cast(Any, runtime)
    calls = 0

    def flaky_cycle():
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError(f"transient-{calls}")
        return {"status": "SUCCEEDED", "cycle_id": f"fake-{calls}"}

    monkeypatch.setattr(runtime, "run_cycle", flaky_cycle)
    report = target.run_daemon(max_cycles=5)

    assert report["status"] == "COMPLETED"
    assert report["completed_cycles"] == 3
    assert report["failed_cycles"] == 2
    assert report["consecutive_failures"] == 0
    assert runtime.db.get_runtime("paused", False) is False
    assert target.survival.status()["heartbeat_count"] == 5
    incidents = runtime.db.query_all(
        "SELECT kind FROM runtime_incidents ORDER BY created_at"
    )
    assert [row["kind"] for row in incidents] == [
        "CYCLE_EXCEPTION",
        "CYCLE_EXCEPTION",
    ]
    assert runtime.ledger.verify()[0] is True


def test_daemon_pauses_when_consecutive_failure_budget_is_exhausted(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime(tmp_path)
    target = cast(Any, runtime)
    runtime.config.daemon_max_consecutive_failures = 2

    def failing_cycle():
        raise ValueError("persistent failure")

    monkeypatch.setattr(runtime, "run_cycle", failing_cycle)
    report = target.run_daemon(max_cycles=10)

    assert report["status"] == "PAUSED_FAILURE_BUDGET"
    assert report["completed_cycles"] == 0
    assert report["failed_cycles"] == 2
    assert report["consecutive_failures"] == 2
    assert runtime.db.get_runtime("paused") is True
    assert "failure budget exhausted" in runtime.db.get_runtime("pause_reason")


def test_daemon_pauses_before_event_backlog_exceeds_budget(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    target = cast(Any, runtime)
    runtime.config.daemon_max_pending_events = 3
    for index in range(4):
        runtime.ingest_event(
            Event(
                event_type="storm",
                source="test",
                payload={"index": index},
                dedupe_key=f"storm-{index}",
            )
        )

    report = target.run_daemon(max_cycles=1)

    assert report["status"] == "PAUSED_RESOURCE_BUDGET"
    assert report["completed_cycles"] == 0
    incident = runtime.db.query_one(
        "SELECT kind,details_json FROM runtime_incidents ORDER BY created_at DESC LIMIT 1"
    )
    assert incident is not None
    assert incident["kind"] == "EVENT_BACKLOG_BUDGET"
    assert json.loads(incident["details_json"])["actual"] == 4


def test_startup_recovers_interrupted_cycle_and_survival_run(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    target = cast(Any, runtime)
    cycle_id = new_id("cycle")
    run_id = target.survival.start_run(max_cycles=20)
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )

    restarted = LivingSystem(runtime.config)

    cycle = restarted.db.query_one(
        "SELECT status,error FROM cycles WHERE cycle_id=?", (cycle_id,)
    )
    run = restarted.db.query_one(
        "SELECT status,termination_reason FROM survival_runs WHERE run_id=?", (run_id,)
    )
    assert cycle is not None
    assert run is not None
    assert cycle["status"] == "FAILED"
    assert "interrupted runtime process" in cycle["error"]
    assert run["status"] == "INTERRUPTED"
    assert "process ended" in run["termination_reason"]
    assert restarted.db.integrity_check() == (True, "ok")
    assert restarted.ledger.verify()[0] is True


def test_bounded_real_runtime_soak_preserves_integrity(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    target = cast(Any, runtime)

    report = target.run_daemon(max_cycles=40)

    assert report["status"] == "COMPLETED"
    assert report["completed_cycles"] == 40
    assert report["failed_cycles"] == 0
    assert runtime.db.get_runtime("cycle_count") == 40
    assert runtime.db.integrity_check() == (True, "ok")
    assert runtime.ledger.verify()[0] is True
    status = runtime.status()
    assert status["survival"]["latest_run"]["status"] == "COMPLETED"


def test_legacy_config_receives_mandatory_builtin_supervisor(tmp_path) -> None:
    config = default_config(tmp_path / "legacy")
    config.plugin_modules = [BUILTIN_SURVIVAL_PLUGIN]
    path = tmp_path / "legacy" / "config.json"
    save_config(config, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["plugin_modules"] = []
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_config(path)

    assert loaded.plugin_modules[0] == BUILTIN_SURVIVAL_PLUGIN
    runtime = LivingSystem(loaded)
    status = runtime.status()
    assert status["survival"]["budgets"]["max_consecutive_failures"] == 3
