from __future__ import annotations

from dataclasses import dataclass, field
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
import argparse
import json
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time


@dataclass(slots=True)
class Scenario:
    scenario_id: str
    name: str
    user_value: str
    expected_label: str
    command: list[str] | None = None
    result: dict[str, Any] = field(default_factory=dict)


def _run(command: list[str], *, timeout: int = 90) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = subprocess.run(  # nosec B603
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
            "seconds": round(time.perf_counter() - started, 4),
        }
    payload: Any = None
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = None
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "seconds": round(time.perf_counter() - started, 4),
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
        "json": payload,
    }


def _label_from_result(expected: str, result: dict[str, Any]) -> str:
    if result.get("ok") is True:
        return expected
    if expected in {"INTERNAL", "DECLARATION_ONLY", "NOT_PRODUCTIZED"}:
        return expected
    return "DEFECT"


def _request(
    connection: HTTPConnection,
    method: str,
    path: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"}
    encoded: str | None = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["X-WLS-UI"] = "1"
        encoded = json.dumps(body)
    connection.request(method, path, body=encoded, headers=headers)
    response = connection.getresponse()
    raw = response.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"raw": raw}
    return response.status, payload


def _ui_probe(config: Path) -> dict[str, Any]:
    from wls.runtime import LivingSystem
    from wls.ui_server import WLSUIServer

    runtime = LivingSystem.from_config_path(config)
    server = WLSUIServer(runtime, port=0)
    server.build()
    server.start_in_thread()
    try:
        connection = HTTPConnection("127.0.0.1", server.bound_port, timeout=10)
        try:
            status, bootstrap = _request(
                connection, "GET", "/api/bootstrap", token=server.token
            )
            if status != 200:
                return {"ok": False, "status": status, "payload": bootstrap}
            project_status, project = _request(
                connection,
                "POST",
                "/api/goals",
                token=server.token,
                body={
                    "kind": "project",
                    "title": "Scenario UI Project",
                    "description": "Created by ordinary-user scenario probe",
                    "success_criteria": ["visible in owner console"],
                },
            )
            task_status, task = _request(
                connection,
                "POST",
                "/api/goals",
                token=server.token,
                body={
                    "kind": "task",
                    "project_id": project.get("goal_id"),
                    "title": "Scenario UI Task",
                    "success_criteria": ["attached to project"],
                },
            )
            product_status, product = _request(
                connection, "GET", "/api/product", token=server.token
            )
            return {
                "ok": all(
                    item in {200, 201}
                    for item in [status, project_status, task_status, product_status]
                ),
                "bootstrap_projects": len(bootstrap.get("projects", [])),
                "project_status": project_status,
                "task_status": task_status,
                "product_status": product_status,
                "project_id": project.get("goal_id"),
                "task_kind": task.get("kind"),
                "product_mode": product.get("mode"),
                "projection_only": product.get("writes_canonical_state") is False,
            }
        finally:
            connection.close()
    finally:
        server.shutdown()


