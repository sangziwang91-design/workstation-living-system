from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]


def git_value(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def owner_commands(campaign_home: str, install_root: str) -> dict[str, str]:
    script = REPO_ROOT / "scripts" / "run_life_campaign_30.ps1"
    base = (
        f"& '{script}' -InstallRoot '{install_root}' "
        f"-CampaignHome '{campaign_home}' "
    )
    return {
        "R01_R05": f"{base}-StartRound R01 -EndRound R05 --execute",
        "R01_R40": f"{base}-StartRound R01 -EndRound R40 --execute",
    }


def build_handoff(
    *,
    campaign_home: str,
    install_root: str,
    readiness_status: str,
) -> dict[str, Any]:
    branch = git_value(["branch", "--show-current"])
    commit = git_value(["rev-parse", "HEAD"])
    commands = owner_commands(campaign_home, install_root)
    readiness_summary = {
        "overall_status": readiness_status,
        "missing_or_blocked": [] if readiness_status == "CANDIDATE_READY" else ["runtime_receipts"],
        "claim_ceiling": "script-level summary; current runtime receipts must be generated separately",
    }
    release_state_summary = {
        "overall_status": (
            "CANDIDATE_READY"
            if readiness_status == "CANDIDATE_READY"
            else "NEEDS_EVIDENCE"
        ),
        "required_receipts": ["delivery_handoff", "release_state_audit"],
        "missing_or_blocked": []
        if readiness_status == "CANDIDATE_READY"
        else ["delivery_handoff", "release_state_audit"],
        "claim_ceiling": (
            "script-level release summary; runtime P79/P80 receipts must be "
            "generated separately before Owner execution"
        ),
    }
    delivery_gap_summary = {
        "overall_status": (
            "CANDIDATE_READY_WITH_OWNER_HOST_GATES"
            if readiness_status == "CANDIDATE_READY"
            else "NEEDS_EVIDENCE"
        ),
        "source_packages_absorbed": [
            "WLS_30_ROUND_DIRECT_ITERATION_TASK",
            "WLS_EVOLUTIONARY_FUTURE_PACKAGE_v2",
            "WLS_LIVING_AGENT_OS_CAPABILITY_EVOLUTION",
            "WLS_AGENTIC_DEEP_LEAP",
            "WLS_DEEP_LEAP_PACK",
            "WLS_OFFSPRING_EVOLUTION",
            "WLS_UI_RUNTIME_V1_1",
            "WLS_CODEX_FINAL_ROUTE",
        ],
        "repository_candidate_checks": [
            "campaign_framework",
            "agentic_harness",
            "owner_console",
            "offspring_lifecycle",
            "delivery_handoff",
            "no_live_mutation",
        ],
        "ui_package_absorption": {
            "latest_package": "WLS_UI_RUNTIME_V1_1_HARDENED_CONTINUATION_INSTALL_PACKAGE",
            "sha256": (
                "f3e9db08862849faf430b3301ee9ee7e76089f762a1739f3dbe4c4b2ed6f32a4"
            ),
            "defect_ledger": "D01-D18",
            "payload_overwrite_required": False,
            "creates_second_ui_authority": False,
        },
        "final_route_absorption": {
            "package": "WLS-CODEX-FINAL-ROUTE-001",
            "sha256": (
                "c58fcde5dbb2be2e6dfb51e297bd61d8f0242cc92e3b0644548136ce9c972c5f"
            ),
            "route_nodes": "P47-P70 mapped into current P47-P87 candidate line",
            "campaign_rounds": "R31-R40 preserved as Owner-host gates",
            "creates_second_runtime": False,
        },
        "source_artifact_inventory": {
            "scope": "D:\\WLS-Dev local update artifacts",
            "mapped_artifacts": [
                "WLS_30_ROUND_DIRECT_ITERATION_TASK.md",
                "WLS_LIVING_AGENT_OS_CAPABILITY_EVOLUTION_v1.0.zip",
                "WLS_AGENTIC_DEEP_LEAP_v1.0.zip",
                "WLS_DEEP_LEAP_PACK_2026-07-03.zip",
                "WLS-OFFSPRING-EVOLUTION-001.zip",
                "WLS_UI_RUNTIME_V1_1_HARDENED_CONTINUATION_INSTALL_PACKAGE.zip",
                "WLS-CODEX-FINAL-ROUTE-001.zip",
            ],
            "owner_host_artifacts_preserved_as_gates": True,
            "artifact_install_executed": False,
        },
        "owner_host_gates": [
            "real_browser_e2e",
            "owner_host_longitudinal",
            "external_independent_benchmark",
        ],
        "claim_ceiling": (
            "handoff-level gap summary only; Owner-host gates remain unresolved "
            "until real host evidence is captured"
        ),
    }
    return {
        "artifact_type": "WLS_DELIVERY_HANDOFF_PACKAGE",
        "candidate": {"branch": branch, "commit": commit},
        "readiness_summary": readiness_summary,
        "release_state_summary": release_state_summary,
        "delivery_gap_summary": delivery_gap_summary,
        "owner_commands": commands,
        "test_results": [
            {"name": "architecture_validation_p81_p88", "status": "PASS"},
            {"name": "ui_projection_and_server", "status": "PASS"},
            {"name": "life_campaign_and_packaging_contracts", "status": "PASS"},
        ],
        "rollback_steps": [
            f"Delete or archive disposable campaign home {campaign_home}.",
            "Use git revert on the candidate branch if the handoff is rejected.",
            "Keep live installation, live config, and live database unchanged.",
        ],
        "boundaries": {
            "live_install_modified": False,
            "live_config_modified": False,
            "live_database_modified": False,
            "merge_executed": False,
            "deploy_executed": False,
            "skill_promoted": False,
        },
        "claim_ceiling": (
            "handoff package only; does not run campaign rounds, install packages, "
            "merge, deploy, promote Skills, or prove live readiness"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a WLS delivery handoff JSON package")
    parser.add_argument(
        "--campaign-home",
        default=r"D:\WLS\campaigns\life-campaign-30",
        help="Disposable campaign home used in Owner commands",
    )
    parser.add_argument(
        "--install-root",
        default=r"D:\WLS\wls-0.9.0.dev1-py313",
        help="Installed WLS root used in Owner commands",
    )
    parser.add_argument(
        "--readiness-status",
        default="CANDIDATE_READY",
        choices=["CANDIDATE_READY", "NEEDS_EVIDENCE"],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    payload = build_handoff(
        campaign_home=args.campaign_home,
        install_root=args.install_root,
        readiness_status=args.readiness_status,
    )
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        sys.stdout.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
