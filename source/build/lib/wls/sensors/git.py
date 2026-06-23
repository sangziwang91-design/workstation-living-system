from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import shutil

# Git execution is fixed to the git binary with structured arguments and shell=False.
import subprocess  # nosec B404

from .base import Sensor
from ..schemas import Observation


def git_output(repository: Path, arguments: list[str]) -> str:
    git_executable = shutil.which("git")
    if not git_executable:
        raise RuntimeError("git executable not found")
    # The executable is fixed and arguments are structured.
    completed = subprocess.run(  # nosec B603
        [git_executable, "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


class GitSensor(Sensor):
    def poll(
        self, previous_state: dict[str, Any]
    ) -> tuple[list[Observation], dict[str, Any]]:
        repositories = [
            Path(item).expanduser().resolve()
            for item in self.settings.get("repositories", [])
        ]
        current: dict[str, Any] = {}
        observations: list[Observation] = []
        previous_repos = dict(previous_state.get("repositories", {}))
        for repo in repositories:
            key = str(repo)
            try:
                head = git_output(repo, ["rev-parse", "HEAD"])
                branch = git_output(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
                status = git_output(repo, ["status", "--porcelain=v1"])
                state = {
                    "head": head,
                    "branch": branch,
                    "dirty": bool(status),
                    "status": status[:20000],
                }
                current[key] = state
                old = previous_repos.get(key)
                if old is None or old != state:
                    observations.append(
                        Observation(
                            source=self.name,
                            kind="git_change",
                            subject=key,
                            predicate="repository_state",
                            value=state,
                            metadata={
                                "previous": old,
                                "dedupe_key": f"git:{key}:{hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()}",
                            },
                        )
                    )
            except Exception as exc:
                current[key] = {"error": str(exc)}
                observations.append(
                    Observation(
                        source=self.name,
                        kind="sensor_error",
                        subject=key,
                        predicate="git_probe",
                        value="failed",
                        confidence=1.0,
                        metadata={
                            "error": str(exc),
                            "dedupe_key": f"git:{key}:error:{hashlib.sha256(str(exc).encode()).hexdigest()}",
                        },
                    )
                )
        return observations, {"repositories": current}
