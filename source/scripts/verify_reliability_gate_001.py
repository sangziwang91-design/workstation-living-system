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
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root / "src")

    report = {
        "target": "RELIABILITY-GATE-001",
        "base_commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "passed": False,
        "platform": platform.platform(),
        "python": sys.version,
        "baseline": {},
        "reliability_tests": {},
        "soak": {},
        "claims": "Independent reliability verification of WLS convergence head."
    }

    # Baseline
    report["baseline"]["et001"] = run_cmd([sys.executable, "scripts/verify_evolution_target_001.py"], cwd=source_root, env=env)
    report["baseline"]["et002"] = run_cmd([sys.executable, "scripts/verify_evolution_target_002.py"], cwd=source_root, env=env)
    report["baseline"]["et003"] = run_cmd([sys.executable, "scripts/verify_evolution_target_003.py"], cwd=source_root, env=env)
    report["baseline"]["et004"] = run_cmd([sys.executable, "scripts/verify_evolution_target_004.py"], cwd=source_root, env=env)

    # Reliability
    report["reliability_tests"]["evidence_tamper"] = run_cmd([sys.executable, "-m", "pytest", "tests/test_evidence_tamper.py"], cwd=source_root, env=env)
    report["reliability_tests"]["approval_replay"] = run_cmd([sys.executable, "-m", "pytest", "tests/test_approval_replay.py"], cwd=source_root, env=env)
    report["reliability_tests"]["crash_recovery"] = run_cmd([sys.executable, "-m", "pytest", "tests/test_crash_recovery.py"], cwd=source_root, env=env)
    report["reliability_tests"]["backup_restore"] = run_cmd([sys.executable, "-m", "pytest", "tests/test_backup_restore.py"], cwd=source_root, env=env)
    report["reliability_tests"]["adversarial"] = run_cmd([sys.executable, "-m", "pytest", "tests/test_adversarial_boundaries.py"], cwd=source_root, env=env)
    report["reliability_tests"]["secret_redaction"] = run_cmd([sys.executable, "-m", "pytest", "tests/test_secret_redaction.py"], cwd=source_root, env=env)
    report["reliability_tests"]["survival"] = run_cmd([sys.executable, "-m", "pytest", "tests/test_survival.py"], cwd=source_root, env=env)
    report["reliability_tests"]["provider_hub"] = run_cmd([sys.executable, "-m", "pytest", "tests/test_provider_hub_hardened.py"], cwd=source_root, env=env)

    # 100 cycle soak
    report["soak"] = run_cmd([sys.executable, "scripts/run_bounded_soak.py", "100"], cwd=source_root, env=env)

    # Overall pass
    all_pass = True
    for v in report["baseline"].values():
        if v.get("status") != "PASS": all_pass = False
    for v in report["reliability_tests"].values():
        if v.get("status") != "PASS": all_pass = False
    if report["soak"].get("status") != "PASS": all_pass = False

    report["passed"] = all_pass

    timestamp = datetime.now().strftime("%Y%m%d")
    out = source_root / "verification" / f"RELIABILITY_GATE_001_LOCAL_{timestamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"Report: {out}")
    print(f"Passed: {all_pass}")

if __name__ == "__main__":
    main()
