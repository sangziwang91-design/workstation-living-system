from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "source" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from campaign_state import (  # noqa: E402
    CampaignLock,
    CampaignPaths,
    load_json,
    validate_campaign_spec,
)
from run_life_campaign_30 import CampaignRunner, expand_rounds  # noqa: E402


def _write_fake_wls(live_home: Path) -> None:
    (live_home / "wls.py").write_text(
        """
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("self-check")
    sub.add_parser("verify")
    sub.add_parser("once")
    goal = sub.add_parser("add-goal")
    goal.add_argument("title")
    goal.add_argument("--description", default="")
    goal.add_argument("--criterion", action="append", default=[])
    daemon = sub.add_parser("daemon")
    daemon.add_argument("--max-cycles", type=int, default=1)
    pause = sub.add_parser("pause")
    pause.add_argument("reason")
    resume = sub.add_parser("resume")
    resume.add_argument("evidence")
    kill = sub.add_parser("kill")
    kill.add_argument("reason")
    reset = sub.add_parser("reset-kill")
    reset.add_argument("evidence")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    home = Path(config["home"])
    state_path = home / "fake_status.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"cycle_count": 0, "paused": False, "killed": False}
    if args.command == "once":
        sensors = {item.get("name"): item for item in config.get("sensors", [])}
        db_path = home / "state" / "wls.db"
        if "campaign_fault_http" in sensors:
            import sqlite3
            sensor = sensors["campaign_fault_http"]
            connection = sqlite3.connect(db_path)
            if sensor.get("sensor_type") == "http":
                connection.execute(
                    "INSERT OR REPLACE INTO sensor_state(sensor_name,state_json,last_polled_at,last_success_at,last_error) VALUES (?,?,?,?,?)",
                    ("campaign_fault_http", "{}", "now", "now", None),
                )
                connection.execute(
                    "INSERT INTO events(event_id,event_type,payload_json) VALUES (?,?,?)",
                    (
                        "evt-fault-http",
                        "observation.service_health",
                        json.dumps(
                            {
                                "observation": {
                                    "source": "campaign_fault_http",
                                    "value": {
                                        "healthy": False,
                                        "error": "connection refused",
                                    },
                                }
                            }
                        ),
                    ),
                )
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO sensor_state(sensor_name,state_json,last_polled_at,last_success_at,last_error) VALUES (?,?,?,?,?)",
                    ("campaign_fault_http", "{}", "now", "now", None),
                )
            connection.commit()
            connection.close()
        if not state["paused"] and not state["killed"]:
            state["cycle_count"] += 1
        payload = {"status": "PAUSED" if state["paused"] else ("KILLED" if state["killed"] else "SUCCEEDED")}
    elif args.command == "daemon":
        if not state["paused"] and not state["killed"]:
            state["cycle_count"] += int(args.max_cycles)
        payload = {"status": "SUCCEEDED", "cycles": int(args.max_cycles)}
    elif args.command == "pause":
        state["paused"] = True
        payload = {"evidence_id": "pause"}
    elif args.command == "resume":
        state["paused"] = False
        payload = {"evidence_id": "resume"}
    elif args.command == "kill":
        state["killed"] = True
        payload = {"evidence_id": "kill"}
    elif args.command == "reset-kill":
        state["killed"] = False
        payload = {"evidence_id": "reset"}
    elif args.command == "add-goal":
        import sqlite3
        connection = sqlite3.connect(home / "state" / "wls.db")
        connection.execute(
            "INSERT INTO goals(goal_id,title,status,progress,source,autonomous) VALUES (?,?,?,?,?,?)",
            (f"goal-{state.get('goal_count', 0) + 1}", args.title, "ACTIVE", 0.0, "cli", 0),
        )
        connection.commit()
        connection.close()
        state["goal_count"] = state.get("goal_count", 0) + 1
        payload = {"goal_id": f"goal-{state['goal_count']}"}
    elif args.command == "self-check":
        payload = {"ok": True, "checks": {"read_only": True}}
    elif args.command == "verify":
        payload = {"ok": True}
    else:
        payload = {
            "home": str(home),
            "read_only": True,
            "paused": state["paused"],
            "killed": state["killed"],
            "cycle_count": state["cycle_count"],
        }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""".lstrip(),
        encoding="utf-8",
    )


