from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
import argparse
import json
import shutil
import sqlite3
import time
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
SUPPORTED_AUTOMATED_ROUNDS = {f"R{index:02d}" for index in range(1, 16)}
LEVEL2_GOAL_PREFIXES = ("Clarify", "Inspect", "Learn", "Recover", "Preserve")


class CampaignRunner:
    def __init__(
        self,
        spec_path: Path,
        paths: CampaignPaths,
        execute: bool,
        fresh_snapshot: bool = False,
        authorize_level2: bool = False,
        r15_duration_seconds: int = 24 * 60 * 60,
        r15_heartbeat_seconds: int = 5 * 60,
    ):
        self.spec_path = spec_path.resolve()
        self.spec = load_json(self.spec_path)
        validate_campaign_spec(self.spec)
        self.paths = paths.resolve()
        self.paths.validate()
        self.execute = execute
        self.fresh_snapshot = fresh_snapshot
        self.authorize_level2 = authorize_level2
        self.r15_duration_seconds = r15_duration_seconds
        self.r15_heartbeat_seconds = r15_heartbeat_seconds
        self.state_path = self.paths.campaign_home / "campaign_state.json"
        self.lock_path = self.paths.campaign_home / ".campaign.lock"
        self.manifest = EvidenceManifest(self.paths.campaign_home)
        self.state = CampaignState.load_or_create(
            self.state_path, self.spec, self.paths
        )

    def reset_from_round(self, round_id: str, note: str) -> dict[str, Any]:
        with CampaignLock(self.lock_path):
            self.state.reset_from_round(round_id, note)
        return {
            "campaign_id": self.spec.get("campaign_id"),
            "state_path": str(self.state_path),
            "reset_from": round_id,
            "note": note,
        }

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
            "R06": self._round_06,
            "R07": self._round_07,
            "R08": self._round_08,
            "R09": self._round_09,
            "R10": self._round_10,
            "R11": self._round_11,
            "R12": self._round_12,
            "R13": self._round_13,
            "R14": self._round_14,
            "R15": self._round_15,
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

    def _round_06(self) -> dict[str, Any]:
        before = self._status_json("R06", "status_before_30_cycles")
        daemon = self._wls("R06", "daemon_30_cycles", ["daemon", "--max-cycles", "30"])
        after = self._status_json("R06", "status_after_30_cycles")
        verify = self._wls("R06", "verify", ["verify"])
        report = self._write_cycle_delta_report(
            "R06", before["payload"], after["payload"], expected_delta=30
        )
        for evidence_id in [before["evidence_id"], daemon, after["evidence_id"], verify, report]:
            self.state.append_evidence("R06", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [before["evidence_id"], daemon, after["evidence_id"], verify, report],
            "claim_ceiling": "30-cycle campaign clone auto-run only",
        }

    def _round_07(self) -> dict[str, Any]:
        before = self._status_json("R07", "status_before_100_cycles")
        db_before = self._db_measurements()
        daemon = self._wls("R07", "daemon_100_cycles", ["daemon", "--max-cycles", "100"])
        after = self._status_json("R07", "status_after_100_cycles")
        db_after = self._db_measurements()
        verify = self._wls("R07", "verify", ["verify"])
        report = self._write_measurement_report(
            "R07",
            "stability_100_cycles",
            {"before": before["payload"], "after": after["payload"], "db_before": db_before, "db_after": db_after},
        )
        self._assert_cycle_delta(before["payload"], after["payload"], 100)
        for evidence_id in [before["evidence_id"], daemon, after["evidence_id"], verify, report]:
            self.state.append_evidence("R07", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [before["evidence_id"], daemon, after["evidence_id"], verify, report],
            "claim_ceiling": "100-cycle campaign clone stability only",
        }

    def _round_08(self) -> dict[str, Any]:
        before = self._db_measurements()
        run = self._wls("R08", "daemon_100_cycle_growth_probe", ["daemon", "--max-cycles", "100"])
        after = self._db_measurements()
        verify = self._wls("R08", "verify", ["verify"])
        growth = {
            "events_delta": after["events"] - before["events"],
            "evidence_delta": after["evidence"] - before["evidence"],
            "actions_delta": after["actions"] - before["actions"],
            "cycles_delta": after["cycles"] - before["cycles"],
            "db_bytes_delta": after["db_bytes"] - before["db_bytes"],
            "warning_thresholds": {
                "events_per_cycle": 50,
                "evidence_records_per_cycle": 100,
                "db_bytes_per_cycle": 1024 * 1024,
            },
            "hard_stop_thresholds": {
                "events_per_cycle": 500,
                "evidence_records_per_cycle": 1000,
                "db_bytes_per_cycle": 10 * 1024 * 1024,
            },
        }
        cycles = max(1, growth["cycles_delta"])
        growth["rates"] = {
            "events_per_cycle": growth["events_delta"] / cycles,
            "evidence_records_per_cycle": growth["evidence_delta"] / cycles,
            "db_bytes_per_cycle": growth["db_bytes_delta"] / cycles,
        }
        report = self._write_measurement_report("R08", "evidence_growth_control", growth)
        for evidence_id in [run, verify, report]:
            self.state.append_evidence("R08", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [run, verify, report],
            "claim_ceiling": "growth measured in campaign clone; thresholds recorded, not live policy",
        }

    def _round_09(self) -> dict[str, Any]:
        original = self._load_campaign_config()
        faulted = self._with_extra_sensor(
            original,
            {
                "sensor_type": "http",
                "name": "campaign_fault_http",
                "enabled": True,
                "interval_seconds": 0.01,
                "settings": {
                    "endpoints": [{"name": "closed-local-port", "url": "http://127.0.0.1:9/"}],
                    "allowed_hosts": ["127.0.0.1"],
                    "timeout_seconds": 0.1,
                    "max_bytes": 128,
                },
            },
        )
        self._save_campaign_config(faulted)
        fault_config = self.manifest.record_file("R09", "faulted_config", self.paths.campaign_home / "config.json")
        fault_run = self._wls("R09", "faulted_sensor_once", ["once"])
        fault_state = self._service_health_report(
            "R09", "fault_service_health", "campaign_fault_http"
        )
        recovery_config = self._with_extra_sensor(
            original,
            {
                "sensor_type": "clock",
                "name": "campaign_fault_http",
                "enabled": True,
                "interval_seconds": 0.01,
                "settings": {"emit_hourly": False},
            },
        )
        self._save_campaign_config(recovery_config)
        restore_config = self.manifest.record_file("R09", "recovery_config", self.paths.campaign_home / "config.json")
        restore_run = self._wls("R09", "recovered_sensor_once", ["once"])
        recovered_state = self._sensor_state_report(
            "R09", "recovered_sensor_state", "campaign_fault_http", expect_error=False
        )
        self._save_campaign_config(original)
        final_config = self.manifest.record_file("R09", "restored_config", self.paths.campaign_home / "config.json")
        verify = self._wls("R09", "verify", ["verify"])
        for evidence_id in [
            fault_config,
            fault_run,
            fault_state,
            restore_config,
            restore_run,
            recovered_state,
            final_config,
            verify,
        ]:
            self.state.append_evidence("R09", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [
                fault_config,
                fault_run,
                fault_state,
                restore_config,
                restore_run,
                recovered_state,
                final_config,
                verify,
            ],
            "claim_ceiling": "campaign sensor failure visibility and config recovery only",
        }

    def _round_10(self) -> dict[str, Any]:
        restore_home = self.paths.campaign_home.parent / f"{self.paths.campaign_home.name}-restore-r10"
        if restore_home.exists():
            shutil.rmtree(restore_home)
        self._copy_campaign_home_to_restore(restore_home)
        restore_config_path = restore_home / "config.json"
        restore_config = load_json(restore_config_path)
        restore_config["home"] = str(restore_home.resolve())
        restore_config["tool_policy"] = {
            "allowed_read_roots": [str(restore_home.resolve()), str(restore_home.parent.resolve())],
            "allowed_write_roots": [str(restore_home / "sandbox"), str(restore_home / "outbox")],
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "allowed_commands": ["git", "python", "python.exe", "pytest", "pytest.exe"],
        }
        atomic_write_json(restore_config_path, restore_config)
        config_ev = self.manifest.record_file("R10", "restore_config", restore_config_path)
        status_ev = self._wls_with_config("R10", "restore_status", restore_config_path, restore_home, ["status"])
        verify_ev = self._wls_with_config("R10", "restore_verify", restore_config_path, restore_home, ["verify"])
        report = self._write_measurement_report(
            "R10",
            "backup_restore",
            {
                "source_home": str(self.paths.campaign_home),
                "restore_home": str(restore_home),
                "source_db": self._db_measurements(self.paths.campaign_home / "state" / "wls.db"),
                "restore_db": self._db_measurements(restore_home / "state" / "wls.db"),
            },
        )
        for evidence_id in [config_ev, status_ev, verify_ev, report]:
            self.state.append_evidence("R10", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [config_ev, status_ev, verify_ev, report],
            "claim_ceiling": "campaign backup restore only; restored home is disposable",
        }

    def _round_11(self) -> dict[str, Any]:
        original = self._load_campaign_config()
        results = {}
        evidence_ids = []
        for mode in ("disabled", "frozen"):
            config = self._profile_config(original, provider={"memory_mode": mode})
            self._save_campaign_config(config)
            evidence_ids.append(self.manifest.record_file("R11", f"memory_{mode}_config", self.paths.campaign_home / "config.json"))
            before = self._status_json("R11", f"status_before_memory_{mode}")
            run = self._wls("R11", f"once_memory_{mode}", ["once"])
            after = self._status_json("R11", f"status_after_memory_{mode}")
            evidence_ids.extend([before["evidence_id"], run, after["evidence_id"]])
            results[mode] = {"before": before["payload"], "after": after["payload"]}
        self._save_campaign_config(original)
        evidence_ids.append(self.manifest.record_file("R11", "memory_profile_restored_config", self.paths.campaign_home / "config.json"))
        verify = self._wls("R11", "verify", ["verify"])
        report = self._write_measurement_report("R11", "memory_disabled_vs_frozen", results)
        evidence_ids.extend([verify, report])
        for evidence_id in evidence_ids:
            self.state.append_evidence("R11", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": evidence_ids,
            "claim_ceiling": "profile A/B executed; no new learning benefit claim",
        }

    def _round_12(self) -> dict[str, Any]:
        original = self._load_campaign_config()
        results = {}
        evidence_ids = []
        for provider_type in ("deterministic", "cognitive"):
            config = self._profile_config(
                original,
                provider={
                    "type": provider_type,
                    "fallback": "deterministic",
                    "memory_mode": "frozen",
                },
            )
            self._save_campaign_config(config)
            evidence_ids.append(self.manifest.record_file("R12", f"{provider_type}_config", self.paths.campaign_home / "config.json"))
            before = self._status_json("R12", f"status_before_{provider_type}")
            run = self._wls("R12", f"once_{provider_type}", ["once"])
            after = self._status_json("R12", f"status_after_{provider_type}")
            evidence_ids.extend([before["evidence_id"], run, after["evidence_id"]])
            results[provider_type] = {"before": before["payload"], "after": after["payload"]}
        self._save_campaign_config(original)
        evidence_ids.append(self.manifest.record_file("R12", "planner_profile_restored_config", self.paths.campaign_home / "config.json"))
        verify = self._wls("R12", "verify", ["verify"])
        report = self._write_measurement_report("R12", "deterministic_vs_cognitive", results)
        evidence_ids.extend([verify, report])
        for evidence_id in evidence_ids:
            self.state.append_evidence("R12", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": evidence_ids,
            "claim_ceiling": "planner profile comparison executed; deterministic remains acceptable default",
        }

    def _round_13(self) -> dict[str, Any]:
        original = self._load_campaign_config()
        evidence_ids = []
        results = {}
        for label, max_actions in (("goal_frozen", 0), ("goal_active", 1)):
            config = self._profile_config(original)
            config["max_actions_per_cycle"] = max_actions
            config["max_autonomous_goals"] = 0
            self._save_campaign_config(config)
            evidence_ids.append(self.manifest.record_file("R13", f"{label}_config", self.paths.campaign_home / "config.json"))
            goal = self._wls("R13", f"add_goal_{label}", ["add-goal", f"Campaign {label} probe", "--description", "R13 profile comparison", "--criterion", "evidence remains traceable"])
            before = self._goal_progress_report("R13", f"{label}_before")
            run = self._wls("R13", f"once_{label}", ["once"])
            after = self._goal_progress_report("R13", f"{label}_after")
            evidence_ids.extend([goal, before, run, after])
            results[label] = {"max_actions_per_cycle": max_actions}
        self._save_campaign_config(original)
        evidence_ids.append(self.manifest.record_file("R13", "goal_profile_restored_config", self.paths.campaign_home / "config.json"))
        verify = self._wls("R13", "verify", ["verify"])
        report = self._write_measurement_report("R13", "goal_frozen_vs_active", results)
        evidence_ids.extend([verify, report])
        for evidence_id in evidence_ids:
            self.state.append_evidence("R13", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": evidence_ids,
            "claim_ceiling": "goal profile comparison only; LEVEL_2 still requires explicit Owner authorization",
        }

    def _round_14(self) -> dict[str, Any]:
        if not self.authorize_level2:
            return {
                "status": "OWNER_REVIEW",
                "reason": "R14 requires explicit LEVEL_2 Owner authorization",
                "claim_ceiling": "no endogenous goal admitted",
            }
        original = self._clean_campaign_config(self._load_campaign_config())
        active_before = self._autonomous_goal_count()
        if active_before > 1:
            raise RuntimeError(f"autonomous goal limit already occupied: {active_before}")
        evidence_ids: list[str] = []
        try:
            if active_before == 0:
                fault_module = self._write_r14_fault_sensor()
                fault_config = self._with_extra_sensor(
                    self._r14_profile_config(original),
                    {
                        "sensor_type": "campaign_r14_fault_sensor:CampaignR14FaultSensor",
                        "name": "campaign_r14_recoverable_sensor",
                        "enabled": True,
                        "interval_seconds": 0.01,
                        "settings": {},
                    },
                )
                self._save_campaign_config(fault_config)
                evidence_ids.extend(
                    [
                        self.manifest.record_file("R14", "fault_sensor_module", fault_module),
                        self.manifest.record_file("R14", "level2_fault_config", self.paths.campaign_home / "config.json"),
                    ]
                )
                trigger_run = self._wls("R14", "trigger_recoverable_sensor_failure", ["once"])
                trigger = self._sensor_state_report(
                    "R14", "endogenous_goal_trigger", "campaign_r14_recoverable_sensor"
                )
                goal_run = self._wls("R14", "autonomy_creates_endogenous_goal", ["once"])
                evidence_ids.extend([trigger_run, trigger, goal_run])
            else:
                evidence_ids.append(
                    self._write_measurement_report(
                        "R14",
                        "existing_single_autonomous_goal_reused",
                        {"active_autonomous_goal_count": active_before},
                    )
                )
            action_config = self._r14_profile_config(original)
            self._save_campaign_config(action_config)
            evidence_ids.append(
                self.manifest.record_file("R14", "level2_action_config", self.paths.campaign_home / "config.json")
            )
            action_run = self._wls("R14", "run_read_only_goal_action", ["once"])
            goal_report = self._r14_goal_report()
            action_report = self._latest_action_report("R14", "r14_resulting_action")
            evidence_ids.extend([action_run, goal_report, action_report])
            recovery_config = self._with_extra_sensor(
                self._r14_profile_config(original),
                {
                    "sensor_type": "clock",
                    "name": "campaign_r14_recoverable_sensor",
                    "enabled": True,
                    "interval_seconds": 0.01,
                    "settings": {"emit_hourly": False},
                },
            )
            self._save_campaign_config(recovery_config)
            recovery_run = self._wls("R14", "recover_sensor_state", ["once"])
            recovered_state = self._sensor_state_report(
                "R14", "recovered_goal_trigger_state", "campaign_r14_recoverable_sensor", expect_error=False
            )
            evidence_ids.extend([recovery_run, recovered_state])
        finally:
            self._save_campaign_config(original)
        restored_config = self.manifest.record_file("R14", "restored_config", self.paths.campaign_home / "config.json")
        verify = self._wls("R14", "verify", ["verify"])
        evidence_ids.extend([restored_config, verify])
        for evidence_id in evidence_ids:
            self.state.append_evidence("R14", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": evidence_ids,
            "claim_ceiling": "one campaign-clone endogenous read-only Goal admitted",
        }

    def _round_15(self) -> dict[str, Any]:
        if not self.authorize_level2:
            return {
                "status": "OWNER_REVIEW",
                "reason": "R15 requires LEVEL_2 authorization inherited from R14",
                "claim_ceiling": "24-hour validation not started",
            }
        run_path = self.paths.campaign_home / "campaign_evidence" / "R15" / "r15_run.json"
        run_path.parent.mkdir(parents=True, exist_ok=True)
        if run_path.exists():
            run_state = load_json(run_path)
        else:
            run_state = {
                "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "duration_seconds": self.r15_duration_seconds,
                "heartbeat_seconds": self.r15_heartbeat_seconds,
                "heartbeats": [],
                "restarts": [],
                "recoveries": [],
                "failures": [],
            }
            atomic_write_json(run_path, run_state)
        evidence_ids = [self.manifest.record_file("R15", "r15_run_state_initial", run_path)]
        started = datetime.fromisoformat(run_state["started_at"])
        deadline = started.timestamp() + int(run_state["duration_seconds"])
        heartbeat_index = len(run_state["heartbeats"])
        while time.time() < deadline:
            heartbeat_index += 1
            heartbeat_started = datetime.now(UTC).isoformat(timespec="seconds")
            try:
                before = self._status_json("R15", f"heartbeat_{heartbeat_index:04d}_status_before")
                once = self._wls("R15", f"heartbeat_{heartbeat_index:04d}_once", ["once"])
                after = self._status_json("R15", f"heartbeat_{heartbeat_index:04d}_status_after")
                verify = self._wls("R15", f"heartbeat_{heartbeat_index:04d}_verify", ["verify"])
                self._assert_autonomous_goal_limit(1)
                heartbeat = {
                    "index": heartbeat_index,
                    "started_at": heartbeat_started,
                    "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "before_cycle_count": before["payload"].get("cycle_count"),
                    "after_cycle_count": after["payload"].get("cycle_count"),
                    "evidence_ids": [before["evidence_id"], once, after["evidence_id"], verify],
                    "status": "PASS",
                }
                run_state["heartbeats"].append(heartbeat)
                run_state["restarts"].append(
                    {
                        "index": heartbeat_index,
                        "mode": "process-real CLI relaunch",
                        "evidence_ids": [once],
                    }
                )
                evidence_ids.extend(heartbeat["evidence_ids"])
            except Exception as exc:
                failure = {
                    "index": heartbeat_index,
                    "failed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                run_state["failures"].append(failure)
                atomic_write_json(run_path, run_state)
                self.manifest.record_file("R15", f"heartbeat_{heartbeat_index:04d}_failure_state", run_path)
                raise
            atomic_write_json(run_path, run_state)
            evidence_ids.append(
                self.manifest.record_file("R15", f"heartbeat_{heartbeat_index:04d}_run_state", run_path)
            )
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(max(1, self.r15_heartbeat_seconds), remaining))
        final_verify = self._wls("R15", "final_verify", ["verify"])
        summary = self._write_measurement_report(
            "R15",
            "minimum_life_24h_summary",
            {
                "started_at": run_state["started_at"],
                "ended_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "duration_seconds_required": run_state["duration_seconds"],
                "heartbeat_count": len(run_state["heartbeats"]),
                "restart_count": len(run_state["restarts"]),
                "failure_count": len(run_state["failures"]),
            },
        )
        evidence_ids.extend([final_verify, summary])
        for evidence_id in evidence_ids:
            self.state.append_evidence("R15", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": evidence_ids,
            "claim_ceiling": "real elapsed minimum-life validation in campaign clone",
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
        return self._wls_with_config(round_id, label, config_path, self.paths.campaign_home, command)

    def _wls_with_config(
        self, round_id: str, label: str, config_path: Path, cwd: Path, command: list[str]
    ) -> str:
        args = [str(self.paths.wls_python), "-m", "wls", "--config", str(config_path), *command]
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        # argv is built from resolved paths and fixed WLS subcommands; shell stays false.
        result = subprocess.run(  # nosec B603
            args,
            cwd=cwd,
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
            cwd,
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
                    SELECT
                        idempotency_key,
                        COUNT(*) AS row_count,
                        SUM(CASE WHEN started_at IS NOT NULL THEN 1 ELSE 0 END)
                            AS execution_count
                    FROM actions
                    WHERE status='SUCCEEDED' AND idempotency_key IS NOT NULL
                    GROUP BY idempotency_key
                    HAVING execution_count > 1
                    """
                ).fetchall()
                duplicates = [dict(row) for row in rows]
                reuse_rows = connection.execute(
                    """
                    SELECT action_id, idempotency_key, started_at, finished_at
                    FROM actions
                    WHERE status='SUCCEEDED'
                      AND idempotency_key IS NOT NULL
                      AND started_at IS NULL
                    ORDER BY rowid
                    """
                ).fetchall()
                reused = [dict(row) for row in reuse_rows]
            except sqlite3.Error as exc:
                duplicates = [{"sqlite_error": str(exc)}]
                reused = []
            finally:
                connection.close()
        report_path = self.paths.campaign_home / "campaign_evidence" / round_id / "duplicate_actions.json"
        atomic_write_json(
            report_path,
            {
                "duplicates": duplicates,
                "idempotent_reuse_rows": reused if "reused" in locals() else [],
                "checked_at": utc_now(),
            },
        )
        evidence_id = self.manifest.record_file(round_id, "duplicate_action_check", report_path)
        if duplicates:
            raise RuntimeError(f"duplicate succeeded actions detected: {duplicates}")
        return evidence_id

    def _record_if_exists(self, round_id: str, label: str, path: Path) -> str | None:
        if not path.exists():
            return None
        return self.manifest.record_file(round_id, label, path)

    def _load_campaign_config(self) -> dict[str, Any]:
        return load_json(self.paths.campaign_home / "config.json")

    def _save_campaign_config(self, config: dict[str, Any]) -> None:
        atomic_write_json(self.paths.campaign_home / "config.json", config)

    def _profile_config(
        self, base: dict[str, Any], provider: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        config = json.loads(json.dumps(base))
        config["home"] = str(self.paths.campaign_home)
        config["read_only"] = True
        config["allow_autonomous_reversible_writes"] = False
        merged_provider = dict(config.get("provider", {}))
        if provider:
            merged_provider.update(provider)
        config["provider"] = merged_provider
        return config

    def _r14_profile_config(self, base: dict[str, Any]) -> dict[str, Any]:
        config = self._profile_config(base)
        config["max_autonomous_goals"] = 1
        config["max_actions_per_cycle"] = 4
        provider = dict(config.get("provider", {}))
        provider["type"] = "deterministic"
        provider["fallback"] = "deterministic"
        provider["memory_mode"] = "frozen"
        config["provider"] = provider
        return config

    def _clean_campaign_config(self, config: dict[str, Any]) -> dict[str, Any]:
        cleaned = json.loads(json.dumps(config))
        cleaned["home"] = str(self.paths.campaign_home)
        cleaned["read_only"] = True
        cleaned["allow_autonomous_reversible_writes"] = False
        cleaned["max_autonomous_goals"] = min(int(cleaned.get("max_autonomous_goals", 0)), 1)
        cleaned["sensors"] = [
            sensor
            for sensor in cleaned.get("sensors", [])
            if not str(sensor.get("name", "")).startswith("campaign_")
        ]
        return cleaned

    def _with_extra_sensor(self, config: dict[str, Any], sensor: dict[str, Any]) -> dict[str, Any]:
        result = json.loads(json.dumps(config))
        sensors = [item for item in result.get("sensors", []) if item.get("name") != sensor["name"]]
        sensors.append(sensor)
        result["sensors"] = sensors
        return result

    def _db_measurements(self, db_path: Path | None = None) -> dict[str, Any]:
        db_path = db_path or (self.paths.campaign_home / "state" / "wls.db")
        measurements = {"db_path": str(db_path), "db_bytes": db_path.stat().st_size if db_path.exists() else 0}
        if not db_path.exists():
            return {**measurements, "events": 0, "evidence": 0, "actions": 0, "cycles": 0}
        connection = sqlite3.connect(db_path)
        try:
            table_counts = {
                "events": "SELECT COUNT(*) FROM events",
                "evidence": "SELECT COUNT(*) FROM evidence",
                "actions": "SELECT COUNT(*) FROM actions",
                "cycles": "SELECT COUNT(*) FROM cycles",
                "goals": "SELECT COUNT(*) FROM goals",
            }
            for table, query in table_counts.items():
                try:
                    row = connection.execute(query).fetchone()
                    measurements[table] = int(row[0]) if row else 0
                except sqlite3.Error:
                    measurements[table] = 0
        finally:
            connection.close()
        return measurements

    def _assert_cycle_delta(self, before: dict[str, Any], after: dict[str, Any], expected_delta: int) -> None:
        before_count = int(before.get("cycle_count", 0))
        after_count = int(after.get("cycle_count", 0))
        if after_count < before_count + expected_delta:
            raise RuntimeError(
                f"expected at least {expected_delta} cycles, got {after_count - before_count}"
            )

    def _write_cycle_delta_report(
        self, round_id: str, before: dict[str, Any], after: dict[str, Any], expected_delta: int
    ) -> str:
        self._assert_cycle_delta(before, after, expected_delta)
        return self._write_measurement_report(
            round_id,
            "cycle_delta",
            {
                "before_cycle_count": before.get("cycle_count", 0),
                "after_cycle_count": after.get("cycle_count", 0),
                "expected_delta": expected_delta,
            },
        )

    def _write_measurement_report(self, round_id: str, label: str, payload: dict[str, Any]) -> str:
        report_path = self.paths.campaign_home / "campaign_evidence" / round_id / f"{label}.json"
        atomic_write_json(report_path, {"label": label, "recorded_at": utc_now(), **payload})
        return self.manifest.record_file(round_id, label, report_path)

    def _write_r14_fault_sensor(self) -> Path:
        module_path = self.paths.campaign_home / "campaign_r14_fault_sensor.py"
        module_path.write_text(
            "\n".join(
                [
                    "from __future__ import annotations",
                    "from wls.sensors.base import Sensor",
                    "",
                    "class CampaignR14FaultSensor(Sensor):",
                    "    def poll(self, previous_state):",
                    "        raise RuntimeError('campaign R14 recoverable sensor fault')",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return module_path

    def _autonomous_goal_count(self) -> int:
        db_path = self.paths.campaign_home / "state" / "wls.db"
        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM goals WHERE autonomous=1 AND status IN ('ACTIVE','BLOCKED')"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            connection.close()

    def _assert_autonomous_goal_limit(self, limit: int) -> None:
        count = self._autonomous_goal_count()
        if count > limit:
            raise RuntimeError(f"autonomous goal limit exceeded: {count} > {limit}")

    def _r14_goal_report(self) -> str:
        db_path = self.paths.campaign_home / "state" / "wls.db"
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT goal_id,title,description,priority,source,autonomous,status,progress,created_at,updated_at
                    FROM goals WHERE autonomous=1
                    ORDER BY rowid DESC
                    """
                )
            ]
        finally:
            connection.close()
        if len(rows) != 1:
            raise RuntimeError(f"expected exactly one autonomous goal, found {len(rows)}")
        title = str(rows[0]["title"])
        if not title.startswith(LEVEL2_GOAL_PREFIXES):
            raise RuntimeError(f"autonomous goal has invalid prefix: {title}")
        return self._write_measurement_report(
            "R14",
            "endogenous_goal",
            {
                "goal": rows[0],
                "trigger": "real WLS internal state selected by AutonomySystem",
                "allowed_goal_prefixes": list(LEVEL2_GOAL_PREFIXES),
                "max_autonomous_goals": 1,
            },
        )

    def _latest_action_report(self, round_id: str, label: str) -> str:
        db_path = self.paths.campaign_home / "state" / "wls.db"
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """
                SELECT action_id,plan_id,goal_id,tool,purpose,risk,status,side_effect_class,
                       started_at,finished_at,result_json,error
                FROM actions ORDER BY rowid DESC LIMIT 1
                """
            ).fetchone()
            action = dict(row) if row else None
        finally:
            connection.close()
        if not action:
            raise RuntimeError("expected resulting R14 action")
        if action["side_effect_class"] != "none" and str(action["risk"]).upper() != "READ":
            raise RuntimeError(f"R14 action is not read-only: {action}")
        return self._write_measurement_report(round_id, label, {"action": action})

    def _sensor_state_report(
        self, round_id: str, label: str, sensor_name: str, expect_error: bool = True
    ) -> str:
        db_path = self.paths.campaign_home / "state" / "wls.db"
        row_payload: dict[str, Any] | None = None
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT * FROM sensor_state WHERE sensor_name=?", (sensor_name,)
            ).fetchone()
            row_payload = dict(row) if row else None
        finally:
            connection.close()
        has_error = bool(row_payload and row_payload.get("last_error"))
        if expect_error and not has_error:
            raise RuntimeError(f"expected recorded sensor failure for {sensor_name}")
        if not expect_error and has_error:
            raise RuntimeError(f"expected recovered sensor state for {sensor_name}")
        return self._write_measurement_report(round_id, label, {"sensor_state": row_payload})

    def _service_health_report(self, round_id: str, label: str, source: str) -> str:
        db_path = self.paths.campaign_home / "state" / "wls.db"
        matches: list[dict[str, Any]] = []
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT event_type,payload_json FROM events
                WHERE event_type='observation.service_health'
                ORDER BY rowid DESC LIMIT 50
                """
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                observation = payload.get("observation", {})
                value = observation.get("value", {})
                if observation.get("source") == source and value.get("healthy") is False:
                    matches.append({"event_type": row["event_type"], "payload": payload})
        finally:
            connection.close()
        if not matches:
            raise RuntimeError(f"expected unhealthy service_health observation from {source}")
        return self._write_measurement_report(round_id, label, {"matches": matches})

    def _goal_progress_report(self, round_id: str, label: str) -> str:
        db_path = self.paths.campaign_home / "state" / "wls.db"
        rows: list[dict[str, Any]] = []
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            for row in connection.execute(
                "SELECT goal_id,title,status,progress,source,autonomous FROM goals ORDER BY rowid DESC LIMIT 20"
            ):
                rows.append(dict(row))
        finally:
            connection.close()
        return self._write_measurement_report(round_id, label, {"goals": rows})

    def _copy_campaign_home_to_restore(self, restore_home: Path) -> None:
        restore_home.mkdir(parents=True, exist_ok=True)
        for source in self.paths.campaign_home.rglob("*"):
            relative = source.relative_to(self.paths.campaign_home)
            if relative.parts and relative.parts[0] == "campaign_evidence":
                continue
            target = restore_home / relative
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
        source_db = self.paths.campaign_home / "state" / "wls.db"
        target_db = restore_home / "state" / "wls.db"
        target_db.parent.mkdir(parents=True, exist_ok=True)
        source_connection = sqlite3.connect(source_db)
        target_connection = sqlite3.connect(target_db)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()

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
    parser.add_argument("--repair-round")
    parser.add_argument("--repair-note", default="candidate branch repair")
    parser.add_argument("--authorize-level2", action="store_true")
    parser.add_argument("--r15-duration-seconds", type=int, default=24 * 60 * 60)
    parser.add_argument("--r15-heartbeat-seconds", type=int, default=5 * 60)
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
            authorize_level2=args.authorize_level2,
            r15_duration_seconds=args.r15_duration_seconds,
            r15_heartbeat_seconds=args.r15_heartbeat_seconds,
        )
        if args.repair_round:
            result = runner.reset_from_round(args.repair_round, args.repair_note)
        else:
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
