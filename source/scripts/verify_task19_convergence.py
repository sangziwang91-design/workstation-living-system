from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time


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


def _run(name: str, command: list[str], timeout: int) -> dict[str, Any]:
    started_wall = datetime.now(UTC).isoformat()
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
        status = "PASS" if completed.returncode == 0 else "FAIL"
        result = {
            "name": name,
            "command": command,
            "cwd": str(REPOSITORY),
            "status": status,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-100_000:],
            "stderr": completed.stderr[-100_000:],
        }
    except FileNotFoundError as exc:
        result = {
            "name": name,
            "command": command,
            "cwd": str(REPOSITORY),
            "status": "UNAVAILABLE",
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        result = {
            "name": name,
            "command": command,
            "cwd": str(REPOSITORY),
            "status": "TIMEOUT",
            "exit_code": None,
            "stdout": (exc.stdout or "")[-100_000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-100_000:] if isinstance(exc.stderr, str) else "",
        }
    result["started_at"] = started_wall
    result["finished_at"] = datetime.now(UTC).isoformat()
    result["duration_seconds"] = round(time.monotonic() - started, 6)
    return result


def _pytest_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "error"):
        match = re.search(rf"(\d+)\s+{key}\b", text)
        if match:
            normalized = "errors" if key == "error" else key
            counts[normalized] = int(match.group(1))
    return counts


def _json_from_output(result: dict[str, Any]) -> Any | None:
    text = result.get("stdout", "").strip()
    if not text:
        return None
    positions = [index for index, char in enumerate(text) if char == "{"]
    for index in positions:
        try:
            return json.loads(text[index:])
        except json.JSONDecodeError:
            continue
    return None