def _make_fake_install(tmp_path: Path) -> tuple[CampaignPaths, Path]:
    install_root = tmp_path / "install"
    live_home = install_root / "home"
    campaign_home = tmp_path / "campaign"
    (install_root / "venv" / "Scripts").mkdir(parents=True)
    (install_root / "INSTALL_RECEIPT.json").write_text(
        json.dumps({"wheel_sha256": "abc"}), encoding="utf-8"
    )
    (install_root / "venv" / "Scripts" / "python.exe").write_text(
        "placeholder", encoding="utf-8"
    )
    (live_home / "state").mkdir(parents=True)
    (live_home / "inbox").mkdir()
    (live_home / "sandbox").mkdir()
    (live_home / "outbox").mkdir()
    (live_home / "config.json").write_text(
        json.dumps(
            {
                "home": str(live_home),
                "read_only": True,
                "cycle_seconds": 30.0,
                "provider": {"type": "deterministic"},
                "tool_policy": {},
            }
        ),
        encoding="utf-8",
    )
    connection = sqlite3.connect(live_home / "state" / "wls.db")
    connection.execute(
        "CREATE TABLE actions(action_id TEXT, idempotency_key TEXT, status TEXT, started_at TEXT, finished_at TEXT)"
    )
    connection.execute(
        "CREATE TABLE sensor_state(sensor_name TEXT PRIMARY KEY,state_json TEXT,last_polled_at TEXT,last_success_at TEXT,last_error TEXT)"
    )
    connection.execute(
        "CREATE TABLE goals(goal_id TEXT,title TEXT,status TEXT,progress REAL,source TEXT,autonomous INTEGER)"
    )
    connection.execute("CREATE TABLE events(event_id TEXT,event_type TEXT,payload_json TEXT)")
    connection.execute("CREATE TABLE evidence(evidence_id TEXT)")
    connection.execute("CREATE TABLE cycles(cycle_id TEXT)")
    connection.commit()
    connection.close()
    _write_fake_wls(live_home)
    paths = CampaignPaths(
        install_root=install_root,
        live_home=live_home,
        campaign_home=campaign_home,
        wls_python=Path(sys.executable),
    )
    return paths, live_home / "config.json"


def test_campaign_spec_has_required_30_round_shape() -> None:
    spec = load_json(REPO_ROOT / "source" / "verification" / "life_campaign_30.json")
    round_ids = validate_campaign_spec(spec)
    assert round_ids[0] == "R01"
    assert round_ids[-1] == "R30"
    assert len(round_ids) == 30
    assert spec["rounds"][0]["title"] == "Owner-host campaign baseline lock"


def test_expand_rounds_is_contiguous_and_rejects_reverse() -> None:
    assert expand_rounds("R02", "R04", None) == ["R02", "R03", "R04"]
    with pytest.raises(ValueError):
        expand_rounds("R04", "R02", None)


def test_campaign_paths_reject_live_home_as_campaign_home(tmp_path: Path) -> None:
    paths, _ = _make_fake_install(tmp_path)
    bad = CampaignPaths(
        install_root=paths.install_root,
        live_home=paths.live_home,
        campaign_home=paths.live_home,
        wls_python=Path(sys.executable),
    )
    with pytest.raises(ValueError):
        bad.validate()


def test_campaign_lock_is_exclusive(tmp_path: Path) -> None:
    lock_path = tmp_path / "campaign" / ".campaign.lock"
    with CampaignLock(lock_path):
        with pytest.raises(RuntimeError):
            with CampaignLock(lock_path):
                pass


def test_runner_executes_r01_to_r13_against_disposable_campaign_home(
    tmp_path: Path,
) -> None:
    paths, live_config = _make_fake_install(tmp_path)
    runner = CampaignRunner(
        REPO_ROOT / "source" / "verification" / "life_campaign_30.json",
        paths,
        execute=True,
    )
    result = runner.run([f"R{index:02d}" for index in range(1, 14)])
    assert [item["status"] for item in result["results"]] == ["PASS"] * 13
    assert result["automation_level"] == "LEVEL_1"
    campaign_config = load_json(paths.campaign_home / "config.json")
    assert campaign_config["home"] == str(paths.campaign_home.resolve())
    assert campaign_config["read_only"] is True
    assert load_json(live_config)["home"] == str(paths.live_home)
    manifest = load_json(paths.campaign_home / "campaign_evidence" / "manifest.json")
    assert any(record["kind"] == "command" for record in manifest["records"])
    assert (paths.campaign_home / "state" / "wls.db").exists()
    assert any(record["round_id"] == "R13" for record in manifest["records"])


