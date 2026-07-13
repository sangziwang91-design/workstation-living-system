from __future__ import annotations

import json

from wls import cli
from wls.config import default_config
from wls.runtime import LivingSystem


def test_bounded_soak_zero_cycle_records_baseline(tmp_path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))

    receipt = runtime.run_bounded_soak(cycles=0, reason="unit test baseline soak")

    assert receipt["status"] == "BOUNDED_SOAK_PASSED"
    assert receipt["requested_cycles"] == 0
    assert receipt["completed_cycles"] == 0
    assert receipt["growth"]["cycle_count"] == 0
    assert receipt["daemon_started"] is False
    assert runtime.bounded_soak_receipts()[0]["audit_id"] == receipt["audit_id"]
    assert runtime.health_snapshot()["bounded_soak"]["audit_id"] == receipt["audit_id"]


def test_bounded_soak_records_successful_cycle(tmp_path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))

    def fake_cycle() -> dict[str, object]:
        count = int(runtime.db.get_runtime("cycle_count", 0)) + 1
        runtime.db.set_runtime("cycle_count", count)
        return {
            "status": "SUCCEEDED",
            "cycle_id": "cycle-unit",
            "phase_timings": [
                {"phase": "memory_retrieval", "elapsed_seconds": 0.2},
                {"phase": "context_assembly", "elapsed_seconds": 0.5},
            ],
            "sensors": [
                {
                    "sensor": "system_resources",
                    "observations": 0,
                    "elapsed_seconds": 0.1,
                    "status": "ok",
                }
            ],
            "planning_timings": [
                {"step": "planner_plan", "elapsed_seconds": 0.3}
            ],
            "event_goal_timings": [
                {
                    "step": "reserve_events",
                    "elapsed_seconds": 0.1,
                    "limit": 16,
                    "reserved": 3,
                }
            ],
            "cognition_learning_timings": [
                {"step": "learning_record_episode", "elapsed_seconds": 0.4}
            ],
        }

    runtime.run_cycle = fake_cycle  # type: ignore[method-assign]

    receipt = runtime.run_bounded_soak(
        cycles=1,
        reason="unit test one cycle soak",
        max_cycle_seconds=10,
    )

    assert receipt["status"] == "BOUNDED_SOAK_PASSED"
    assert receipt["completed_cycles"] == 1
    assert receipt["growth"]["cycle_count"] == 1
    assert receipt["cycle_results"][0]["phase_timings"][0]["phase"] == "memory_retrieval"
    assert receipt["cycle_results"][0]["slowest_phases"] == [
        {"phase": "context_assembly", "elapsed_seconds": 0.5},
        {"phase": "memory_retrieval", "elapsed_seconds": 0.2},
    ]
    assert receipt["cycle_results"][0]["sensor_summaries"][0]["sensor"] == "system_resources"
    assert receipt["cycle_results"][0]["planning_timings"][0]["step"] == "planner_plan"
    assert receipt["cycle_results"][0]["event_goal_timings"][0]["limit"] == 16
    assert (
        receipt["cycle_results"][0]["cognition_learning_timings"][0]["step"]
        == "learning_record_episode"
    )


def test_cli_soak_audit_zero_cycle(tmp_path, capsys) -> None:
    home = tmp_path / "home"
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "soak-audit",
                "--cycles",
                "0",
                "--reason",
                "unit test cli soak",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["receipt_type"] == "BOUNDED_SOAK_AUDIT"
    assert payload["requested_cycles"] == 0
    assert payload["daemon_started"] is False
