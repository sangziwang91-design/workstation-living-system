from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RECENT_PASSES = [f"P{index:02d}" for index in range(81, 89)]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )


def _git_value(args: list[str]) -> str:
    result = _run(["git", *args])
    return result.stdout.strip() if result.returncode == 0 else ""


def _architecture_validation(python_exe: str) -> dict[str, Any]:
    command = [
        python_exe,
        "source/scripts/run_architecture_validation.py",
        *[item for pass_id in RECENT_PASSES for item in ("--only", pass_id)],
    ]
    result = _run(command)
    payload: dict[str, Any] = {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
        "parsed": None,
    }
    if result.returncode == 0 and result.stdout.strip():
        payload["parsed"] = json.loads(result.stdout)
    return payload


def _handoff(python_exe: str) -> dict[str, Any]:
    command = [
        python_exe,
        "source/scripts/build_delivery_handoff.py",
        "--readiness-status",
        "CANDIDATE_READY",
    ]
    result = _run(command)
    payload: dict[str, Any] = {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
        "parsed": None,
    }
    if result.returncode == 0 and result.stdout.strip():
        payload["parsed"] = json.loads(result.stdout)
    return payload


def build_delivery_self_check(*, python_exe: str) -> dict[str, Any]:
    branch = _git_value(["branch", "--show-current"])
    head = _git_value(["rev-parse", "HEAD"])
    status = _git_value(["status", "--short"])
    validation = _architecture_validation(python_exe)
    handoff = _handoff(python_exe)
    parsed_validation = validation.get("parsed") or {}
    validation_results = parsed_validation.get("results", [])
    if not isinstance(validation_results, list):
        validation_results = []
    parsed_handoff = handoff.get("parsed") or {}
    if not isinstance(parsed_handoff, dict):
        parsed_handoff = {}
    repository_checks = {
        "git_branch_present": bool(branch),
        "git_head_present": bool(head),
        "worktree_clean": status == "",
        "architecture_validation_executed": validation["returncode"] == 0,
        "handoff_export_executed": handoff["returncode"] == 0,
    }
    boundary_checks = {
        "live_install_modified": False,
        "live_config_modified": False,
        "live_database_modified": False,
        "merge_executed": False,
        "deploy_executed": False,
        "skill_promoted": False,
    }
    missing_passes = sorted(
        set(RECENT_PASSES)
        - {
            str(item.get("pass_id"))
            for item in validation_results
            if isinstance(item, dict)
        }
    )
    validation_failures = [
        str(item.get("pass_id", "UNKNOWN"))
        for item in validation_results
        if isinstance(item, dict) and item.get("verdict") != "ADMIT_SHADOW_ONLY"
    ]
    handoff_ok = (
        parsed_handoff.get("artifact_type") == "WLS_DELIVERY_HANDOFF_PACKAGE"
        and (parsed_handoff.get("candidate") or {}).get("commit") == head
    )
    ok = (
        all(repository_checks.values())
        and not any(boundary_checks.values())
        and not missing_passes
        and not validation_failures
        and handoff_ok
    )
    return {
        "receipt_type": "DELIVERY_SELF_CHECK",
        "status": "PASS" if ok else "FAIL",
        "candidate": {"branch": branch, "commit": head},
        "repository_checks": repository_checks,
        "boundary_checks": boundary_checks,
        "architecture_validation": validation,
        "handoff": handoff,
        "failure_groups": {
            "missing_passes": missing_passes,
            "validation_failures": validation_failures,
            "handoff_failures": [] if handoff_ok else ["handoff_commit_or_type"],
            "repository_failures": [
                key for key, value in repository_checks.items() if value is not True
            ],
            "boundary_failures": [
                key for key, value in boundary_checks.items() if value is True
            ],
        },
        "claim_ceiling": (
            "candidate delivery self-check only; does not run campaigns, install, "
            "merge, deploy, promote Skills, or prove Owner-host longitudinal readiness"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run WLS candidate delivery self-check")
    parser.add_argument("--python-exe", default=sys.executable)
    args = parser.parse_args(argv)
    payload = build_delivery_self_check(python_exe=args.python_exe)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
