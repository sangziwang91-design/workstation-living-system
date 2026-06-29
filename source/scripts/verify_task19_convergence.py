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


def _gate(
    name: str,
    *,
    status: str,
    command: list[str] | None = None,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    started_at: str | None = None,
    duration_seconds: float = 0.0,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "name": name,
        "command": command or [],
        "cwd": str(REPOSITORY),
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout[-100_000:],
        "stderr": stderr[-100_000:],
        "started_at": started_at or now,
        "finished_at": now,
        "duration_seconds": round(duration_seconds, 6),
    }


def _run(name: str, command: list[str], timeout: int) -> dict[str, Any]:
    started_at = datetime.now(UTC).isoformat()
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
        return _gate(
            name,
            status="PASS" if completed.returncode == 0 else "FAIL",
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            started_at=started_at,
            duration_seconds=time.monotonic() - started,
        )
    except FileNotFoundError as exc:
        return _gate(
            name,
            status="UNAVAILABLE",
            command=command,
            stderr=str(exc),
            started_at=started_at,
            duration_seconds=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as exc:
        return _gate(
            name,
            status="TIMEOUT",
            command=command,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            started_at=started_at,
            duration_seconds=time.monotonic() - started,
        )


def _state_gate(name: str, passed: bool, details: str) -> dict[str, Any]:
    return _gate(
        name,
        status="PASS" if passed else "FAIL",
        exit_code=0 if passed else 1,
        stdout=details if passed else "",
        stderr="" if passed else details,
    )


def _json_from_output(gate: dict[str, Any] | None) -> dict[str, Any] | None:
    if gate is None:
        return None
    text = str(gate.get("stdout", "")).strip()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value = json.loads(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _pytest_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "error"):
        matches = re.findall(rf"(\d+)\s+{key}\b", text)
        if matches:
            counts["errors" if key == "error" else key] = max(
                int(value) for value in matches
            )
    return counts


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Task19 Exact-Head Verification",
        "",
        f"- Head: `{report['head_sha']}`",
        f"- Branch: `{report['branch']}`",
        f"- Diagnostic only: **{report['diagnostic_only']}**",
        f"- All executed gates passed: **{report['all_executed_gates_passed']}**",
        f"- Acceptance ready: **{report['acceptance_ready']}**",
        "",
        "## Gates",
        "",
        "| Gate | Status | Exit | Seconds |",
        "|---|---:|---:|---:|",
    ]
    for item in report["gates"]:
        lines.append(
            f"| `{item['name']}` | {item['status']} | "
            f"{item['exit_code']} | {item['duration_seconds']} |"
        )
    lines.extend(
        [
            "",
            "## Evidence summary",
            "",
            "```json",
            json.dumps(
                {
                    "test_counts": report["test_counts"],
                    "wheel_metadata": report["wheel_metadata"],
                    "clean_install": report["clean_install"],
                    "soak": report["soak"],
                    "head_unchanged": report["head_unchanged"],
                    "worktree_clean": report["worktree_clean"],
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
            "This report does not authorize merge, deployment, provider attachment, or Task20.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    stem = f"TASK19_CONVERGENCE_{report['head_sha'][:12]}"
    json_path = ARTIFACT_ROOT / f"{stem}.json"
    markdown_path = ARTIFACT_ROOT / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-soak", action="store_true")
    args = parser.parse_args()

    started_at = datetime.now(UTC).isoformat()
    head_sha = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current") or "DETACHED"
    initial_status = _git("status", "--short")
    gates: list[dict[str, Any]] = [
        _state_gate("initial_clean_worktree", not initial_status, initial_status or "clean")
    ]

    if not initial_status:
        compile_targets = ["source/src/wls", "source/tests", "source/scripts", "scripts"]
        gates.extend(
            [
                _run(
                    "compileall",
                    [sys.executable, "-m", "compileall", "-q", *compile_targets],
                    300,
                ),
                _run(
                    "packaging_layout",
                    [sys.executable, "source/scripts/verify_packaging_layout.py"],
                    120,
                ),
                _run(
                    "pytest_full",
                    [sys.executable, "-m", "pytest", "source/tests", "-q"],
                    1800,
                ),
            ]
        )
        for target in range(1, 5):
            gates.append(
                _run(
                    f"evolution_target_{target:03d}",
                    [
                        sys.executable,
                        f"source/scripts/verify_evolution_target_{target:03d}.py",
                    ],
                    900,
                )
            )
        gates.extend(
            [
                _run(
                    "ruff",
                    [
                        sys.executable,
                        "-m",
                        "ruff",
                        "check",
                        "source/src",
                        "source/tests",
                        "source/scripts",
                    ],
                    600,
                ),
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
                ),
                _run(
                    "bandit",
                    [
                        sys.executable,
                        "-m",
                        "bandit",
                        "-q",
                        "-r",
                        "source/src/wls",
                        "source/scripts",
                    ],
                    600,
                ),
            ]
        )

        for path in (REPOSITORY / "dist", REPOSITORY / "build"):
            if path.exists():
                shutil.rmtree(path)
        gates.append(_run("build_root", [sys.executable, "-m", "build", "."], 900))
        wheels = sorted((REPOSITORY / "dist").glob("*.whl"))
        if len(wheels) == 1:
            wheel = wheels[0]
            gates.append(
                _run(
                    "wheel_metadata",
                    [
                        sys.executable,
                        "source/scripts/verify_wheel_metadata.py",
                        "--wheel",
                        str(wheel),
                    ],
                    300,
                )
            )
            gates.append(
                _run(
                    "clean_install",
                    [
                        sys.executable,
                        "source/scripts/verify_clean_install.py",
                        "--wheel",
                        str(wheel),
                    ],
                    900,
                )
            )
        else:
            details = f"expected exactly one Wheel, found {len(wheels)}"
            gates.append(_state_gate("wheel_metadata", False, details))
            gates.append(_state_gate("clean_install", False, details))

        if not args.skip_soak:
            gates.append(
                _run(
                    "bounded_soak_100",
                    [sys.executable, "source/scripts/run_bounded_soak.py", "100"],
                    1800,
                )
            )

    final_head = _git("rev-parse", "HEAD")
    final_status = _git("status", "--short")
    head_unchanged = final_head == head_sha
    worktree_clean = not final_status
    gates.extend(
        [
            _state_gate(
                "final_head_unchanged",
                head_unchanged,
                f"initial={head_sha}; final={final_head}",
            ),
            _state_gate("final_clean_worktree", worktree_clean, final_status or "clean"),
        ]
    )

    pytest_gate = next((item for item in gates if item["name"] == "pytest_full"), None)
    test_counts = _pytest_counts(
        f"{pytest_gate.get('stdout', '')}\n{pytest_gate.get('stderr', '')}"
        if pytest_gate
        else ""
    )
    wheel_metadata = _json_from_output(
        next((item for item in gates if item["name"] == "wheel_metadata"), None)
    )
    clean_install = _json_from_output(
        next((item for item in gates if item["name"] == "clean_install"), None)
    )
    soak = _json_from_output(
        next((item for item in gates if item["name"] == "bounded_soak_100"), None)
    )
    all_passed = bool(
        gates
        and all(item["status"] == "PASS" for item in gates)
        and bool(test_counts)
        and test_counts.get("failed", 0) == 0
        and test_counts.get("errors", 0) == 0
        and isinstance(wheel_metadata, dict)
        and wheel_metadata.get("passed") is True
        and isinstance(clean_install, dict)
        and clean_install.get("passed") is True
        and (
            args.skip_soak
            or (isinstance(soak, dict) and soak.get("passed") is True)
        )
    )
    diagnostic_only = bool(args.skip_soak)
    report = {
        "schema_version": "2.0",
        "task": "TASK19_MAIN_REBUILD_CONVERGENCE",
        "repository": "sangziwang91-design/workstation-living-system-private",
        "head_sha": head_sha,
        "final_head_sha": final_head,
        "branch": branch,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "diagnostic_only": diagnostic_only,
        "python": sys.version,
        "platform": sys.platform,
        "github_hosted_actions": "NOT_USED_AS_ACCEPTANCE_EVIDENCE",
        "gates": gates,
        "test_counts": test_counts,
        "wheel_metadata": wheel_metadata,
        "clean_install": clean_install,
        "soak": soak,
        "head_unchanged": head_unchanged,
        "worktree_clean": worktree_clean,
        "failed_gates": [item["name"] for item in gates if item["status"] != "PASS"],
        "unavailable_gates": [
            item["name"] for item in gates if item["status"] == "UNAVAILABLE"
        ],
        "all_executed_gates_passed": all_passed,
        "acceptance_ready": all_passed and not diagnostic_only,
        "claim_ceiling": (
            "Exact-head local verification of the tested repository, clean Wheel, "
            "persistent provenance integrity, and a bounded 100-cycle workload with "
            "three runtime re-instantiations. This is not production, longitudinal, "
            "owner-host, or unrestricted-autonomy proof."
        ),
    }
    json_path, markdown_path = _write_report(report)
    print(
        json.dumps(
            {
                "acceptance_ready": report["acceptance_ready"],
                "diagnostic_only": diagnostic_only,
                "head_sha": head_sha,
                "json": str(json_path),
                "markdown": str(markdown_path),
                "failed_gates": report["failed_gates"],
            },
            indent=2,
        )
    )
    if report["acceptance_ready"]:
        return 0
    if diagnostic_only and all_passed:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
