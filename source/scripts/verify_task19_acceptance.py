from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import argparse
import json
import subprocess
import sys


REPOSITORY = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = REPOSITORY / "artifacts" / "task19"


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    return completed.stdout.strip()


def _run(command: list[str], timeout: int) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-100_000:],
        "stderr": completed.stderr[-100_000:],
    }


def _json_from_text(text: str) -> dict[str, Any] | None:
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value = json.loads(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Task19 Exact-Head Acceptance",
            "",
            f"- Head: `{report['head_sha']}`",
            f"- Branch: `{report['branch']}`",
            f"- Diagnostic only: **{report['diagnostic_only']}**",
            f"- Inner convergence gate: **{report['inner_gate_passed']}**",
            f"- Wheel metadata gate: **{report['wheel_metadata_passed']}**",
            f"- Head unchanged: **{report['head_unchanged']}**",
            f"- Worktree clean: **{report['worktree_clean']}**",
            f"- Acceptance ready: **{report['acceptance_ready']}**",
            "",
            "## Failures",
            "",
            "```json",
            json.dumps(report["failed_gates"], indent=2, sort_keys=True),
            "```",
            "",
            "## Wheel",
            "",
            "```json",
            json.dumps(report["wheel_metadata"], indent=2, sort_keys=True),
            "```",
            "",
            "## Claim ceiling",
            "",
            report["claim_ceiling"],
            "",
            "This report does not authorize merge, provider attachment, deployment, or Task20.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-soak", action="store_true")
    args = parser.parse_args()

    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current") or "DETACHED"
    initial_status = _git("status", "--short")
    inner_command = [
        sys.executable,
        "source/scripts/verify_task19_convergence.py",
    ]
    if args.skip_soak:
        inner_command.append("--skip-soak")
    inner = _run(inner_command, 2400)
    inner_summary = _json_from_text(inner["stdout"])
    inner_report: dict[str, Any] | None = None
    if inner_summary and inner_summary.get("json"):
        report_path = Path(str(inner_summary["json"]))
        if report_path.is_file():
            value = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                inner_report = value

    wheels = sorted((REPOSITORY / "dist").glob("*.whl"))
    if len(wheels) == 1:
        wheel_result = _run(
            [
                sys.executable,
                "source/scripts/verify_wheel_metadata.py",
                "--wheel",
                str(wheels[0]),
            ],
            300,
        )
        wheel_metadata = _json_from_text(wheel_result["stdout"])
    else:
        wheel_result = {
            "command": [],
            "exit_code": 1,
            "stdout": "",
            "stderr": f"expected exactly one Wheel, found {len(wheels)}",
        }
        wheel_metadata = None

    final_head = _git("rev-parse", "HEAD")
    final_status = _git("status", "--short")
    head_unchanged = final_head == head
    worktree_clean = not final_status
    inner_gate_passed = bool(
        inner["exit_code"] == 0
        and inner_report
        and inner_report.get("passed") is True
        and inner_report.get("head_sha") == head
    )
    wheel_metadata_passed = bool(
        wheel_result["exit_code"] == 0
        and wheel_metadata
        and wheel_metadata.get("passed") is True
    )
    failed_gates: list[str] = []
    if initial_status:
        failed_gates.append("initial_clean_worktree")
    if not inner_gate_passed:
        failed_gates.append("inner_convergence_gate")
    if not wheel_metadata_passed:
        failed_gates.append("wheel_metadata")
    if not head_unchanged:
        failed_gates.append("head_unchanged")
    if not worktree_clean:
        failed_gates.append("final_clean_worktree")
    diagnostic_only = bool(args.skip_soak)
    all_passed = not failed_gates
    acceptance_ready = all_passed and not diagnostic_only
    report = {
        "schema_version": "1.0",
        "task": "TASK19_EXACT_HEAD_ACCEPTANCE",
        "repository": "sangziwang91-design/workstation-living-system-private",
        "head_sha": head,
        "final_head_sha": final_head,
        "branch": branch,
        "generated_at": datetime.now(UTC).isoformat(),
        "diagnostic_only": diagnostic_only,
        "inner_command": inner,
        "inner_summary": inner_summary,
        "inner_report": inner_report,
        "inner_gate_passed": inner_gate_passed,
        "wheel_result": wheel_result,
        "wheel_metadata": wheel_metadata,
        "wheel_metadata_passed": wheel_metadata_passed,
        "head_unchanged": head_unchanged,
        "worktree_clean": worktree_clean,
        "initial_status": initial_status,
        "final_status": final_status,
        "failed_gates": failed_gates,
        "all_passed": all_passed,
        "acceptance_ready": acceptance_ready,
        "claim_ceiling": (
            "Exact-head local repository verification, clean Wheel verification, and "
            "a bounded 100-cycle workload when diagnostic_only is false. This is not "
            "production, longitudinal, owner-host, or unrestricted-autonomy proof."
        ),
    }
    stem = f"TASK19_ACCEPTANCE_{head[:12]}"
    json_path = ARTIFACT_ROOT / f"{stem}.json"
    md_path = ARTIFACT_ROOT / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "acceptance_ready": acceptance_ready,
                "diagnostic_only": diagnostic_only,
                "head_sha": head,
                "json": str(json_path),
                "markdown": str(md_path),
                "failed_gates": failed_gates,
            },
            indent=2,
        )
    )
    if acceptance_ready:
        return 0
    if diagnostic_only and all_passed:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
