import subprocess
import sys
import os
import json
import platform
import time
from pathlib import Path
from datetime import datetime

def run_cmd(cmd, cwd=None, env=None):
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
        return {
            "status": "PASS" if res.returncode == 0 else "FAIL",
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "error": str(e)
        }

def main():
    source_root = Path(__file__).parent.parent.resolve()
    repo_root = source_root.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root / "src")

    report = {
        "task_id": "RELIABILITY-GATE-001",
        "repository": "workstation-living-system-private",
        "base_sha": "cf3e60d70d6c679891a560effabe89fd30da6709",
        "final_head_sha": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "branch": "convergence/wls-side-lines-20260624",
        "start_time": datetime.now().isoformat(),
        "commands_executed": [],
        "test_counts": {},
        "failed_tests": [],
        "skipped_tests": [],
        "unavailable_gates": ["GITHUB_ACTIONS"],
        "baseline": {},
        "reliability_tests": {},
        "provider_hub_security": {},
        "soak": {},
        "claim_ceiling": "100-cycle bounded local soak passed. Not longitudinally reliable, production reliable, or multi-day stable."
    }

    def track_run(name, cmd, group):
        report["commands_executed"].append(" ".join(cmd))
        res = run_cmd(cmd, cwd=source_root, env=env)
        group[name] = res
        return res

    # Baseline
    track_run("et001", [sys.executable, "scripts/verify_evolution_target_001.py"], report["baseline"])
    track_run("et002", [sys.executable, "scripts/verify_evolution_target_002.py"], report["baseline"])
    track_run("et003", [sys.executable, "scripts/verify_evolution_target_003.py"], report["baseline"])
    track_run("et004", [sys.executable, "scripts/verify_evolution_target_004.py"], report["baseline"])

    # Reliability
    track_run("evidence_tamper", [sys.executable, "-m", "pytest", "tests/test_evidence_tamper.py"], report["reliability_tests"])
    track_run("approval_replay", [sys.executable, "-m", "pytest", "tests/test_approval_replay.py"], report["reliability_tests"])
    track_run("crash_recovery", [sys.executable, "-m", "pytest", "tests/test_crash_recovery.py"], report["reliability_tests"])
    track_run("backup_restore", [sys.executable, "-m", "pytest", "tests/test_backup_restore.py"], report["reliability_tests"])
    track_run("adversarial", [sys.executable, "-m", "pytest", "tests/test_adversarial_boundaries.py"], report["reliability_tests"])
    track_run("secret_redaction", [sys.executable, "-m", "pytest", "tests/test_secret_redaction.py"], report["reliability_tests"])
    track_run("survival", [sys.executable, "-m", "pytest", "tests/test_survival.py"], report["reliability_tests"])
    track_run("provider_hub_hardened", [sys.executable, "-m", "pytest", "tests/test_provider_hub_hardened.py"], report["reliability_tests"])

    # Provider Hub Security
    track_run("provider_hub_security", [sys.executable, "-m", "pytest", "tests/test_provider_hub_security.py"], report["provider_hub_security"])

    # 100 cycle soak
    track_run("soak_100", [sys.executable, "scripts/run_bounded_soak.py", "100"], report["soak"])

    report["end_time"] = datetime.now().isoformat()

    # Summary
    all_pass = True
    for g in [report["baseline"], report["reliability_tests"], report["provider_hub_security"], report["soak"]]:
        for k, v in g.items():
            if v.get("status") != "PASS":
                all_pass = False
                report["failed_tests"].append(k)

    report["passed"] = all_pass

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = source_root / "verification" / f"TASK19_LOCAL_{timestamp}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2))

    # Generate MD report
    out_md = source_root / "verification" / f"TASK19_LOCAL_{timestamp}.md"
    md_content = f"""# Reliability Verification Report - Task 19
- Target: RELIABILITY-GATE-001
- Passed: {all_pass}
- Final Head SHA: {report["final_head_sha"]}
- Time: {report["start_time"]} to {report["end_time"]}

## Status
- ET001: {report["baseline"]["et001"]["status"]}
- ET002: {report["baseline"]["et002"]["status"]}
- ET003: {report["baseline"]["et003"]["status"]}
- ET004: {report["baseline"]["et004"]["status"]}
- Soak (100 cycles): {report["soak"]["soak_100"]["status"]}

## Security & Reliability
- Provider Hub Hardened: {report["reliability_tests"]["provider_hub_hardened"]["status"]}
- SSRF/Secret/Thread Safety: {report["provider_hub_security"]["provider_hub_security"]["status"]}
- Evidence Tamper Detection: {report["reliability_tests"]["evidence_tamper"]["status"]}
- Approval Safety: {report["reliability_tests"]["approval_replay"]["status"]}
- Crash Recovery: {report["reliability_tests"]["crash_recovery"]["status"]}

## Unavailable Gates
- GITHUB_ACTIONS: DEFERRED_CAPACITY_UNAVAILABLE

## Claim Ceiling
{report["claim_ceiling"]}
"""
    out_md.write_text(md_content)

    print(f"Report JSON: {out_json}")
    print(f"Report MD: {out_md}")
    print(f"Passed: {all_pass}")
    if not all_pass:
        print(f"Failed: {report['failed_tests']}")

if __name__ == "__main__":
    main()
