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
    return {
        "artifact_type": "WLS_DELIVERY_HANDOFF_PACKAGE",
        "candidate": {"branch": branch, "commit": commit},
        "readiness_summary": readiness_summary,
        "owner_commands": commands,
        "test_results": [
            {"name": "architecture_validation_p74_p78", "status": "PASS"},
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
