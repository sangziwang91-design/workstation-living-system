from __future__ import annotations

from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess  # nosec B404 - verifier runs fixed local PowerShell scripts.
import tempfile
import time
import zipfile


REQUIRED_FILES = {
    "install.ps1",
    "verify_install.ps1",
    "uninstall.ps1",
    "README_DEPLOYMENT.md",
    "PACKAGE_MANIFEST.json",
    "SHA256SUMS.txt",
    "VERSION.json",
    "HANDOFF.md",
}
FORBIDDEN_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".key", ".pem", ".p12")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], cwd: Path, timeout: int) -> dict[str, object]:
    started = time.monotonic()
    completed = subprocess.run(  # nosec B603 - commands are verifier-defined script invocations.
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stdout": completed.stdout[-50_000:],
        "stderr": completed.stderr[-50_000:],
        "passed": completed.returncode == 0,
    }


def _verify_hashes(root: Path) -> list[str]:
    errors: list[str] = []
    sums = root / "SHA256SUMS.txt"
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split(None, 1)
        path = root / relative
        if not path.is_file():
            errors.append(f"missing hash target: {relative}")
            continue
        actual = _sha256(path)
        if actual.lower() != expected.lower():
            errors.append(f"hash mismatch: {relative}")
    return errors


def verify(zip_path: Path, *, roundtrip: bool, python_exe: str) -> dict[str, object]:
    zip_path = zip_path.resolve()
    temp_root = Path(tempfile.mkdtemp(prefix="wls-deploy-verify-"))
    extract_root = temp_root / "package"
    install_root = temp_root / "install"
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = sorted(archive.namelist())
            archive.extractall(extract_root)
        forbidden = [
            name
            for name in names
            if any(part in FORBIDDEN_PARTS for part in Path(name).parts)
            or name.endswith(FORBIDDEN_SUFFIXES)
        ]
        required_missing = sorted(REQUIRED_FILES - set(names))
        wheels = [name for name in names if name.startswith("wheel/") and name.endswith(".whl")]
        verification = [name for name in names if name.startswith("verification/") and name.endswith(".json")]
        hash_errors = _verify_hashes(extract_root)
        manifest = json.loads((extract_root / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
        manifest_paths = {str(item["path"]) for item in manifest.get("files", [])}
        control_files = {"PACKAGE_MANIFEST.json", "SHA256SUMS.txt"}
        manifest_missing = sorted((set(names) - control_files) - manifest_paths)
        manifest_extra = sorted(manifest_paths - (set(names) - control_files))
        checks = {
            "required_files": not required_missing,
            "single_wheel": len(wheels) == 1,
            "verification_json_present": bool(verification),
            "hashes_match": not hash_errors,
            "manifest_matches_archive": not manifest_missing and not manifest_extra,
            "forbidden_absent": not forbidden,
        }
        roundtrip_steps: list[dict[str, object]] = []
        if roundtrip and all(checks.values()):
            shell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell"
            roundtrip_steps.extend(
                [
                    _run(
                        [
                            shell,
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(extract_root / "install.ps1"),
                            "-InstallRoot",
                            str(install_root),
                            "-PythonExe",
                            python_exe,
                            "-DryRun",
                        ],
                        extract_root,
                        120,
                    ),
                    _run(
                        [
                            shell,
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(extract_root / "install.ps1"),
                            "-InstallRoot",
                            str(install_root),
                            "-PythonExe",
                            python_exe,
                        ],
                        extract_root,
                        600,
                    ),
                    _run(
                        [
                            shell,
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(extract_root / "verify_install.ps1"),
                            "-InstallRoot",
                            str(install_root),
                        ],
                        extract_root,
                        300,
                    ),
                    _run(
                        [
                            shell,
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(extract_root / "uninstall.ps1"),
                            "-InstallRoot",
                            str(install_root),
                        ],
                        extract_root,
                        300,
                    ),
                ]
            )
            checks["roundtrip"] = all(bool(step["passed"]) for step in roundtrip_steps) and not install_root.exists()
        return {
            "passed": all(checks.values()),
            "zip": str(zip_path),
            "zip_sha256": _sha256(zip_path),
            "zip_size_bytes": zip_path.stat().st_size,
            "checks": checks,
            "required_missing": required_missing,
            "hash_errors": hash_errors,
            "forbidden": forbidden,
            "manifest_missing": manifest_missing,
            "manifest_extra": manifest_extra,
            "wheels": wheels,
            "verification": verification,
            "roundtrip_steps": roundtrip_steps,
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--roundtrip", action="store_true")
    parser.add_argument("--python-exe", default="python")
    args = parser.parse_args()
    try:
        report = verify(args.zip, roundtrip=args.roundtrip, python_exe=args.python_exe)
    except Exception as exc:
        report = {"passed": False, "error_type": type(exc).__name__, "error": str(exc)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
