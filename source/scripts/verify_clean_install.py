from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import hashlib
import json
import os
import subprocess
import sys
import venv


def _run(command: list[str], *, cwd: Path) -> dict:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(wheel: Path) -> dict:
    wheel = wheel.resolve()
    if not wheel.is_file():
        raise FileNotFoundError(wheel)
    repository = Path(__file__).resolve().parents[2]
    with TemporaryDirectory(prefix="wls-clean-install-") as tmp:
        root = Path(tmp)
        environment = root / "venv"
        outside = root / "outside-repository"
        outside.mkdir()
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        if os.name == "nt":
            python = environment / "Scripts" / "python.exe"
            wls = environment / "Scripts" / "wls.exe"
            provider = environment / "Scripts" / "wls-provider.exe"
        else:
            python = environment / "bin" / "python"
            wls = environment / "bin" / "wls"
            provider = environment / "bin" / "wls-provider"
        commands: list[dict] = []
        commands.append(
            _run(
                [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
                cwd=outside,
            )
        )
        commands.append(_run([str(python), "-m", "pip", "check"], cwd=outside))
        commands.append(_run([str(wls), "--help"], cwd=outside))
        commands.append(_run([str(provider), "--help"], cwd=outside))
        smoke = r'''
import json
import tempfile
from pathlib import Path
import wls
from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import Event

with tempfile.TemporaryDirectory(prefix="wls-installed-smoke-") as tmp:
    config = default_config(Path(tmp))
    config.sensors = []
    config.max_actions_per_cycle = 1
    runtime = LivingSystem(config)
    runtime.ingest_event(Event(
        event_type="installed-smoke",
        source="clean-install",
        payload={"observation": {
            "source": "clean-install",
            "kind": "state",
            "subject": "installed-package",
            "predicate": "smoke",
            "value": "run",
            "confidence": 1.0,
        }},
        dedupe_key="installed-smoke",
    ))
    cycle = runtime.run_cycle()
    integrity = runtime.verify_integrity(full=True)
    print(json.dumps({
        "module_path": wls.__file__,
        "cycle_status": cycle["status"],
        "integrity": integrity,
        "survival": runtime.status()["survival"],
    }, sort_keys=True, default=str))
'''
        smoke_result = _run([str(python), "-c", smoke], cwd=outside)
        commands.append(smoke_result)
        parsed_smoke = None
        if smoke_result["exit_code"] == 0:
            parsed_smoke = json.loads(smoke_result["stdout"].strip().splitlines()[-1])
        imported_path = Path(parsed_smoke["module_path"]).resolve() if parsed_smoke else None
        passed = bool(
            all(item["exit_code"] == 0 for item in commands)
            and parsed_smoke
            and parsed_smoke["cycle_status"] == "SUCCEEDED"
            and parsed_smoke["integrity"]["ok"]
            and imported_path
            and environment.resolve() in imported_path.parents
            and repository.resolve() not in imported_path.parents
        )
        return {
            "passed": passed,
            "wheel": str(wheel),
            "wheel_sha256": _sha256(wheel),
            "wheel_size_bytes": wheel.stat().st_size,
            "environment_marker": str(environment),
            "outside_repository_cwd": str(outside),
            "imported_module_path": str(imported_path) if imported_path else None,
            "commands": commands,
            "smoke": parsed_smoke,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = verify(args.wheel)
    except Exception as exc:
        report = {"passed": False, "error_type": type(exc).__name__, "error": str(exc)}
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