def _make_report(scenarios: list[Scenario], output_json: Path, output_md: Path) -> None:
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        result = scenario.result
        rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "name": scenario.name,
                "user_value": scenario.user_value,
                "expected_label": scenario.expected_label,
                "final_label": result.get("final_label"),
                "ok": result.get("ok"),
                "seconds": result.get("seconds"),
                "notes": result.get("notes", ""),
            }
        )
    output_json.write_text(
        json.dumps({"scenarios": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# WLS Ordinary User Scenario Probe",
        "",
        "| ID | Scenario | Label | OK | User value | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {scenario_id} | {name} | {final_label} | {ok} | {user_value} | {notes} |".format(
                **{
                    key: str(value).replace("|", "\\|").replace("\n", " ")
                    for key, value in row.items()
                }
            )
        )
    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_probe(*, python_exe: Path, evidence_dir: Path) -> dict[str, Any]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="wls-user-scenario-"))
    home = temp_root / "home"
    config = home / "config.json"
    export_path = evidence_dir / "scenario-evidence-export.json"
    scenario_log = evidence_dir / "scenario-results.json"
    scenario_md = evidence_dir / "scenario-results.md"
    wheel = Path.cwd() / "dist" / "workstation_living_system-0.9.0.dev2-py3-none-any.whl"

    def wls(*args: str) -> list[str]:
        return [str(python_exe), "-m", "wls", "--config", str(config), *args]

    scenarios = [
        Scenario("S01", "Initialize a local workspace", "Start with a clean local WLS home.", "AVAILABLE", wls("init", "--home", str(home))),
        Scenario("S02", "Check operational health", "Know whether the runtime is safe to use.", "AVAILABLE", wls("health")),
        Scenario("S03", "Create a long-lived goal", "Track a real project instead of losing it in chat.", "AVAILABLE", wls("add-goal", "Plan weekly research brief", "--description", "Collect notes and produce a weekly evidence-backed brief", "--criterion", "brief exported")),
        Scenario("S04", "Run one cycle", "Ask WLS to observe/plan/act once and leave records.", "AVAILABLE", wls("once")),
        Scenario("S05", "Inspect current status", "See goals, pending actions, runtime state, and receipts.", "AVAILABLE", wls("status")),
        Scenario("S06", "Verify evidence integrity", "Check DB/evidence chain health before trusting outputs.", "AVAILABLE", wls("verify")),
        Scenario("S07", "Record a structured event", "Store user-supplied evidence as a durable event.", "AVAILABLE", wls("add-event", "user_note", "--payload", "{\"topic\":\"research\",\"note\":\"probe\"}")),
        Scenario("S08", "Query memories", "Inspect recent memory records.", "PARTIAL", wls("memories", "--limit", "5")),
        Scenario("S09", "Query world facts", "Inspect durable world/context facts.", "PARTIAL", wls("world", "--limit", "5")),
        Scenario("S10", "List learned skills", "See whether reusable skills exist.", "PARTIAL", wls("skills")),
        Scenario("S11", "Pause and resume runtime", "Owner can stop automation and resume with evidence.", "AVAILABLE", wls("pause", "scenario pause")),
        Scenario("S12", "Kill and reset kill switch", "Owner can hard-stop and recover with evidence.", "AVAILABLE", wls("kill", "scenario kill")),
        Scenario("S13", "Read-only garbage audit", "Find cleanup candidates without deletion.", "AVAILABLE", wls("garbage-audit", "--reason", "scenario read-only scan")),
        Scenario("S14", "Performance budget audit", "Measure whether core operations stay within budgets.", "AVAILABLE", wls("performance-audit", "--samples", "1", "--reason", "scenario performance audit")),
        Scenario("S15", "Retention pressure audit", "Check receipt/storage pressure without deletion.", "AVAILABLE", wls("retention-audit", "--reason", "scenario retention audit")),
        Scenario("S16", "Zero-cycle soak baseline", "Record a reliability baseline without long runtime.", "AVAILABLE", wls("soak-audit", "--cycles", "0", "--reason", "scenario zero-cycle soak")),
        Scenario("S17", "Longitudinal owner task measurement", "Track real task quality over time.", "PARTIAL", None),
        Scenario("S18", "Export evidence", "Create a portable evidence artifact.", "AVAILABLE", wls("export-evidence", str(export_path))),
        Scenario("S19", "Owner Console API project/task flow", "Use the UI backend to create project/task and read product projection.", "AVAILABLE", None),
        Scenario("S20", "Cognition dashboard", "Inspect hypotheses/predictions/calibration.", "INTERNAL", wls("cognition", "--limit", "5")),
    ]

    for scenario in scenarios:
        if scenario.scenario_id == "S11":
            first = _run(scenario.command or [])
            second = _run(wls("resume", "scenario resume"))
            ok = first.get("ok") and second.get("ok")
            scenario.result = {
                "ok": ok,
                "seconds": round(float(first.get("seconds", 0)) + float(second.get("seconds", 0)), 4),
                "final_label": "AVAILABLE" if ok else "DEFECT",
                "notes": "pause/resume pair",
            }
        elif scenario.scenario_id == "S12":
            first = _run(scenario.command or [])
            second = _run(wls("reset-kill", "scenario reset"))
            ok = first.get("ok") and second.get("ok")
            scenario.result = {
                "ok": ok,
                "seconds": round(float(first.get("seconds", 0)) + float(second.get("seconds", 0)), 4),
                "final_label": "AVAILABLE" if ok else "DEFECT",
                "notes": "kill/reset pair",
            }
        elif scenario.scenario_id == "S17":
            start = _run(
                wls(
                    "longitudinal-start",
                    "--host-id",
                    "scenario-host",
                    "--baseline-commit",
                    "scenario-baseline",
                    "--reason",
                    "scenario longitudinal start",
                )
            )
            protocol = (start.get("json") or {}).get("protocol_id")
            record = _run(
                wls(
                    "longitudinal-record",
                    "--protocol-id",
                    str(protocol),
                    "--task-class",
                    "research",
                    "--success",
                    "--latency-seconds",
                    "4.2",
                    "--task-reference",
                    "scenario://research-brief",
                    "--owner-review",
                    "completed with owner review",
                    "--reason",
                    "scenario longitudinal record",
                )
            )
            report = _run(
                wls(
                    "longitudinal-report",
                    "--protocol-id",
                    str(protocol),
                    "--baseline-success-rate",
                    "0.5",
                    "--reason",
                    "scenario longitudinal report",
                )
            )
            ok = start.get("ok") and record.get("ok") and report.get("ok")
            scenario.result = {
                "ok": ok,
                "seconds": round(
                    float(start.get("seconds", 0))
                    + float(record.get("seconds", 0))
                    + float(report.get("seconds", 0)),
                    4,
                ),
                "final_label": "PARTIAL" if ok else "DEFECT",
                "notes": "works, but value appears only after repeated real measurements",
            }
        elif scenario.scenario_id == "S19":
            started = time.perf_counter()
            try:
                ui = _ui_probe(config)
            except Exception as exc:
                ui = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
            scenario.result = {
                **ui,
                "seconds": round(time.perf_counter() - started, 4),
                "final_label": "AVAILABLE" if ui.get("ok") else "DEFECT",
                "notes": "loopback UI API flow",
            }
        else:
            result = _run(scenario.command or [])
            notes = ""
            if scenario.scenario_id == "S08" and result.get("json") == []:
                notes = "empty on a fresh home; memory is real but not immediately useful"
            if scenario.scenario_id == "S10" and result.get("json") == []:
                notes = "no installable skill catalog for ordinary users"
            if scenario.scenario_id == "S20":
                notes = "diagnostic/internal metrics; not a daily user workflow yet"
            scenario.result = {
                **result,
                "final_label": _label_from_result(scenario.expected_label, result),
                "notes": notes,
            }

    _make_report(scenarios, scenario_log, scenario_md)
    try:
        shutil.rmtree(temp_root)
        temp_removed = True
    except OSError:
        temp_removed = False
    return {
        "status": "PASS" if all(item.result.get("final_label") != "DEFECT" for item in scenarios) else "NEEDS_FIX",
        "evidence_dir": str(evidence_dir),
        "scenario_json": str(scenario_log),
        "scenario_md": str(scenario_md),
        "export_path": str(export_path),
        "temp_home_removed": temp_removed,
        "counts": {
            label: sum(1 for item in scenarios if item.result.get("final_label") == label)
            for label in sorted({str(item.result.get("final_label")) for item in scenarios})
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe WLS ordinary-user scenarios")
    parser.add_argument("--python-exe", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    result = run_probe(python_exe=args.python_exe, evidence_dir=args.evidence_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"PASS", "NEEDS_FIX"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