def test_runner_blocks_after_failed_round(tmp_path: Path) -> None:
    paths, _ = _make_fake_install(tmp_path)
    (paths.live_home / "wls.py").write_text(
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    runner = CampaignRunner(
        REPO_ROOT / "source" / "verification" / "life_campaign_30.json",
        paths,
        execute=True,
    )
    result = runner.run(["R01", "R02"])
    assert result["results"][0]["status"] == "FAIL"
    state = load_json(paths.campaign_home / "campaign_state.json")
    assert state["rounds"]["R02"]["status"] == "BLOCKED"


def test_repair_reset_preserves_failure_history_and_allows_rerun(tmp_path: Path) -> None:
    paths, _ = _make_fake_install(tmp_path)
    broken = paths.live_home / "wls.py"
    original = broken.read_text(encoding="utf-8")
    broken.write_text("raise SystemExit(3)\n", encoding="utf-8")
    runner = CampaignRunner(
        REPO_ROOT / "source" / "verification" / "life_campaign_30.json",
        paths,
        execute=True,
    )
    first = runner.run(["R01", "R02"])
    assert first["results"][0]["status"] == "FAIL"
    broken.write_text(original, encoding="utf-8")
    campaign_copy = paths.campaign_home / "wls.py"
    if campaign_copy.exists():
        campaign_copy.write_text(original, encoding="utf-8")
    reset = runner.reset_from_round("R01", "test repair")
    assert reset["reset_from"] == "R01"
    second = runner.run(["R01"])
    assert second["results"][0]["status"] == "PASS"
    state = load_json(paths.campaign_home / "campaign_state.json")
    assert state["rounds"]["R01"]["repair_history"]


def test_duplicate_action_check_allows_idempotent_reuse(tmp_path: Path) -> None:
    paths, _ = _make_fake_install(tmp_path)
    runner = CampaignRunner(
        REPO_ROOT / "source" / "verification" / "life_campaign_30.json",
        paths,
        execute=True,
    )
    runner._prepare_campaign_home()
    connection = sqlite3.connect(paths.campaign_home / "state" / "wls.db")
    connection.execute(
        "INSERT INTO actions(action_id,idempotency_key,status) VALUES (?,?,?)",
        ("act-real", "same-key", "SUCCEEDED"),
    )
    connection.execute("UPDATE actions SET started_at='started' WHERE action_id='act-real'")
    connection.execute(
        "INSERT INTO actions(action_id,idempotency_key,status,started_at,finished_at) VALUES (?,?,?,?,?)",
        ("act-reused", "same-key", "SUCCEEDED", None, "finished"),
    )
    connection.commit()
    connection.close()
    evidence_id = runner._record_duplicate_action_check("R03")
    assert evidence_id


def test_dry_run_prepares_owner_command_without_runtime_claim(tmp_path: Path) -> None:
    paths, _ = _make_fake_install(tmp_path)
    runner = CampaignRunner(
        REPO_ROOT / "source" / "verification" / "life_campaign_30.json",
        paths,
        execute=False,
    )
    result = runner.run(["R01"])
    assert result["results"][0]["status"] == "OWNER_REVIEW"
    assert "-StartRound R01 -EndRound R01 -Execute" in result["results"][0]["owner_command"]


def test_powershell_wrapper_uses_file_runner_not_source_module() -> None:
    script = (REPO_ROOT / "scripts" / "run_life_campaign_30.ps1").read_text(
        encoding="utf-8"
    )
    assert "source\\scripts\\run_life_campaign_30.py" in script
    assert "python -m source.scripts.run_life_campaign_30" not in script
    assert "venv\\Scripts\\python.exe" in script


def test_repository_integration_cli_dry_run(tmp_path: Path) -> None:
    paths, _ = _make_fake_install(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "source" / "scripts" / "run_life_campaign_30.py"),
            "--install-root",
            str(paths.install_root),
            "--live-home",
            str(paths.live_home),
            "--campaign-home",
            str(paths.campaign_home),
            "--wls-python",
            sys.executable,
            "--start-round",
            "R01",
            "--end-round",
            "R01",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["results"][0]["status"] == "OWNER_REVIEW"