def _markdown(report: dict[str, Any]) -> str:
    rows = [
        "# Task19 Convergence Verification",
        "",
        f"- Head: `{report['head_sha']}`",
        f"- Branch: `{report['branch']}`",
        f"- Started: {report['started_at']}",
        f"- Finished: {report['finished_at']}",
        f"- Passed: **{report['passed']}**",
        f"- GitHub-hosted Actions: `{report['github_hosted_actions']}`",
        "",
        "## Gates",
        "",
        "| Gate | Status | Exit | Seconds |",
        "|---|---:|---:|---:|",
    ]
    for item in report["commands"]:
        rows.append(
            f"| `{item['name']}` | {item['status']} | {item['exit_code']} | {item['duration_seconds']} |"
        )
    rows.extend(
        [
            "",
            "## Test counts",
            "",
            "```json",
            json.dumps(report["test_counts"], indent=2, sort_keys=True),
            "```",
            "",
            "## Artifact and soak evidence",
            "",
            "```json",
            json.dumps(
                {
                    "wheel": report.get("wheel"),
                    "clean_install": report.get("clean_install"),
                    "soak": report.get("soak"),
                },
                indent=2,
                sort_keys=True,
                default=str,
            ),
            "```",
            "",
            "## Claim ceiling",
            "",
            report["claim_ceiling"],
            "",
            "This report does not authorize merge, deployment, plugin enablement on the owner host, or Task20.",
        ]
    )
    return "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-soak", action="store_true")
    args = parser.parse_args()
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat()
    head_sha = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current") or "DETACHED"
    initial_status = _git("status", "--short")
    commands: list[dict[str, Any]] = []

    compile_targets = ["source/src/wls", "source/tests", "source/scripts"]
    if (REPOSITORY / "scripts").exists():
        compile_targets.append("scripts")
    commands.append(
        _run(
            "compileall",
            [sys.executable, "-m", "compileall", "-q", *compile_targets],
            300,
        )
    )
    commands.append(
        _run(
            "packaging_layout",
            [sys.executable, "source/scripts/verify_packaging_layout.py"],
            120,
        )
    )
    commands.append(
        _run("pytest_full", [sys.executable, "-m", "pytest", "source/tests", "-q"], 1800)
    )
    for target in range(1, 5):
        commands.append(
            _run(
                f"evolution_target_{target:03d}",
                [sys.executable, f"source/scripts/verify_evolution_target_{target:03d}.py"],
                900,
            )
        )
    commands.append(
        _run(
            "ruff",
            [sys.executable, "-m", "ruff", "check", "source/src", "source/tests", "source/scripts"],
            600,
        )
    )
    commands.append(
        _run(
            "mypy",
            [
                sys.executable,
                "-m",
                "mypy",
                "source/src/wls",
                "source/tests",
                "source/scripts",
                "--ignore-missing-imports",
            ],
            900,
        )
    )
    commands.append(
        _run(
            "bandit",
            [sys.executable, "-m", "bandit", "-q", "-r", "source/src/wls", "source/scripts"],
            600,
        )
    )

    for path in (REPOSITORY / "dist", REPOSITORY / "build"):
        if path.exists():
            shutil.rmtree(path)
    commands.append(_run("build_root", [sys.executable, "-m", "build", "."], 900))
    wheels = sorted((REPOSITORY / "dist").glob("*.whl"))
    wheel_info: dict[str, Any] | None = None
    if len(wheels) == 1:
        wheel = wheels[0]
        clean = _run(
            "clean_install",
            [sys.executable, "source/scripts/verify_clean_install.py", "--wheel", str(wheel)],
            900,
        )
        commands.append(clean)
        clean_report = _json_from_output(clean)
        if isinstance(clean_report, dict):
            wheel_info = {
                "filename": wheel.name,
                "sha256": clean_report.get("wheel_sha256"),
                "size_bytes": clean_report.get("wheel_size_bytes"),
            }
    else:
        commands.append(
            {
                "name": "clean_install",
                "command": [],
                "cwd": str(REPOSITORY),
                "status": "FAIL",
                "exit_code": 1,
                "stdout": "",
                "stderr": f"expected exactly one wheel, found {len(wheels)}",
                "started_at": datetime.now(UTC).isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "duration_seconds": 0.0,
            }
        )

    if not args.skip_soak:
        commands.append(
            _run(
                "bounded_soak_100",
                [sys.executable, "source/scripts/run_bounded_soak.py", "100"],
                1800,
            )
        )

    pytest_result = next(item for item in commands if item["name"] == "pytest_full")
    test_counts = _pytest_counts(
        f"{pytest_result.get('stdout', '')}\n{pytest_result.get('stderr', '')}"
    )
    clean_result = next((item for item in commands if item["name"] == "clean_install"), None)
    soak_result = next((item for item in commands if item["name"] == "bounded_soak_100"), None)
    clean_report = _json_from_output(clean_result) if clean_result else None
    soak_report = _json_from_output(soak_result) if soak_result else None
    mandatory = [item for item in commands if item["name"] != "bounded_soak_100" or not args.skip_soak]
    passed = bool(
        mandatory
        and all(item["status"] == "PASS" for item in mandatory)
        and test_counts.get("failed", 0) == 0
        and test_counts.get("errors", 0) == 0
        and bool(test_counts)
        and isinstance(clean_report, dict)
        and clean_report.get("passed") is True
        and (
            args.skip_soak
            or (isinstance(soak_report, dict) and soak_report.get("passed") is True)
        )
    )
    finished_at = datetime.now(UTC).isoformat()
    report = {
        "schema_version": "1.0",
        "task": "TASK19_MAIN_REBUILD_CONVERGENCE",
        "repository": "sangziwang91-design/workstation-living-system-private",
        "head_sha": head_sha,
        "branch": branch,
        "started_at": started_at,
        "finished_at": finished_at,
        "initial_git_status": initial_status,
        "final_git_status": _git("status", "--short"),
        "python": sys.version,
        "platform": sys.platform,
        "github_hosted_actions": "DEFERRED_CAPACITY_UNAVAILABLE",
        "execution_surface": "local or self-hosted Windows runner",
        "commands": commands,
        "test_counts": test_counts,
        "failed_gates": [item["name"] for item in commands if item["status"] != "PASS"],
        "unavailable_gates": [item["name"] for item in commands if item["status"] == "UNAVAILABLE"],
        "wheel": wheel_info,
        "clean_install": clean_report,
        "soak": soak_report,
        "passed": passed,
        "claim_ceiling": (
            "Exact-head local verification of the tested repository, clean Wheel, and a 100-cycle "
            "bounded workload with three runtime re-instantiations. This is not production, "
            "longitudinal, owner-host, or unrestricted-autonomy proof."
        ),
    }
    name = f"TASK19_CONVERGENCE_{head_sha[:12]}"
    json_path = ARTIFACT_ROOT / f"{name}.json"
    md_path = ARTIFACT_ROOT / f"{name}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"passed": passed, "json": str(json_path), "markdown": str(md_path), "head_sha": head_sha, "failed_gates": report["failed_gates"]}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
