from __future__ import annotations

from pathlib import Path
from typing import Any
import csv
import io
import os
import shutil

# Subprocess calls use fixed or allowlisted commands with shell=False.
import subprocess  # nosec B404

from .base import Sensor
from ..schemas import Observation


def list_processes() -> dict[str, dict[str, Any]]:
    psutil_result: dict[str, dict[str, Any]] | None = None
    try:
        import psutil  # type: ignore

        psutil_result = {}
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            info = process.info
            psutil_result[str(info["pid"])] = {
                "name": info.get("name") or "",
                "cmdline": info.get("cmdline") or [],
            }
    except Exception:
        psutil_result = None
    if psutil_result is not None:
        return psutil_result
    if os.name == "nt":
        tasklist = shutil.which("tasklist") or str(
            Path(os.environ.get("SYSTEMROOT", "C:/Windows"))
            / "System32"
            / "tasklist.exe"
        )
        # Fixed Windows system process-list command; shell remains disabled.
        completed = subprocess.run(  # nosec B603
            [tasklist, "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
        result: dict[str, dict[str, Any]] = {}
        for row in csv.reader(io.StringIO(completed.stdout)):
            if len(row) >= 2 and row[1].isdigit():
                result[row[1]] = {"name": row[0], "cmdline": []}
        return result
    ps_executable = shutil.which("ps") or "/bin/ps"
    # Fixed POSIX system process-list command; shell remains disabled.
    completed = subprocess.run(  # nosec B603
        [ps_executable, "-eo", "pid=,comm=,args="],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        shell=False,
    )
    result = {}
    for line in completed.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) >= 2 and parts[0].isdigit():
            result[parts[0]] = {"name": parts[1], "cmdline": parts[2:]}
    return result


class ProcessSensor(Sensor):
    def poll(
        self, previous_state: dict[str, Any]
    ) -> tuple[list[Observation], dict[str, Any]]:
        watch_names = {
            str(item).lower() for item in self.settings.get("watch_names", [])
        }
        current_all = list_processes()
        current = {
            pid: info
            for pid, info in current_all.items()
            if not watch_names or str(info.get("name", "")).lower() in watch_names
        }
        previous = dict(previous_state.get("processes", {}))
        observations: list[Observation] = []
        for pid, info in current.items():
            if pid not in previous:
                observations.append(
                    Observation(
                        source=self.name,
                        kind="process_change",
                        subject=str(info.get("name", pid)),
                        predicate="process_state",
                        value="started",
                        metadata={
                            "pid": pid,
                            "info": info,
                            "dedupe_key": f"process:{pid}:started",
                        },
                    )
                )
        for pid, info in previous.items():
            if pid not in current:
                observations.append(
                    Observation(
                        source=self.name,
                        kind="process_change",
                        subject=str(info.get("name", pid)),
                        predicate="process_state",
                        value="stopped",
                        metadata={
                            "pid": pid,
                            "info": info,
                            "dedupe_key": f"process:{pid}:stopped",
                        },
                    )
                )
        return observations, {"processes": current}
