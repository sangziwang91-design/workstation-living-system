from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
import argparse
import json
import shutil
import sqlite3
# The runner executes the installed WLS CLI with explicit argv and shell=False.
import subprocess  # nosec B404
import sys

from campaign_state import (
    CampaignLock,
    CampaignPaths,
    CampaignState,
    EvidenceManifest,
    atomic_write_json,
    load_json,
    utc_now,
    validate_campaign_spec,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC = REPO_ROOT / "source" / "verification" / "life_campaign_30.json"
DEFAULT_INSTALL_ROOT = Path(r"D:\WLS\wls-0.9.0.dev1-py313")
DEFAULT_CAMPAIGN_HOME = Path(r"D:\WLS\campaigns\life-campaign-30")
SUPPORTED_AUTOMATED_ROUNDS = {f"R{index:02d}" for index in range(1, 6)}


class CampaignRunner:
    def __init__(
        self,
        spec_path: Path,
        paths: CampaignPaths,
        execute: bool,
        fresh_snapshot: bool = False,
    ):
        self.spec_path = spec_path.resolve()
        self.spec = load_json(self.spec_path)
        validate_campaign_spec(self.spec)
        self.paths = paths.resolve()
        self.paths.validate()
        self.execute = execute
        self.fresh_snapshot = fresh_snapshot
        self.state_path = self.paths.campaign_home / "campaign_state.json"
        self.lock_path = self.paths.campaign_home / ".campaign.lock"
        self.manifest = EvidenceManifest(self.paths.campaign_home)
        self.state = CampaignState.load_or_create(
            self.state_path, self.spec, self.paths
        )

    def run(self, round_ids: list[str]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        with CampaignLock(self.lock_path):
            for round_id in round_ids:
                if round_id not in self.state.data["rounds"]:
                    raise ValueError(f"unknown round: {round_id}")
                ready, reason = self.state.can_start(round_id)
                if not ready:
                    results.append(
                        {"round_id": round_id, "status": self.state.round_status(round_id), "reason": reason}
                    )
                    break
                self.state.mark_running(round_id)
                try:
                    result = self._run_round(round_id)
                except Exception as exc:
                    verdict = {
                        "status": "FAIL",
                        "error": f"{type(exc).__name__}: {exc}",
                        "claim_ceiling": "failed round blocks all later rounds",
                    }
                    self.state.mark_fail(round_id, verdict)
                    results.append({"round_id": round_id, **verdict})
                    break
                status = result["status"]
                if status == "PASS":
                    self.state.mark_pass(round_id, result)
                elif status == "OWNER_REVIEW":
                    self.state.mark_owner_review(round_id, result)
                    results.append({"round_id": round_id, **result})
                    break
                else:
                    self.state.mark_fail(round_id, result)
                    results.append({"round_id": round_id, **result})
                    break
                results.append({"round_id": round_id, **result})
        return {
            "campaign_id": self.spec.get("campaign_id"),
            "state_path": str(self.state_path),
            "manifest_path": str(self.manifest.manifest_path),
            "automation_level": self.state.data["automation_level"],
            "results": results,
        }

    def _run_round(self, round_id: str) -> dict[str, Any]:
        if not self.execute:
            return self._planned_verdict(round_id)
        if round_id not in SUPPORTED_AUTOMATED_ROUNDS:
            return {
                "status": "OWNER_REVIEW",
                "reason": f"{round_id} is specified but not authorized for this delivery",
                "claim_ceiling": "framework-ready only; owner gate required",
            }
        handlers: dict[str, Callable[[], dict[str, Any]]] = {
            "R01": self._round_01,
            "R02": self._round_02,
            "R03": self._round_03,
            "R04": self._round_04,
            "R05": self._round_05,
        }
        return handlers[round_id]()

    def _planned_verdict(self, round_id: str) -> dict[str, Any]:
        command = self.owner_command(round_id, round_id)
        evidence_id = self._record_plan(round_id, command)
        self.state.append_evidence(round_id, evidence_id)
        return {
            "status": "OWNER_REVIEW",
            "reason": "planned only; rerun with --execute to perform owner-host round",
            "owner_command": command,
            "claim_ceiling": "no runtime claim; command prepared only",
        }

    def _round_01(self) -> dict[str, Any]:
        self._prepare_campaign_home()
        evidence_ids = [
            self._record_if_exists("R01", "install_receipt", self.paths.install_root / "INSTALL_RECEIPT.json"),
            self._record_if_exists("R01", "live_config", self.paths.live_home / "config.json"),
            self._record_if_exists("R01", "live_database", self.paths.live_home / "state" / "wls.db"),
            self._record_if_exists("R01", "campaign_config", self.paths.campaign_home / "config.json"),
            self._record_if_exists("R01", "campaign_database", self.paths.campaign_home / "state" / "wls.db"),
        ]
        status = self._wls("R01", "status", ["status"])
        self_check = self._wls("R01", "self_check", ["self-check"])
        verify = self._wls("R01", "verify", ["verify"])
        evidence_ids.extend([status, self_check, verify])
        for evidence_id in evidence_ids:
            if evidence_id:
                self.state.append_evidence("R01", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [item for item in evidence_ids if item],
            "claim_ceiling": "campaign clone and baseline locked; no source provenance claim",
        }

    def _round_02(self) -> dict[str, Any]:
        inbox = self.paths.campaign_home / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        task_path = inbox / "campaign-r02-readonly.json"
        atomic_write_json(
            task_path,
            {
                "task_id": "campaign-r02-readonly",
                "kind": "owner_readonly_observation",
                "created_at": utc_now(),
                "instruction": "Perform a distinct read-only campaign observation.",
            },
        )
        evidence_ids = [
            self.manifest.record_file("R02", "readonly_task", task_path),
            self._wls("R02", "once", ["once"]),
            self._wls("R02", "status", ["status"]),
            self._wls("R02", "verify", ["verify"]),
        ]
        for evidence_id in evidence_ids:
            self.state.append_evidence("R02", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": evidence_ids,
            "claim_ceiling": "one campaign-scoped read-only action cycle executed",
        }

    def _round_03(self) -> dict[str, Any]:
        before = self._status_json("R03", "status_before_restart")
        first = self._wls("R03", "process_one_once", ["once"])
        middle = self._status_json("R03", "status_after_first_process")
        second = self._wls("R03", "process_two_once", ["once"])
        after = self._status_json("R03", "status_after_second_process")
        verify = self._wls("R03", "verify", ["verify"])
        no_duplicates = self._record_duplicate_action_check("R03")
        for evidence_id in [first, middle["evidence_id"], second, after["evidence_id"], verify, no_duplicates]:
            self.state.append_evidence("R03", evidence_id)
        before_count = int(before["payload"].get("cycle_count", 0))
        after_count = int(after["payload"].get("cycle_count", 0))
        if after_count < before_count + 2:
            return {
                "status": "FAIL",
                "reason": "cycle_count did not increase across two process-real launches",
                "before_cycle_count": before_count,
                "after_cycle_count": after_count,
                "claim_ceiling": "restart continuity not established",
            }
        return {
            "status": "PASS",
            "evidence_ids": [first, middle["evidence_id"], second, after["evidence_id"], verify, no_duplicates],
            "claim_ceiling": "process-real restart continuity checked in campaign home",
        }

    def _round_04(self) -> dict[str, Any]:
        daemon = self._wls("R04", "daemon_10_cycles", ["daemon", "--max-cycles", "10"])
        status = self._wls("R04", "status", ["status"])
        verify = self._wls("R04", "verify", ["verify"])
        for evidence_id in [daemon, status, verify]:
            self.state.append_evidence("R04", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [daemon, status, verify],
            "claim_ceiling": "bounded 10-cycle read-only survival run only",
        }

    def _round_05(self) -> dict[str, Any]:
        evidence_ids = [
            self._wls("R05", "pause", ["pause", "campaign R05 pause probe"]),
            self._wls("R05", "once_while_paused", ["once"]),
            self._wls("R05", "resume", ["resume", "campaign R05 resume evidence"]),
            self._wls("R05", "kill", ["kill", "campaign R05 kill probe"]),
            self._wls("R05", "once_while_killed", ["once"]),
            self._wls("R05", "reset_kill", ["reset-kill", "campaign R05 reset evidence"]),
            self._wls("R05", "daemon_lease_probe", ["daemon", "--max-cycles", "2"]),
            self._wls("R05", "verify", ["verify"]),
        ]
        for evidence_id in evidence_ids:
            self.state.append_evidence("R05", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": evidence_ids,
            "claim_ceiling": "LEVEL_1 may be considered only for this campaign clone",
        }

    def _prepare_campaign_home(self) -> None:
        config_path = self.paths.campaign_home / "config.json"
        if self.fresh_snapshot and self.paths.campaign_home.exists():
            shutil.rmtree(self.paths.campaign_home)
        self.paths.campaign_home.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            return
        self._copy_live_home_without_sqlite()
        self._backup_live_sqlite()
        self._write_safe_campaign_config()

    def _copy_live_home_without_sqlite(self) -> None:
        for source in self.paths.live_home.rglob("*"):
            relative = source.relative_to(self.paths.live_home)
            target = self.paths.campaign_home / relative
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            suffixes = "".join(source.suffixes).lower()
            if suffixes.endswith(".db") or suffixes.endswith(".db-wal") or suffixes.endswith(".db-shm"):
                continue
            if source.name in {"runtime.lock", "daemon.lock"}:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _backup_live_sqlite(self) -> None:
        source_db = self.paths.live_home / "state" / "wls.db"
        if not source_db.exists():
            return
        target_db = self.paths.campaign_home / "state" / "wls.db"
        target_db.parent.mkdir(parents=True, exist_ok=True)
        uri = f"file:{source_db.as_posix()}?mode=ro"
        source = sqlite3.connect(uri, uri=True)
        target = sqlite3.connect(target_db)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def _write_safe_campaign_config(self) -> None:
        live_config_path = self.paths.live_home / "config.json"
        config = load_json(live_config_path)
        campaign_home = str(self.paths.campaign_home)
        config["home"] = campaign_home
        config["read_only"] = True
        config["allow_autonomous_read_actions"] = True
        config["allow_autonomous_reversible_writes"] = False
        config["max_autonomous_goals"] = 0
        config["cycle_seconds"] = min(float(config.get("cycle_seconds", 30.0)), 0.2)
        provider = dict(config.get("provider", {}))
        provider["type"] = "deterministic"
        provider["fallback"] = "deterministic"
        provider["goal_mode"] = "disabled"
        provider["memory_mode"] = "disabled"
        config["provider"] = provider
        config["tool_policy"] = {
            "allowed_read_roots": [campaign_home, str(self.paths.campaign_home.parent)],
            "allowed_write_roots": [
                str(self.paths.campaign_home / "sandbox"),
                str(self.paths.campaign_home / "outbox"),
            ],
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "allowed_commands": ["git", "python", "python.exe", "pytest", "pytest.exe"],
        }
        atomic_write_json(self.paths.campaign_home / "config.json", config)

    def _wls(self, round_id: str, label: str, command: list[str]) -> str:
        config_path = self.paths.campaign_home / "config.json"
        args = [str(self.paths.wls_python), "-m", "wls", "--config", str(config_path), *command]
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        # argv is built from resolved paths and fixed WLS subcommands; shell stays false.
        result = subprocess.run(  # nosec B603
            args,
            cwd=self.paths.campaign_home,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        evidence_id = self.manifest.record_command(
            round_id,
            label,
            args,
            self.paths.campaign_home,
            result.returncode,
            result.stdout,
            result.stderr,
            started_at,
            finished_at,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{label} failed with exit {result.returncode}")
        return evidence_id

    def _status_json(self, round_id: str, label: str) -> dict[str, Any]:
        config_path = self.paths.campaign_home / "config.json"
        args = [str(self.paths.wls_python), "-m", "wls", "--config", str(config_path), "status"]
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        # argv is built from resolved paths and fixed WLS subcommands; shell stays false.
        result = subprocess.run(  # nosec B603
            args,
            cwd=self.paths.campaign_home,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        evidence_id = self.manifest.record_command(
            round_id,
            label,
            args,
            self.paths.campaign_home,
            result.returncode,
            result.stdout,
            result.stderr,
            started_at,
            finished_at,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{label} failed with exit {result.returncode}")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError("status output must be JSON object")
        return {"payload": payload, "evidence_id": evidence_id}

    def _record_duplicate_action_check(self, round_id: str) -> str:
        db_path = self.paths.campaign_home / "state" / "wls.db"
        duplicates: list[dict[str, Any]] = []
        if db_path.exists():
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT idempotency_key, COUNT(*) AS count
                    FROM actions
                    WHERE status='SUCCEEDED' AND idempotency_key IS NOT NULL
                    GROUP BY idempotency_key
                    HAVING COUNT(*) > 1
                    """
                ).fetchall()
                duplicates = [dict(row) for row in rows]
            except sqlite3.Error as exc:
                duplicates = [{"sqlite_error": str(exc)}]
            finally:
                connection.close()
        report_path = self.paths.campaign_home / "campaign_evidence" / round_id / "duplicate_actions.json"
        atomic_write_json(report_path, {"duplicates": duplicates, "checked_at": utc_now()})
        evidence_id = self.manifest.record_file(round_id, "duplicate_action_check", report_path)
        if duplicates:
            raise RuntimeError(f"duplicate succeeded actions detected: {duplicates}")
        return evidence_id

    def _record_if_exists(self, round_id: str, label: str, path: Path) -> str | None:
        if not path.exists():
            return None
        return self.manifest.record_file(round_id, label, path)

    def _record_plan(self, round_id: str, command: str) -> str:
        plan_path = self.paths.campaign_home / "campaign_evidence" / round_id / "owner_command.txt"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(command + "\n", encoding="utf-8")
        return self.manifest.record_file(round_id, "owner_command", plan_path)

    def owner_command(self, start: str, end: str) -> str:
        script = REPO_ROOT / "scripts" / "run_life_campaign_30.ps1"
        return (
            f"& '{script}' -InstallRoot '{self.paths.install_root}' "
            f"-CampaignHome '{self.paths.campaign_home}' -StartRound {start} "
            f"-EndRound {end} -Execute"
        )


def expand_rounds(start: str | None, end: str | None, explicit: list[str] | None) -> list[str]:
    all_rounds = [f"R{index:02d}" for index in range(1, 31)]
    if explicit:
        for item in explicit:
            if item not in all_rounds:
                raise ValueError(f"unknown round {item}")
        return explicit
    start = start or "R01"
    end = end or "R05"
    start_index = all_rounds.index(start)
    end_index = all_rounds.index(end)
    if end_index < start_index:
        raise ValueError("end round must not precede start round")
    return all_rounds[start_index : end_index + 1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WLS 30-round life campaign")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    parser.add_argument("--live-home", type=Path)
    parser.add_argument("--campaign-home", type=Path, default=DEFAULT_CAMPAIGN_HOME)
    parser.add_argument("--wls-python", type=Path)
    parser.add_argument("--start-round", default="R01")
    parser.add_argument("--end-round", default="R05")
    parser.add_argument("--round", action="append", dest="rounds")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--fresh-snapshot", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    install_root = args.install_root.resolve()
    paths = CampaignPaths(
        install_root=install_root,
        live_home=(args.live_home or install_root / "home"),
        campaign_home=args.campaign_home,
        wls_python=(args.wls_python or install_root / "venv" / "Scripts" / "python.exe"),
    )
    round_ids = expand_rounds(args.start_round, args.end_round, args.rounds)
    try:
        runner = CampaignRunner(
            spec_path=args.spec,
            paths=paths,
            execute=args.execute,
            fresh_snapshot=args.fresh_snapshot,
        )
        result = runner.run(round_ids)
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
