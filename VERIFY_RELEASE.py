from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parent


def fail(message: str) -> None:
    raise SystemExit(f"release verification failed: {message}")


def run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        fail(f"command returned {result.returncode}: {' '.join(command)}\n{result.stdout}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


version_file = ROOT / "VERSION.txt"
if not version_file.is_file():
    fail("VERSION.txt is missing")
version = version_file.read_text(encoding="utf-8").strip()
if not re.fullmatch(r"\d+\.\d+\.\d+(?:a\d+|b\d+|rc\d+)?", version):
    fail(f"unsupported version format: {version!r}")

wheel_name = f"workstation_living_system-{version}-py3-none-any.whl"
wheel = ROOT / "dist" / wheel_name
if not wheel.is_file():
    fail(f"expected wheel is missing: {wheel_name}")
all_wheels = sorted((ROOT / "dist").glob("workstation_living_system-*.whl"))
if all_wheels != [wheel]:
    fail(f"release must contain exactly one WLS wheel: {[path.name for path in all_wheels]}")

hash_file = ROOT / "WHEEL_SHA256.txt"
if not hash_file.is_file():
    fail("WHEEL_SHA256.txt is missing")
expected_hash = hash_file.read_text(encoding="utf-8").strip().split()[0].lower()
actual_hash = sha256(wheel)
if expected_hash != actual_hash:
    fail(f"wheel hash mismatch: expected {expected_hash}, got {actual_hash}")

pyproject = (ROOT / "source" / "pyproject.toml").read_text(encoding="utf-8")
source_version = (ROOT / "source" / "src" / "wls" / "_version.py").read_text(encoding="utf-8")
if f'version = "{version}"' not in pyproject:
    fail("source/pyproject.toml version does not match VERSION.txt")
if f'__version__ = "{version}"' not in source_version:
    fail("source package version does not match VERSION.txt")

with tempfile.TemporaryDirectory(prefix="wls-release-verify-") as temporary:
    root = Path(temporary)
    environment = root / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    home = root / "home"
    config = home / "config.json"
    run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--no-deps", str(wheel)])
    imported = run([str(python), "-c", "import wls; print(wls.__version__)"])
    if imported.stdout.strip() != version:
        fail(f"installed package reported {imported.stdout.strip()!r}, expected {version!r}")
    run([str(python), "-m", "wls", "--config", str(config), "init", "--home", str(home)])
    self_check = run([str(python), "-m", "wls", "--config", str(config), "self-check"])
    verify = run([str(python), "-m", "wls", "--config", str(config), "verify"])
    try:
        self_check_json = json.loads(self_check.stdout)
        verify_json = json.loads(verify.stdout)
    except json.JSONDecodeError as exc:
        fail(f"installed CLI returned non-JSON output: {exc}")
    if self_check_json.get("ok") is not True:
        fail(f"self-check did not pass: {self_check_json}")
    if verify_json.get("ok") is not True:
        fail(f"integrity verification did not pass: {verify_json}")

print(json.dumps({
    "ok": True,
    "version": version,
    "wheel": wheel.name,
    "wheel_sha256": actual_hash,
    "network_used_for_install": False,
    "git_metadata_required": False,
}, indent=2, sort_keys=True))
