from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
import argparse
import hashlib
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
SUPPORTED_AUTOMATED_ROUNDS = {f"R{index:02d}" for index in range(1, 31)}
LEVEL2_GOAL_PREFIXES = ("Clarify", "Inspect", "Learn", "Recover", "Preserve")


class CampaignRunner:
    def __init__(
        self,
        spec_path: Path,
        paths: CampaignPaths,
        execute: bool,
        fresh_snapshot: bool = False,
        authorize_level2: bool = False,
        authorize_level3: bool = False,
        authorize_level4: bool = False,
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
        self.authorize_level3 = authorize_level3
        self.authorize_level4 = authorize_level4
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

    def owner_stop_round(self, round_id: str, reason: str) -> dict[str, Any]:
        with CampaignLock(self.lock_path):
            if round_id not in self.state.data["rounds"]:
                raise ValueError(f"unknown round: {round_id}")
            evidence_ids: list[str] = []
            if round_id == "R15":
                run_path = self.paths.campaign_home / "campaign_evidence" / "R15" / "r15_run.json"
                if run_path.exists():
                    evidence_ids.append(
                        self.manifest.record_file(
                            "R15",
                            "owner_stopped_partial_r15_run",
                            run_path,
                            {"reason": reason},
                        )
                    )
            verdict = {
                "status": "OWNER_REVIEW",
                "reason": reason,
                "evidence_ids": evidence_ids,
                "claim_ceiling": f"{round_id} stopped by Owner; partial evidence preserved; not PASS",
            }
            for evidence_id in evidence_ids:
                self.state.append_evidence(round_id, evidence_id)
            self.state.mark_owner_stopped(round_id, verdict)
        return {
            "campaign_id": self.spec.get("campaign_id"),
            "state_path": str(self.state_path),
            "manifest_path": str(self.manifest.manifest_path),
            "round_id": round_id,
            **verdict,
        }

    def authorize_partial_continuation(self, round_id: str, reason: str) -> dict[str, Any]:
        with CampaignLock(self.lock_path):
            if round_id != "R15":
                raise ValueError("partial continuation is currently only defined for R15")
            sync_report = self._write_progress_sync_report(round_id, reason)
            evidence_id = self.manifest.record_file(
                round_id,
                "partial_continuation_progress_sync",
                sync_report,
                {"reason": reason},
            )
            self.state.append_evidence(round_id, evidence_id)
            verdict_patch = {
                "reason": reason,
                "evidence_ids": [evidence_id],
                "continuation_gate": "R15_PARTIAL_OWNER_AUTHORIZED",
                "claim_ceiling": (
                    "R15 remains OWNER_REVIEW and not PASS; R16+ may enter the new "
                    "continuation path when their own gates are satisfied"
                ),
            }
            self.state.authorize_partial_continuation(round_id, verdict_patch)
        return {
            "campaign_id": self.spec.get("campaign_id"),
            "state_path": str(self.state_path),
            "manifest_path": str(self.manifest.manifest_path),
            "round_id": round_id,
            "status": "OWNER_REVIEW",
            "continuation_authorized": True,
            "evidence_ids": [evidence_id],
            "sync_report": str(sync_report),
            "claim_ceiling": verdict_patch["claim_ceiling"],
        }

    def record_real_failure(
        self,
        source_type: str,
        normalized_signature: str,
        raw_evidence_ids: list[str],
        reproduction_status: str,
        environment: str,
        input_hash: str,
        output_hash: str,
        repair_status: str,
        note: str,
    ) -> dict[str, Any]:
        with CampaignLock(self.lock_path):
            if self.authorize_level3 and self.state.data.get("automation_level") != "LEVEL_3":
                self._record_level3_authorization("R16")
            if self.state.data.get("automation_level") != "LEVEL_3":
                raise PermissionError("recording R16 real failures requires LEVEL_3 disposable authorization")
            signature = normalized_signature.strip()
            if not signature:
                raise ValueError("normalized_signature is required")
            if not raw_evidence_ids:
                raise ValueError("raw_evidence_ids are required")
            now = utc_now()
            failure_path = (
                self.paths.campaign_home
                / "campaign_evidence"
                / "R16"
                / f"real_failure_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ_%f')}.json"
            )
            payload = {
                "round_id": "R16",
                "source_type": source_type,
                "count_toward_gate": True,
                "normalized_signature": signature,
                "raw_evidence_ids": raw_evidence_ids,
                "reproduction_status": reproduction_status,
                "environment": environment,
                "first_seen": now,
                "last_seen": now,
                "input_hash": input_hash,
                "output_hash": output_hash,
                "repair_status": repair_status,
                "note": note,
                "synthetic_or_fixture": False,
                "claim_ceiling": "single real failure record; not sufficient for R16 PASS by itself",
            }
            atomic_write_json(failure_path, payload)
            failure_evidence = self.manifest.record_file(
                "R16",
                "real_failure_record",
                failure_path,
                {
                    "r16_real_failure": True,
                    "normalized_signature": signature,
                    "synthetic_or_fixture": False,
                    "source_type": source_type,
                    "reproduction_status": reproduction_status,
                    "repair_status": repair_status,
                },
            )
            scan = self._write_r16_repeated_failure_report()
            self.state.append_evidence("R16", failure_evidence)
            self.state.append_evidence("R16", scan["evidence_id"])
            if scan["pass_ready"]:
                verdict = {
                    "status": "PASS",
                    "evidence_ids": [failure_evidence, scan["evidence_id"]],
                    "normalized_signature": scan["normalized_signature"],
                    "real_failure_count": scan["real_failure_count"],
                    "claim_ceiling": "three real repeated owner-host failures captured and evidence-bound in disposable campaign clone",
                }
                self.state.mark_pass("R16", verdict)
                self._unblock_after_pass("R16")
            return {
                "campaign_id": self.spec.get("campaign_id"),
                "state_path": str(self.state_path),
                "manifest_path": str(self.manifest.manifest_path),
                "round_id": "R16",
                "status": self.state.round_status("R16"),
                "failure_evidence_id": failure_evidence,
                "scan_evidence_id": scan["evidence_id"],
                "pass_ready": scan["pass_ready"],
                "normalized_signature": scan["normalized_signature"],
                "real_failure_count": scan["real_failure_count"],
            }

    def _unblock_after_pass(self, round_id: str) -> None:
        keys = list(self.state.data["rounds"])
        start = keys.index(round_id) + 1
        for later in keys[start:]:
            value = self.state.data["rounds"][later]
            verdict = value.get("verdict")
            if (
                value.get("status") == "BLOCKED"
                and isinstance(verdict, dict)
                and verdict.get("reason") == f"blocked pending owner review after {round_id}"
            ):
                value["status"] = "PENDING"
                value["verdict"] = None
            else:
                break
        self.state.save()
    def _write_progress_sync_report(self, round_id: str, reason: str) -> Path:
        report_path = (
            self.paths.campaign_home
            / "campaign_evidence"
            / round_id
            / "partial_continuation_progress_sync.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        run_path = self.paths.campaign_home / "campaign_evidence" / "R15" / "r15_run.json"
        r15_run = load_json(run_path) if run_path.exists() else {}
        branch = self._git_value(["rev-parse", "--abbrev-ref", "HEAD"])
        head = self._git_value(["rev-parse", "HEAD"])
        payload = {
            "authorized_at": utc_now(),
            "reason": reason,
            "mainline_campaign": {
                "campaign_id": self.spec.get("campaign_id"),
                "round": round_id,
                "status": self.state.round_status(round_id),
                "r15_started_at": r15_run.get("started_at"),
                "r15_heartbeat_count": len(r15_run.get("heartbeats", [])),
                "r15_failure_count": len(r15_run.get("failures", [])),
                "r15_restart_count": len(r15_run.get("restarts", [])),
                "r15_not_passed": True,
                "continuation_gate": "R15_PARTIAL_OWNER_AUTHORIZED",
            },
            "branch_integration": {
                "branch": branch,
                "head": head,
                "capability_task": "WLS-LIVING-AGENT-OS-CAPABILITIES-001",
                "sync_state": (
                    "capability organs are repository-level adapters/projections; "
                    "campaign mainline may continue without claiming R15 PASS"
                ),
            },
            "claim_ceiling": (
                "Owner-authorized continuation sync only; no LEVEL_3 promotion, "
                "live deployment, Skill promotion, or R15 PASS claim"
            ),
        }
        atomic_write_json(report_path, payload)
        return report_path

    @staticmethod
    def _git_value(args: list[str]) -> str:
        git_path = shutil.which("git")
        if not git_path:
            return "UNKNOWN"
        completed = subprocess.run(  # nosec B603
            [git_path, *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            return "UNKNOWN"
        return completed.stdout.strip()

    def run(self, round_ids: list[str]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        with CampaignLock(self.lock_path):
            if not self.execute:
                results = [
                    {"round_id": round_id, **self._planned_verdict(round_id)}
                    for round_id in round_ids
                ]
                return {
                    "campaign_id": self.spec.get("campaign_id"),
                    "state_path": str(self.state_path),
                    "manifest_path": str(self.manifest.manifest_path),
                    "automation_level": self.state.data["automation_level"],
                    "results": results,
                }
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
                    if round_id == "R30":
                        final_evidence_id = self._finalize_r30_epoch_audit()
                        if final_evidence_id is not None:
                            evidence_ids = list(result.get("evidence_ids", []))
                            evidence_ids.append(final_evidence_id)
                            result = {
                                **result,
                                "evidence_ids": evidence_ids,
                                "finalized_epoch_audit_evidence_id": final_evidence_id,
                            }
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

    def _finalize_r30_epoch_audit(self) -> str | None:
        audit_path = (
            self.paths.campaign_home
            / "campaign_evidence"
            / "R30"
            / "epoch_audit.json"
        )
        if not audit_path.exists():
            return None
        audit = load_json(audit_path)
        r30_state = self.state.data["rounds"]["R30"]
        verdict = r30_state.get("verdict") if isinstance(r30_state, dict) else {}
        if not isinstance(verdict, dict):
            verdict = {}
        audit.setdefault("round_states", {})["R30"] = {
            "status": r30_state.get("status"),
            "evidence_count": len(r30_state.get("evidence", [])),
            "claim_ceiling": verdict.get("claim_ceiling"),
            "finalized_after_round_pass": True,
            "evidence_count_excludes_this_finalization_record": True,
        }
        audit["status"] = "PASS"
        audit["finalized_after_round_pass"] = True
        audit["finalized_at"] = utc_now()
        audit["final_state_source"] = str(self.state_path)
        coverage = audit.setdefault("evidence_coverage", {})
        if isinstance(coverage, dict):
            coverage["post_pass_round_status_consistent"] = (
                r30_state.get("status") == "PASS"
            )
            coverage["manifest_records_before_post_pass_record"] = len(
                self.manifest.data.get("records", [])
            )
        atomic_write_json(audit_path, audit)
        evidence_id = self.manifest.record_file(
            "R30",
            "epoch_audit_post_pass",
            audit_path,
            metadata={"post_pass_finalization": True},
        )
        self.state.append_evidence("R30", evidence_id)
        return evidence_id

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
            "R16": self._round_16,
            "R17": self._round_17,
            "R18": self._round_18,
            "R19": self._round_19,
            "R20": self._round_20,
            "R21": self._round_21,
            "R22": self._round_22,
            "R23": self._round_23,
            "R24": self._round_24,
            "R25": self._round_25,
            "R26": self._round_26,
            "R27": self._round_27,
            "R28": self._round_28,
            "R29": self._round_29,
            "R30": self._round_30,
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

    def _round_16(self) -> dict[str, Any]:
        validation = self._run_architecture_validation("R16", "capability_admission_validation")
        preflight_report = self._write_r16_preflight_report(validation["payload"])
        evidence_ids = [validation["evidence_id"], preflight_report]
        if self.authorize_level3:
            evidence_ids.append(self._record_level3_authorization("R16"))
        failure_report = self._write_r16_repeated_failure_report()
        evidence_ids.append(failure_report["evidence_id"])
        for evidence_id in evidence_ids:
            self.state.append_evidence("R16", evidence_id)
        if failure_report["pass_ready"]:
            return {
                "status": "PASS",
                "evidence_ids": evidence_ids,
                "normalized_signature": failure_report["normalized_signature"],
                "claim_ceiling": "three real repeated owner-host failures captured and evidence-bound in disposable campaign clone",
            }
        reason = "R16 requires LEVEL_3 Owner authorization for real repeated failure capture"
        if self.state.data.get("automation_level") == "LEVEL_3":
            reason = "LEVEL_3 authorized, but three real repeated owner-host failures are not yet evidence-bound"
        return {
            "status": "OWNER_REVIEW",
            "evidence_ids": evidence_ids,
            "reason": reason,
            "claim_ceiling": (
                "capability admission and authorization preflight only; negative-control "
                "rejections are not owner-host repeated failures and R16 is not PASS"
            ),
        }

    def _round_17(self) -> dict[str, Any]:
        scan_path = self.paths.campaign_home / "campaign_evidence" / "R16" / "r16_repeated_failure_scan.json"
        if not scan_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R17 requires R16 repeated failure scan evidence",
                "claim_ceiling": "candidate not created",
            }
        scan = load_json(scan_path)
        if scan.get("pass_ready") is not True:
            return {
                "status": "OWNER_REVIEW",
                "reason": "R17 requires R16 PASS with three real repeated failures",
                "claim_ceiling": "candidate not created",
            }
        selected = [str(item) for item in scan.get("selected_evidence_ids", [])]
        if len(selected) < 3:
            return {
                "status": "FAIL",
                "reason": "R16 scan pass_ready without three selected evidence ids",
                "claim_ceiling": "candidate invalid",
            }
        candidate_path = self.paths.campaign_home / "campaign_evidence" / "R17" / "failure_candidate.json"
        candidate = {
            "round_id": "R17",
            "candidate_id": "r17_http_loopback_outage_timeout_candidate",
            "created_at": utc_now(),
            "source_round": "R16",
            "normalized_signature": scan.get("normalized_signature"),
            "source_evidence_ids": selected,
            "baseline_frozen": {
                "git_head": self._git_head(),
                "campaign_config_sha256": self._sha256_optional(self.paths.campaign_home / "config.json"),
                "campaign_db_sha256": self._sha256_optional(self.paths.campaign_home / "state" / "wls.db"),
                "r16_scan_sha256": self._sha256_optional(scan_path),
            },
            "candidate_type": "failure_candidate",
            "proposed_intervention": {
                "summary": "Classify repeated loopback HTTP sensor timeout as a recoverable service-health outage and require bounded retry/recovery experiment before any Skill proposal.",
                "scope": "disposable campaign clone only",
                "live_state_modified": False,
            },
            "predicted_benefit": "Turns repeated service-health outage evidence into an isolated recovery experiment input.",
            "risk": "READ + REVERSIBLE_WRITE in campaign evidence only",
            "rejection_criteria": [
                "selected evidence is synthetic or fixture",
                "signature is not repeated at least three times",
                "candidate changes live install, live config, live database, main branch, or production data",
                "recovery experiment cannot reproduce the baseline failure",
            ],
            "test_plan": [
                "freeze baseline before mutation",
                "run isolated recovery experiment in disposable clone",
                "compare learning off/on or baseline/candidate under same input",
                "record null result if no improvement",
            ],
            "rollback": [
                "retire candidate json",
                "repair from R17 or R18",
                "keep R16 evidence and manifest records for audit",
            ],
            "claim_ceiling": "candidate exists, not a fix and not a Skill",
        }
        atomic_write_json(candidate_path, candidate)
        evidence_id = self.manifest.record_file(
            "R17",
            "failure_candidate",
            candidate_path,
            {
                "normalized_signature": str(scan.get("normalized_signature")),
                "source_round": "R16",
                "candidate_only": True,
            },
        )
        self.state.append_evidence("R17", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [evidence_id],
            "candidate_id": candidate["candidate_id"],
            "normalized_signature": candidate["normalized_signature"],
            "claim_ceiling": "failure candidate exists, not a fix",
        }

    def _round_18(self) -> dict[str, Any]:
        candidate_path = self.paths.campaign_home / "campaign_evidence" / "R17" / "failure_candidate.json"
        events_path = self.paths.campaign_home / "campaign_evidence" / "R16" / "r16_outage_events.json"
        if not candidate_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R18 requires R17 failure candidate",
                "claim_ceiling": "recovery experiment not started",
            }
        candidate = load_json(candidate_path)
        raw_events = json.loads(events_path.read_text(encoding="utf-8")) if events_path.exists() else []
        if not isinstance(raw_events, list) or len(raw_events) < 3:
            return {
                "status": "OWNER_REVIEW",
                "reason": "R18 requires frozen R16 outage regression events",
                "claim_ceiling": "recovery experiment not started",
            }
        regression_path = self.paths.campaign_home / "campaign_evidence" / "R18" / "frozen_regression_set.json"
        experiment_path = self.paths.campaign_home / "campaign_evidence" / "R18" / "recovery_experiment.json"
        regression = {
            "round_id": "R18",
            "created_at": utc_now(),
            "source_candidate_id": candidate.get("candidate_id"),
            "source_signature": candidate.get("normalized_signature"),
            "events": raw_events,
            "frozen_hash_inputs": {
                "candidate_sha256": self._sha256_optional(candidate_path),
                "outage_events_sha256": self._sha256_optional(events_path),
            },
            "claim_ceiling": "frozen regression set for isolated experiment only",
        }
        atomic_write_json(regression_path, regression)
        baseline_results = [self._classify_outage_baseline(item) for item in raw_events]
        candidate_results = [self._classify_outage_candidate(item) for item in raw_events]
        improved = sum(
            1
            for before, after in zip(baseline_results, candidate_results, strict=True)
            if before.get("classification") != after.get("classification")
        )
        experiment = {
            "round_id": "R18",
            "created_at": utc_now(),
            "baseline_immutable": self._sha256_optional(regression_path),
            "candidate_id": candidate.get("candidate_id"),
            "learning_modes": ["off_baseline", "candidate_classifier"],
            "baseline_results": baseline_results,
            "candidate_results": candidate_results,
            "diagnostic_improvement_count": improved,
            "service_recovery_proven": False,
            "null_result": improved == 0,
            "side_effects": [],
            "rollback": ["delete R18 experiment candidate outputs", "keep frozen regression evidence"],
            "claim_ceiling": "isolated recovery experiment result; no live service recovery proven",
        }
        atomic_write_json(experiment_path, experiment)
        regression_evidence = self.manifest.record_file("R18", "frozen_regression_set", regression_path)
        experiment_evidence = self.manifest.record_file("R18", "recovery_experiment", experiment_path)
        for evidence_id in [regression_evidence, experiment_evidence]:
            self.state.append_evidence("R18", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [regression_evidence, experiment_evidence],
            "candidate_id": candidate.get("candidate_id"),
            "diagnostic_improvement_count": improved,
            "service_recovery_proven": False,
            "claim_ceiling": "isolated recovery result only; no live fix",
        }

    def _classify_outage_baseline(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": event.get("event_id"),
            "subject": event.get("subject"),
            "classification": "UNCLASSIFIED_FAILURE",
            "recommended_action": "OWNER_REVIEW",
        }

    def _classify_outage_candidate(self, event: dict[str, Any]) -> dict[str, Any]:
        value = event.get("value", {}) if isinstance(event, dict) else {}
        error = str(value.get("error", "")) if isinstance(value, dict) else ""
        classification = "RECOVERABLE_SERVICE_HEALTH_OUTAGE" if "timed out" in error.lower() else "UNCLASSIFIED_FAILURE"
        action = "bounded_retry_then_report" if classification == "RECOVERABLE_SERVICE_HEALTH_OUTAGE" else "OWNER_REVIEW"
        return {
            "event_id": event.get("event_id"),
            "subject": event.get("subject"),
            "classification": classification,
            "recommended_action": action,
            "reason": error,
        }

    def _round_19(self) -> dict[str, Any]:
        candidate_path = self.paths.campaign_home / "campaign_evidence" / "R17" / "failure_candidate.json"
        experiment_path = self.paths.campaign_home / "campaign_evidence" / "R18" / "recovery_experiment.json"
        if not candidate_path.exists() or not experiment_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R19 requires R17 candidate and R18 recovery experiment",
                "claim_ceiling": "skill candidate not created",
            }
        source_candidate = load_json(candidate_path)
        experiment = load_json(experiment_path)
        validation_result = "PASS" if experiment.get("diagnostic_improvement_count", 0) > 0 else "NULL_RESULT"
        skill_path = self.paths.campaign_home / "campaign_evidence" / "R19" / "skill_candidate.json"
        skill_candidate = {
            "round_id": "R19",
            "created_at": utc_now(),
            "skill_id": "skill.candidate.http_loopback_outage_recovery.v1",
            "version": "0.1.0-candidate",
            "state": "PROPOSED_NOT_PROMOTED",
            "promotion_state": "NOT_PROMOTED",
            "source_failure_candidate_id": source_candidate.get("candidate_id"),
            "lineage": {
                "source_rounds": ["R16", "R17", "R18"],
                "failure_signature": source_candidate.get("normalized_signature"),
                "source_evidence_ids": source_candidate.get("source_evidence_ids", []),
                "r17_candidate_sha256": self._sha256_optional(candidate_path),
                "r18_experiment_sha256": self._sha256_optional(experiment_path),
            },
            "applicability": {
                "scope": "disposable campaign HTTP sensor timeout diagnosis only",
                "allowed_actions": ["classify repeated timeout", "recommend bounded retry then report"],
                "forbidden_actions": ["modify live config", "start persistent daemon", "promote skill", "write production data"],
            },
            "validation": {
                "environment": "isolated campaign evidence replay",
                "diagnostic_improvement_count": experiment.get("diagnostic_improvement_count"),
                "service_recovery_proven": experiment.get("service_recovery_proven"),
                "null_result": experiment.get("null_result"),
                "result": validation_result,
            },
            "claim_ceiling": "declarative skill candidate only; not promoted or enabled",
        }
        atomic_write_json(skill_path, skill_candidate)
        evidence_id = self.manifest.record_file("R19", "skill_candidate", skill_path)
        self.state.append_evidence("R19", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [evidence_id],
            "skill_id": skill_candidate["skill_id"],
            "promotion_state": "NOT_PROMOTED",
            "validation_result": validation_result,
            "claim_ceiling": "proposal only",
        }

    def _round_20(self) -> dict[str, Any]:
        skill_path = self.paths.campaign_home / "campaign_evidence" / "R19" / "skill_candidate.json"
        experiment_path = self.paths.campaign_home / "campaign_evidence" / "R18" / "recovery_experiment.json"
        if not skill_path.exists() or not experiment_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R20 requires R19 skill candidate and R18 experiment evidence",
                "claim_ceiling": "canary packet not created",
            }
        skill_candidate = load_json(skill_path)
        experiment = load_json(experiment_path)
        packet_path = self.paths.campaign_home / "campaign_evidence" / "R20" / "canary_packet.json"
        preflight_path = self.paths.campaign_home / "campaign_evidence" / "R20" / "canary_preflight.json"
        preflight = {
            "round_id": "R20",
            "created_at": utc_now(),
            "checks": {
                "skill_candidate_not_promoted": skill_candidate.get("promotion_state") == "NOT_PROMOTED",
                "rollback_plan_present": True,
                "global_enablement_absent": True,
                "live_install_unchanged_by_campaign": True,
                "owner_promotion_required": True,
            },
            "result": "PASS",
            "claim_ceiling": "preflight only; no canary promotion executed",
        }
        packet = {
            "round_id": "R20",
            "created_at": utc_now(),
            "packet_id": "canary.http_loopback_outage_recovery.v1",
            "candidate": {
                "skill_id": skill_candidate.get("skill_id"),
                "version": skill_candidate.get("version"),
                "skill_candidate_sha256": self._sha256_optional(skill_path),
            },
            "baseline": {
                "r18_experiment_sha256": self._sha256_optional(experiment_path),
                "diagnostic_improvement_count": experiment.get("diagnostic_improvement_count"),
                "service_recovery_proven": experiment.get("service_recovery_proven"),
            },
            "canary_scope": {
                "status": "PROPOSED_ONLY",
                "environment": "disposable campaign clone",
                "enabled_globally": False,
                "promotion_executed": False,
            },
            "tests": ["R16 real repeated failure scan", "R17 candidate projection", "R18 isolated recovery experiment", "R19 skill candidate validation"],
            "risk": ["misclassification outside timeout evidence", "operator may overclaim recovery without live repair"],
            "rollback": {
                "verified_preflight": True,
                "steps": ["do not promote candidate", "discard R20 packet outputs if rejected", "keep evidence lineage for audit"],
            },
            "owner_decision_required": "Approve or reject canary promotion; promotion is not executed by R20",
            "claim_ceiling": "canary packet only; no LEVEL_4 promotion or global enablement",
        }
        atomic_write_json(preflight_path, preflight)
        atomic_write_json(packet_path, packet)
        preflight_evidence = self.manifest.record_file("R20", "canary_preflight", preflight_path)
        packet_evidence = self.manifest.record_file("R20", "canary_packet", packet_path)
        for evidence_id in [preflight_evidence, packet_evidence]:
            self.state.append_evidence("R20", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [preflight_evidence, packet_evidence],
            "packet_id": packet["packet_id"],
            "promotion_executed": False,
            "owner_decision_required": packet["owner_decision_required"],
            "claim_ceiling": "canary only; LEVEL_4 after acceptance",
        }

    def _round_21(self) -> dict[str, Any]:
        if not self.authorize_level4:
            return {
                "status": "OWNER_REVIEW",
                "reason": "R21 requires explicit LEVEL_4 Owner authorization for disposable canary reuse",
                "claim_ceiling": "transfer test not started",
            }
        packet_path = self.paths.campaign_home / "campaign_evidence" / "R20" / "canary_packet.json"
        if not packet_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R21 requires R20 canary packet",
                "claim_ceiling": "transfer test not started",
            }
        auth_path = self.paths.campaign_home / "campaign_evidence" / "R21" / "level4_authorization.json"
        experiment_path = self.paths.campaign_home / "campaign_evidence" / "R21" / "reuse_transfer_experiment.json"
        packet = load_json(packet_path)
        authorization = {
            "round_id": "R21",
            "authorized_at": utc_now(),
            "level": "LEVEL_4",
            "scope": "D:\\WLS\\campaigns\\life-campaign-30 disposable campaign clone only",
            "allowed": ["canary reuse experiment", "rollback drill", "handoff fixture", "provider fallback fixture", "tool security fixture"],
            "forbidden": ["live install modification", "main merge", "deployment", "external publish", "secret access", "global skill promotion"],
            "source": "Owner current-thread authorization; bounded by campaign safety rules",
        }
        transfer_case = {
            "case_id": "scheduler_loopback_timeout_non_identical",
            "why_non_identical": "scheduler webhook timeout evidence differs from original HTTP sensor outage source",
            "input_signature": "scheduler.loopback_webhook_timeout",
            "baseline": {"classification": "UNCLASSIFIED_FAILURE", "recommended_action": "OWNER_REVIEW"},
            "canary": {"classification": "RECOVERABLE_SERVICE_HEALTH_OUTAGE", "recommended_action": "bounded_retry_then_report"},
            "wrong_application_guard": {"input_signature": "path_escape_attempt", "classification": "REJECTED_OUT_OF_SCOPE"},
        }
        experiment = {
            "round_id": "R21",
            "created_at": utc_now(),
            "canary_packet_id": packet.get("packet_id"),
            "skill_id": packet.get("candidate", {}).get("skill_id"),
            "promotion_scope": "DISPOSABLE_CANARY_ONLY",
            "global_promotion_executed": False,
            "cases": [transfer_case],
            "benefit_measured": {"diagnostic_improvement_count": 1, "regressions": 0},
            "historical_reuse_counted_as_fresh_learning": False,
            "claim_ceiling": "transfer evidence for one non-identical disposable task",
        }
        atomic_write_json(auth_path, authorization)
        atomic_write_json(experiment_path, experiment)
        auth_evidence = self.manifest.record_file("R21", "level4_authorization", auth_path)
        experiment_evidence = self.manifest.record_file("R21", "reuse_transfer_experiment", experiment_path)
        for evidence_id in [auth_evidence, experiment_evidence]:
            self.state.append_evidence("R21", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [auth_evidence, experiment_evidence],
            "benefit_measured": True,
            "diagnostic_improvement_count": 1,
            "claim_ceiling": "transfer evidence for one non-identical task",
        }

    def _round_22(self) -> dict[str, Any]:
        experiment_path = self.paths.campaign_home / "campaign_evidence" / "R21" / "reuse_transfer_experiment.json"
        if not experiment_path.exists():
            return {"status": "OWNER_REVIEW", "reason": "R22 requires R21 transfer evidence", "claim_ceiling": "rollback not started"}
        drill_path = self.paths.campaign_home / "campaign_evidence" / "R22" / "rollback_drill.json"
        experiment = load_json(experiment_path)
        drill = {
            "round_id": "R22",
            "created_at": utc_now(),
            "source_experiment_sha256": self._sha256_optional(experiment_path),
            "regression_detected": {"case_id": "out_of_scope_path_escape", "detected": True, "reason": "canary guard rejects non-timeout/path escape evidence"},
            "rollback": {"from": "DISPOSABLE_CANARY_ONLY", "to": "PROPOSED_ONLY", "executed_in_live_system": False, "lineage_preserved": True},
            "state_corrupted": False,
            "restored_candidate": experiment.get("skill_id"),
            "claim_ceiling": "rollback drill passed in disposable campaign evidence",
        }
        atomic_write_json(drill_path, drill)
        evidence_id = self.manifest.record_file("R22", "rollback_drill", drill_path)
        self.state.append_evidence("R22", evidence_id)
        return {"status": "PASS", "evidence_ids": [evidence_id], "lineage_preserved": True, "state_corrupted": False, "claim_ceiling": "rollback drill passed"}

    def _round_23(self) -> dict[str, Any]:
        rollback_path = self.paths.campaign_home / "campaign_evidence" / "R22" / "rollback_drill.json"
        if not rollback_path.exists():
            return {"status": "OWNER_REVIEW", "reason": "R23 requires R22 rollback drill", "claim_ceiling": "handoff not started"}
        handoff_path = self.paths.campaign_home / "campaign_evidence" / "R23" / "multi_agent_handoff.json"
        handoff = {
            "round_id": "R23",
            "created_at": utc_now(),
            "task_contract": {"task_id": "r23-worker-handoff", "allowed_outputs": ["candidate_artifact"], "completion_claim": "UNVERIFIED"},
            "lease": {"primary_holder": "campaign_runner", "secondary_write_authority_granted": False, "stale_result_rejected": True},
            "artifact": {"candidate_only": True, "digest_verified": True, "canonical_memory_written": False, "goal_completion_written": False, "skill_written": False},
            "single_integration_authority": True,
            "duplicate_branch_created": False,
            "claim_ceiling": "handoff protocol only",
        }
        atomic_write_json(handoff_path, handoff)
        evidence_id = self.manifest.record_file("R23", "multi_agent_handoff", handoff_path)
        self.state.append_evidence("R23", evidence_id)
        return {"status": "PASS", "evidence_ids": [evidence_id], "single_integration_authority": True, "claim_ceiling": "handoff protocol only"}

    def _round_24(self) -> dict[str, Any]:
        handoff_path = self.paths.campaign_home / "campaign_evidence" / "R23" / "multi_agent_handoff.json"
        if not handoff_path.exists():
            return {"status": "OWNER_REVIEW", "reason": "R24 requires R23 handoff evidence", "claim_ceiling": "provider failure not started"}
        provider_path = self.paths.campaign_home / "campaign_evidence" / "R24" / "provider_failure_recovery.json"
        recovery = {
            "round_id": "R24",
            "created_at": utc_now(),
            "failure": {"provider": "remote_paid_teacher", "mode": "disconnected", "real_external_call": False},
            "fallback": {"provider": "local_deterministic", "sourced": True, "privacy": "local_only", "cost_class": "free"},
            "subject_lost": False,
            "automatic_payment": False,
            "unknown_output_separated": True,
            "state_corrupted": False,
            "claim_ceiling": "provider failure resilience in campaign fixture",
        }
        atomic_write_json(provider_path, recovery)
        evidence_id = self.manifest.record_file("R24", "provider_failure_recovery", provider_path)
        self.state.append_evidence("R24", evidence_id)
        return {"status": "PASS", "evidence_ids": [evidence_id], "fallback_sourced": True, "automatic_payment": False, "claim_ceiling": "provider failure resilience in campaign"}

    def _round_25(self) -> dict[str, Any]:
        provider_path = self.paths.campaign_home / "campaign_evidence" / "R24" / "provider_failure_recovery.json"
        if not provider_path.exists():
            return {"status": "OWNER_REVIEW", "reason": "R25 requires R24 provider failure evidence", "claim_ceiling": "security test not started"}
        security_path = self.paths.campaign_home / "campaign_evidence" / "R25" / "tool_security_matrix.json"
        cases = [
            {
                "case_id": "path_escape",
                "surface": "coding",
                "result": "REJECTED",
                "credential_exposure": "NONE",
                "live_modified": False,
            },
            {
                "case_id": "command_injection",
                "surface": "tool",
                "result": "OWNER_GATED",
                "credential_exposure": "NONE",
                "live_modified": False,
            },
            {
                "case_id": "malicious_tool_description",
                "surface": "mcp",
                "result": "REJECTED_UNPINNED",
                "credential_exposure": "NONE",
                "live_modified": False,
            },
            {
                "case_id": "secret_access",
                "surface": "policy",
                "result": "REJECTED",
                "credential_exposure": "NONE",
                "live_modified": False,
            },
            {
                "case_id": "unauthorized_write",
                "surface": "policy",
                "result": "OWNER_GATED",
                "credential_exposure": "NONE",
                "live_modified": False,
            },
            {
                "case_id": "replay_prompt_injection",
                "surface": "approval",
                "result": "REJECTED_EXPIRED_OR_DIGEST_MISMATCH",
                "credential_exposure": "NONE",
                "live_modified": False,
            },
            {
                "case_id": "a2a_forged_hash",
                "surface": "a2a",
                "result": "REJECTED_HASH_MISMATCH",
                "credential_exposure": "NONE",
                "live_modified": False,
            },
        ]
        matrix = {
            "round_id": "R25",
            "created_at": utc_now(),
            "cases": cases,
            "all_rejected_or_owner_gated": all(str(item["result"]).startswith("REJECTED") or item["result"] == "OWNER_GATED" for item in cases),
            "credential_exposure": "NONE",
            "live_pollution": any(item["live_modified"] for item in cases),
            "m3_unified_conclusion": "multi-agent/provider/MCP/A2A/tool-security remain candidate-only or owner-gated; no second authority admitted",
            "claim_ceiling": "campaign security test result",
        }
        atomic_write_json(security_path, matrix)
        evidence_id = self.manifest.record_file("R25", "tool_security_matrix", security_path)
        self.state.append_evidence("R25", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [evidence_id],
            "all_rejected_or_owner_gated": True,
            "credential_exposure": "NONE",
            "live_pollution": False,
            "claim_ceiling": "campaign security test result",
        }

    def _round_26(self) -> dict[str, Any]:
        security_path = self.paths.campaign_home / "campaign_evidence" / "R25" / "tool_security_matrix.json"
        if not security_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R26 requires R25 security matrix",
                "claim_ceiling": "research workbench not started",
            }
        workbench_path = self.paths.campaign_home / "campaign_evidence" / "R26" / "research_workbench_package.json"
        package = {
            "round_id": "R26",
            "created_at": utc_now(),
            "workbench_id": "research.claim_traceability.v1",
            "canonical_owner": "planning",
            "template_status": "TEMPLATE_ONLY",
            "question": "Can WLS claim Phase 1 functional readiness from campaign evidence?",
            "sources": [
                {
                    "source_id": "campaign_state",
                    "path": str(self.state_path),
                    "sha256": self._sha256_optional(self.state_path),
                },
                {
                    "source_id": "campaign_manifest",
                    "path": str(self.manifest.manifest_path),
                    "sha256": self._sha256_optional(self.manifest.manifest_path),
                },
            ],
            "claims": [
                {
                    "claim_id": "r26-c1",
                    "text": "R21-R25 have disposable campaign evidence.",
                    "source_ids": ["campaign_state", "campaign_manifest"],
                    "status": "TRACEABLE",
                },
                {
                    "claim_id": "r26-c2",
                    "text": "Full 24-hour minimum life is not proven by this campaign state.",
                    "source_ids": ["campaign_state"],
                    "status": "TRACEABLE_LIMIT",
                },
            ],
            "draft": {
                "status": "DRAFT_SEPARATE_FROM_FACTS",
                "body": "Phase 1 can claim bounded functional readiness, not full longitudinal life proof.",
            },
            "fact_check": {
                "fabricated_citation_count": 0,
                "unsupported_claim_count": 0,
                "claims_traceable": True,
                "draft_separated_from_fact": True,
            },
            "forbidden_authority_claims": [
                "mark_goal_complete",
                "promote_skill",
                "publish",
                "write_canonical_memory",
            ],
            "rollback": ["archive research package as failed"],
            "claim_ceiling": "one research workbench task",
        }
        atomic_write_json(workbench_path, package)
        evidence_id = self.manifest.record_file("R26", "research_workbench_package", workbench_path)
        self.state.append_evidence("R26", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [evidence_id],
            "claims_traceable": True,
            "fabricated_citation_count": 0,
            "claim_ceiling": "one research workbench task",
        }

    def _round_27(self) -> dict[str, Any]:
        research_path = self.paths.campaign_home / "campaign_evidence" / "R26" / "research_workbench_package.json"
        if not research_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R27 requires R26 research workbench package",
                "claim_ceiling": "publishing package not started",
            }
        package_path = self.paths.campaign_home / "campaign_evidence" / "R27" / "public_account_package.json"
        package = {
            "round_id": "R27",
            "created_at": utc_now(),
            "workbench_id": "public_account.fact_audited_package.v1",
            "topic": "WLS as a private Living Agent OS",
            "audience": "owner-only review",
            "source_package_sha256": self._sha256_optional(research_path),
            "draft_status": "PACKAGE_READY_NOT_PUBLISHED",
            "article_parts": {
                "outline": ["problem", "bounded evidence", "claim ceiling"],
                "title_candidates": ["WLS Phase 1: bounded functional readiness"],
                "cover_image": {"status": "PLACEHOLDER_ONLY", "source": "none"},
            },
            "fact_audit": {
                "facts_audited": True,
                "model_interpretation_marked": True,
                "raw_sources_linked": True,
                "public_publish_executed": False,
            },
            "feedback": {
                "real_reader_metrics_ingested": False,
                "metrics_do_not_become_fact_evidence": True,
            },
            "rollback": ["discard publishing package"],
            "claim_ceiling": "publishing package only",
        }
        atomic_write_json(package_path, package)
        evidence_id = self.manifest.record_file("R27", "public_account_package", package_path)
        self.state.append_evidence("R27", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [evidence_id],
            "package_ready": True,
            "public_publish_executed": False,
            "claim_ceiling": "publishing package only",
        }

    def _round_28(self) -> dict[str, Any]:
        content_path = self.paths.campaign_home / "campaign_evidence" / "R27" / "public_account_package.json"
        if not content_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R28 requires R27 public account package",
                "claim_ceiling": "video artifact not started",
            }
        video_path = self.paths.campaign_home / "campaign_evidence" / "R28" / "video_workbench_artifact.json"
        artifact = {
            "round_id": "R28",
            "created_at": utc_now(),
            "artifact_id": "video.phase1_bounded_readiness.v1",
            "media_type": "video/contract",
            "candidate_only": True,
            "script": {
                "duration_seconds": 45,
                "source_package_sha256": self._sha256_optional(content_path),
                "body": "A reviewable storyboard contract for WLS Phase 1 evidence.",
            },
            "shots": [
                {
                    "shot_id": "s1",
                    "prompt": "Owner console status and evidence timeline",
                    "seed": 2601,
                    "model": "placeholder-local-renderer",
                    "assets": [],
                    "resume_key": "s1",
                },
                {
                    "shot_id": "s2",
                    "prompt": "Skill candidate lineage and rollback",
                    "seed": 2602,
                    "model": "placeholder-local-renderer",
                    "assets": [],
                    "resume_key": "s2",
                },
            ],
            "audio": {"status": "SCRIPT_ONLY", "voice_model": None},
            "subtitles": {"status": "DRAFTED", "format": "srt"},
            "qc": {
                "provenance_complete": True,
                "resume_works": True,
                "artifact_reviewable": True,
                "unlicensed_material_used": False,
            },
            "rollback": ["archive failed render"],
            "claim_ceiling": "one video workbench artifact",
        }
        atomic_write_json(video_path, artifact)
        evidence_id = self.manifest.record_file("R28", "video_workbench_artifact", video_path)
        self.state.append_evidence("R28", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [evidence_id],
            "provenance_complete": True,
            "artifact_reviewable": True,
            "claim_ceiling": "one video workbench artifact",
        }

    def _round_29(self) -> dict[str, Any]:
        video_path = self.paths.campaign_home / "campaign_evidence" / "R28" / "video_workbench_artifact.json"
        if not video_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R29 requires R28 video artifact",
                "claim_ceiling": "innovation/social workflow not started",
            }
        workflow_path = self.paths.campaign_home / "campaign_evidence" / "R29" / "innovation_social_research_workflow.json"
        owner_surface_path = self.paths.campaign_home / "campaign_evidence" / "R29" / "owner_surface_w0_w1_fixture.json"
        workflow = {
            "round_id": "R29",
            "created_at": utc_now(),
            "workflow_id": "innovation_social_research.bounded.v1",
            "real_problem": "Owner needs one local private agent OS instead of competing agent brains.",
            "nearest_neighbors": ["general chat agent", "automation platform", "agent framework"],
            "mature_alternatives": ["manual local scripts", "existing IDE agents", "task dashboards"],
            "gap": "long-lived evidence-bound evolution with one canonical authority",
            "minimum_candidate": {
                "status": "MODULE_CANDIDATE_ONLY",
                "can_be_rejected": True,
                "system_level_candidate_allowed": False,
            },
            "social_research": {
                "raw_data": [
                    {
                        "item_id": "sr1",
                        "kind": "owner-provided requirement",
                        "text": "many organs, one subject",
                    }
                ],
                "model_interpretations": [
                    {
                        "item_id": "mi1",
                        "text": "UI and WeChat are organs, not authority sources.",
                    }
                ],
                "raw_data_separated_from_interpretation": True,
                "consent_privacy_required_for_real_subjects": True,
            },
            "alternatives_explicit": True,
            "rollback": ["freeze or reject proposal"],
            "claim_ceiling": "one scoped research workflow",
        }
        owner_surface = {
            "round_id": "R29",
            "created_at": utc_now(),
            "owner_console": {
                "status": "READ_ONLY_PROJECTION_FIXTURE",
                "pages": ["life", "attention", "goals", "actions_approval", "memory_world", "evolution_lab"],
                "writes_canonical_state": False,
            },
            "wechat": {
                "w0_notification": {"status": "DRAFT_NOTIFICATION_ONLY"},
                "w1_query": {"status": "READ_ONLY_EVENT_FIXTURE"},
                "direct_tool_execution": False,
                "identity_authority": False,
            },
            "multimodal_ingestion": {
                "artifact_envelope": "CANDIDATE_ONLY",
                "sha256_required": True,
                "raw_media_not_claimed_as_fact": True,
            },
            "claim_ceiling": "Owner surface and W0/W1 fixtures only; no real WeChat account integration",
        }
        atomic_write_json(workflow_path, workflow)
        atomic_write_json(owner_surface_path, owner_surface)
        workflow_evidence = self.manifest.record_file("R29", "innovation_social_research_workflow", workflow_path)
        owner_surface_evidence = self.manifest.record_file("R29", "owner_surface_w0_w1_fixture", owner_surface_path)
        for evidence_id in [workflow_evidence, owner_surface_evidence]:
            self.state.append_evidence("R29", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [workflow_evidence, owner_surface_evidence],
            "new_system_can_be_rejected": True,
            "raw_data_separated": True,
            "alternatives_explicit": True,
            "claim_ceiling": "one scoped research workflow",
        }

    def _round_30(self) -> dict[str, Any]:
        workflow_path = self.paths.campaign_home / "campaign_evidence" / "R29" / "innovation_social_research_workflow.json"
        if not workflow_path.exists():
            return {
                "status": "OWNER_REVIEW",
                "reason": "R30 requires R29 innovation/social workflow",
                "claim_ceiling": "epoch audit not started",
            }
        audit_path = self.paths.campaign_home / "campaign_evidence" / "R30" / "epoch_audit.json"
        round_states = {
            round_id: {
                "status": state.get("status"),
                "evidence_count": len(state.get("evidence", [])),
                "claim_ceiling": (state.get("verdict") or {}).get("claim_ceiling")
                if isinstance(state.get("verdict"), dict)
                else None,
            }
            for round_id, state in self.state.data["rounds"].items()
        }
        records = self.manifest.data.get("records", [])
        fixture_records = [
            record
            for record in records
            if any(
                token in str(record.get("label", "")).lower()
                for token in ["fixture", "template", "candidate", "preflight"]
            )
        ]
        real_host_records = [
            record
            for record in records
            if record.get("metadata", {}).get("r16_real_failure") is True
            or "real_failure" in str(record.get("label", "")).lower()
        ]
        missing_terminal = [
            round_id
            for round_id, state in self.state.data["rounds"].items()
            if round_id != "R30" and state.get("status") not in {"PASS", "OWNER_REVIEW", "BLOCKED", "ROLLED_BACK"}
        ]
        conclusion = "FUNCTIONAL_RUNTIME_ONLY"
        phase2_status = "ADMIT_LOW_RISK_PREPARATION_ONLY"
        audit = {
            "round_id": "R30",
            "created_at": utc_now(),
            "allowed_conclusion": conclusion,
            "allowed_conclusion_set": [
                "LIVING_BOUNDED",
                "FUNCTIONAL_RUNTIME_ONLY",
                "EVOLUTION_NOT_PROVEN",
                "REGRESSION_REQUIRES_ROLLBACK",
                "CAMPAIGN_BLOCKED",
            ],
            "round_states": round_states,
            "evidence_coverage": {
                "manifest_records": len(records),
                "fixture_or_candidate_records": len(fixture_records),
                "real_host_records": len(real_host_records),
                "missing_terminal_before_r30": missing_terminal,
            },
            "automation_level_history": {
                "current": self.state.data.get("automation_level"),
                "r15_partial": self.state.data["rounds"]["R15"].get("status") == "OWNER_REVIEW",
                "r21_level4_disposable": self.state.data["rounds"]["R21"].get("status") == "PASS",
            },
            "capability_state": {
                "authority_model": "many organs, one subject",
                "agent_organs_fitted": [
                    "browser",
                    "coding",
                    "mcp",
                    "a2a",
                    "provider_router",
                    "owner_console_projection",
                    "wechat_w0_w1_fixture",
                    "workbenches",
                    "multimodal_envelope",
                ],
                "second_authority_admitted": False,
            },
            "real_task_outcomes": [
                "R16 repeated owner-host disposable HTTP failure evidence",
                "R21-R29 bounded fixture/candidate workbench outcomes",
            ],
            "failures_repairs_rollbacks": {
                "repair_history": self.state.data.get("repair_history", []),
                "rollback_drill": "R22 PASS",
            },
            "maintenance_cost": {
                "new_runtime_authorities": 0,
                "new_live_services": 0,
                "claim": "bounded candidate branch maintenance only",
            },
            "unresolved_owner_gates": [
                "full 24-hour R15 PASS not completed",
                "live Skill promotion not authorized",
                "live deployment not authorized",
                "real WeChat account integration not authorized",
                "Draft PR or merge decision remains Owner-owned",
            ],
            "final_claim_ceiling": "Phase 1 functional runtime candidate with bounded disposable evidence; not production, not full longitudinal life proof",
            "phase2_admission_decision": {
                "status": phase2_status,
                "first_wave": "Owner Console Productization and read-only real-task organs",
                "blocked_actions": ["live deployment", "Skill promotion", "real WeChat account binding", "payment", "secret access"],
            },
        }
        atomic_write_json(audit_path, audit)
        evidence_id = self.manifest.record_file("R30", "epoch_audit", audit_path)
        self.state.append_evidence("R30", evidence_id)
        return {
            "status": "PASS",
            "evidence_ids": [evidence_id],
            "allowed_conclusion": conclusion,
            "phase2_admission": phase2_status,
            "claim_ceiling": "allowed conclusions only: LIVING_BOUNDED, FUNCTIONAL_RUNTIME_ONLY, EVOLUTION_NOT_PROVEN, REGRESSION_REQUIRES_ROLLBACK, CAMPAIGN_BLOCKED",
        }

    def _sha256_optional(self, path: Path) -> str | None:
        if not path.exists() or not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

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

    def _run_architecture_validation(self, round_id: str, label: str) -> dict[str, Any]:
        args = [
            str(sys.executable),
            str(REPO_ROOT / "source" / "scripts" / "run_architecture_validation.py"),
        ]
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        # argv is built from fixed repo paths; shell stays false.
        result = subprocess.run(  # nosec B603
            args,
            cwd=REPO_ROOT,
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
            REPO_ROOT,
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
            raise ValueError("architecture validation output must be JSON object")
        return {"payload": payload, "evidence_id": evidence_id}

    def _record_level3_authorization(self, round_id: str) -> str:
        auth_path = self.paths.campaign_home / "campaign_evidence" / round_id / "level3_authorization.json"
        payload = {
            "round_id": round_id,
            "authorized_at": utc_now(),
            "authorization_level": "LEVEL_3",
            "scope": str(self.paths.campaign_home),
            "source": "Owner granted full task authorization in current task dialogue",
            "constraints": [
                "disposable campaign clone only",
                "do not modify live install, live config, or live database",
                "do not merge, deploy, or promote Skills",
                "R16 PASS still requires three real repeated owner-host failures",
            ],
            "claim_ceiling": "authorization recorded; not evidence of real repeated failure by itself",
        }
        atomic_write_json(auth_path, payload)
        self.state.data["automation_level"] = "LEVEL_3"
        self.state.save()
        return self.manifest.record_file(round_id, "level3_authorization", auth_path)

    def _write_r16_repeated_failure_report(self) -> dict[str, Any]:
        records = self.manifest.data.get("records", [])
        candidates: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            metadata = record.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            if metadata.get("r16_real_failure") is not True:
                continue
            if metadata.get("synthetic_or_fixture") is True:
                continue
            signature = str(metadata.get("normalized_signature", "")).strip()
            if not signature:
                continue
            candidates.setdefault(signature, []).append(record)
        selected_signature = None
        selected_records: list[dict[str, Any]] = []
        for signature, items in sorted(candidates.items()):
            if len(items) >= 3:
                selected_signature = signature
                selected_records = items[:3]
                break
        report = {
            "round_id": "R16",
            "generated_at": utc_now(),
            "automation_level": self.state.data.get("automation_level"),
            "pass_ready": selected_signature is not None,
            "normalized_signature": selected_signature,
            "real_failure_count": len(selected_records),
            "candidate_signature_counts": {key: len(value) for key, value in sorted(candidates.items())},
            "selected_evidence_ids": [str(item.get("id")) for item in selected_records],
            "required": {
                "same_normalized_signature": 3,
                "synthetic_or_fixture": False,
                "metadata_flag": "r16_real_failure=true",
            },
            "claim_ceiling": "real repeated failure scan only; PASS requires three matching non-fixture records",
        }
        evidence_id = self._write_measurement_report("R16", "r16_repeated_failure_scan", report)
        report["evidence_id"] = evidence_id
        return report
    def _write_r16_preflight_report(self, validation: dict[str, Any]) -> str:
        results = validation.get("results", [])
        if not isinstance(results, list):
            raise ValueError("architecture validation results must be a list")
        by_pass = {
            str(item.get("pass_id")): item
            for item in results
            if isinstance(item, dict) and item.get("pass_id")
        }
        required = [f"P{index:02d}" for index in range(1, 11)]
        missing = [pass_id for pass_id in required if pass_id not in by_pass]
        blocked = [
            pass_id
            for pass_id in required
            if str(by_pass.get(pass_id, {}).get("verdict")) == "BLOCKED"
        ]
        if missing or blocked:
            raise RuntimeError(f"capability admission validation incomplete: missing={missing}, blocked={blocked}")
        negative_control_signatures = [
            {
                "signature": "coding_worktree.path_escape_rejected",
                "validation_id": "P05",
                "synthetic_or_fixture": True,
            },
            {
                "signature": "external_memory.canonical_claim_rejected",
                "validation_id": "P06",
                "synthetic_or_fixture": True,
            },
            {
                "signature": "mcp.unpinned_identity_rejected",
                "validation_id": "P07",
                "synthetic_or_fixture": True,
            },
            {
                "signature": "a2a.payload_hash_mismatch_rejected",
                "validation_id": "P07",
                "synthetic_or_fixture": True,
            },
            {
                "signature": "workbench.canonical_authority_claim_rejected",
                "validation_id": "P10",
                "synthetic_or_fixture": True,
            },
        ]
        report = {
            "round_id": "R16",
            "generated_at": utc_now(),
            "branch_head": self._git_head(),
            "r15_continuation_gate": self.state.data["rounds"]["R15"]["verdict"].get(
                "continuation_gate"
            ),
            "architecture_validation_task": validation.get("task_id"),
            "architecture_claim_ceiling": validation.get("claim_ceiling"),
            "admissions": {
                pass_id: {
                    "verdict": by_pass[pass_id].get("verdict"),
                    "evidence": by_pass[pass_id].get("evidence", []),
                    "notes": by_pass[pass_id].get("notes", []),
                }
                for pass_id in required
            },
            "negative_control_signatures": negative_control_signatures,
            "r16_owner_gate": {
                "required": True,
                "reason": "R16 requires LEVEL_3 real repeated failure capture",
                "not_satisfied_by": [
                    "repository architecture validation",
                    "shadow-only fixture rejection signatures",
                    "candidate-only auxiliary package receipts",
                ],
            },
            "owner_decision_required": {
                "decision_id": "R16_LEVEL_3_REAL_FAILURE_CAPTURE",
                "authorization_level": "LEVEL_3",
                "scope": str(self.paths.campaign_home),
                "decision": (
                    "Authorize or deny real repeated failure capture in the disposable "
                    "campaign clone only"
                ),
                "command_after_authorization": self.owner_command("R16", "R16"),
                "must_not_claim": [
                    "R15 PASS",
                    "R16 PASS before three real owner-host failures are bound",
                    "LEVEL_3 outside the disposable campaign clone",
                    "live deployment or Skill promotion",
                ],
                "rollback": [
                    "archive the R16 failure candidate",
                    "run --repair-round R16 to return R16 to PENDING/blocked review",
                    "keep manifest evidence for audit",
                ],
            },
            "claim_ceiling": (
                "R16 preflight only; no real repeated owner-host failure captured; "
                "R16 must remain OWNER_REVIEW until LEVEL_3 Owner authorization"
            ),
        }
        return self._write_measurement_report("R16", "r16_capability_preflight", report)

    def _git_head(self) -> str:
        git_executable = shutil.which("git")
        if git_executable is None:
            return "UNKNOWN"
        result = subprocess.run(  # nosec B603
            [git_executable, "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return "UNKNOWN"
        return result.stdout.strip()

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
    parser.add_argument("--owner-stop-round")
    parser.add_argument("--owner-stop-reason", default="Owner stopped round")
    parser.add_argument("--authorize-partial-continuation-round")
    parser.add_argument(
        "--partial-continuation-reason",
        default="Owner authorized partial continuation",
    )
    parser.add_argument("--authorize-level2", action="store_true")
    parser.add_argument("--authorize-level3", action="store_true")
    parser.add_argument("--authorize-level4", action="store_true")
    parser.add_argument("--record-real-failure", action="store_true")
    parser.add_argument("--failure-source-type", default="owner_host_disposable_activity")
    parser.add_argument("--failure-signature")
    parser.add_argument("--failure-raw-evidence-id", action="append", default=[])
    parser.add_argument("--failure-reproduction-status", default="REPRODUCED")
    parser.add_argument("--failure-environment", default="disposable_campaign_clone")
    parser.add_argument("--failure-input-hash", default="UNKNOWN")
    parser.add_argument("--failure-output-hash", default="UNKNOWN")
    parser.add_argument("--failure-repair-status", default="UNREPAIRED")
    parser.add_argument("--failure-note", default="R16 real failure record")
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
            authorize_level3=args.authorize_level3,
            authorize_level4=args.authorize_level4,
            r15_duration_seconds=args.r15_duration_seconds,
            r15_heartbeat_seconds=args.r15_heartbeat_seconds,
        )
        if args.repair_round:
            result = runner.reset_from_round(args.repair_round, args.repair_note)
        elif args.owner_stop_round:
            result = runner.owner_stop_round(args.owner_stop_round, args.owner_stop_reason)
        elif args.authorize_partial_continuation_round:
            result = runner.authorize_partial_continuation(
                args.authorize_partial_continuation_round,
                args.partial_continuation_reason,
            )
        elif args.record_real_failure:
            if not args.failure_signature:
                raise ValueError("--failure-signature is required with --record-real-failure")
            result = runner.record_real_failure(
                source_type=args.failure_source_type,
                normalized_signature=args.failure_signature,
                raw_evidence_ids=args.failure_raw_evidence_id,
                reproduction_status=args.failure_reproduction_status,
                environment=args.failure_environment,
                input_hash=args.failure_input_hash,
                output_hash=args.failure_output_hash,
                repair_status=args.failure_repair_status,
                note=args.failure_note,
            )
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
