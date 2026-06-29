from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import argparse
import ast
import hashlib
import json
import shutil
import subprocess  # nosec B404 - fixed local git command for package provenance.
import sys
import zipfile


REPOSITORY = Path(__file__).resolve().parents[2]
FORBIDDEN_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".key", ".pem", ".p12")


INSTALL_PS1 = r'''[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [string]$PythonExe = "python",
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PackageRoot = $PSScriptRoot

function Assert-PackageHashes {
    $sumPath = Join-Path $PackageRoot "SHA256SUMS.txt"
    if (-not (Test-Path -LiteralPath $sumPath -PathType Leaf)) { throw "SHA256SUMS.txt missing" }
    foreach ($line in Get-Content -LiteralPath $sumPath) {
        if (-not $line.Trim()) { continue }
        $parts = $line -split "\s+", 2
        if ($parts.Count -ne 2) { throw "invalid SHA256SUMS line: $line" }
        $expected = $parts[0].ToLowerInvariant()
        $relative = $parts[1]
        $path = Join-Path $PackageRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "missing package file: $relative" }
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "hash mismatch: $relative" }
    }
}

Assert-PackageHashes
$root = [System.IO.Path]::GetFullPath($InstallRoot)
$venv = Join-Path $root "venv"
$wlsHome = Join-Path $root "home"
$receiptPath = Join-Path $root "INSTALL_RECEIPT.json"
$wheel = Get-ChildItem -LiteralPath (Join-Path $PackageRoot "wheel") -Filter "*.whl" | Select-Object -First 1
if ($null -eq $wheel) { throw "wheel missing" }

$version = & $PythonExe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 42)"
if ($LASTEXITCODE -ne 0) { throw "Python >=3.11 is required" }

if ($DryRun) {
    [PSCustomObject]@{ dry_run = $true; install_root = $root; wheel = $wheel.Name } | ConvertTo-Json -Depth 4
    exit 0
}

if ((Test-Path -LiteralPath $root) -and (Get-ChildItem -LiteralPath $root -Force | Select-Object -First 1)) {
    throw "refusing to overwrite non-empty install root: $root"
}

$created = New-Object System.Collections.Generic.List[string]
New-Item -ItemType Directory -Force -Path $root | Out-Null
$created.Add($root)

try {
    & $PythonExe -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    $created.Add($venv)
    $python = Join-Path $venv "Scripts\python.exe"
    & $python -m pip install --disable-pip-version-check --no-index --no-deps $wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw "wheel install failed" }
    & $python -m wls init --home $wlsHome
    if ($LASTEXITCODE -ne 0) { throw "wls init failed" }
    $created.Add($wlsHome)

    $configPath = Join-Path $wlsHome "config.json"
    $config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
    $config.provider = [PSCustomObject]@{ type = "deterministic"; fallback = "deterministic"; goal_mode = "disabled"; memory_mode = "disabled" }
    $config.plugin_modules = @()
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 20), $utf8NoBom)

    $receipt = [PSCustomObject]@{
        package_digest = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PackageRoot "PACKAGE_MANIFEST.json")).Hash.ToLowerInvariant()
        installed_at = (Get-Date).ToUniversalTime().ToString("o")
        install_root = $root
        wheel = $wheel.Name
        wheel_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheel.FullName).Hash.ToLowerInvariant()
        providers_disabled = $true
        examiner_shadow_disabled = $true
        created_paths = $created.ToArray()
    }
    [System.IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 20), $utf8NoBom)
    [PSCustomObject]@{ installed = $true; install_root = $root; receipt = $receiptPath } | ConvertTo-Json -Depth 4
} catch {
    foreach ($path in ($created.ToArray() | Sort-Object Length -Descending)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue }
    }
    throw
}
'''


VERIFY_PS1 = r'''[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallRoot
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($InstallRoot)
$receiptPath = Join-Path $root "INSTALL_RECEIPT.json"
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw "INSTALL_RECEIPT.json missing" }
$receipt = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
$python = Join-Path $root "venv\Scripts\python.exe"
$config = Join-Path $root "home\config.json"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "venv python missing" }
if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { throw "config missing" }
$statusOutput = & $python -m wls --config $config status 2>&1
if ($LASTEXITCODE -ne 0) { throw "wls status failed: $statusOutput" }
$selfCheckOutput = & $python -m wls --config $config self-check 2>&1
if ($LASTEXITCODE -ne 0) { throw "wls self-check failed: $selfCheckOutput" }
$verifyOutput = & $python -m wls --config $config verify 2>&1
if ($LASTEXITCODE -ne 0) { throw "wls verify failed: $verifyOutput" }
$examiner = & $python -c "from wls.runtime import LivingSystem; r=LivingSystem.from_config_path(r'$config'); print(hasattr(r, 'examiner')); r.close()"
if ($examiner.Trim() -ne "False") { throw "examiner must remain shadow-disabled by default" }
[PSCustomObject]@{ verified = $true; install_root = $root; examiner_shadow_disabled = $true; providers_disabled = $receipt.providers_disabled } | ConvertTo-Json -Depth 4
'''


UNINSTALL_PS1 = r'''[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [switch]$DryRun
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath($InstallRoot)
$receiptPath = Join-Path $root "INSTALL_RECEIPT.json"
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw "INSTALL_RECEIPT.json missing" }
$receipt = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
if ($DryRun) {
    [PSCustomObject]@{ dry_run = $true; remove = $receipt.created_paths } | ConvertTo-Json -Depth 6
    exit 0
}
foreach ($path in ($receipt.created_paths | Sort-Object Length -Descending)) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}
[PSCustomObject]@{ uninstalled = $true; install_root = $root } | ConvertTo-Json -Depth 4
'''


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    completed = subprocess.run(  # nosec B603 B607 - fixed git executable for local provenance.
        ["git", *args],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed.stdout.strip()


def _version() -> str:
    module = ast.parse((REPOSITORY / "source/src/wls/_version.py").read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value:
                return value
    raise ValueError("canonical version not found")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _copy(path: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def _manifest(stage: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(item for item in stage.rglob("*") if item.is_file()):
        relative = path.relative_to(stage).as_posix()
        if relative in {"PACKAGE_MANIFEST.json", "SHA256SUMS.txt"}:
            continue
        if any(part in FORBIDDEN_PARTS for part in Path(relative).parts):
            raise ValueError(f"forbidden package path: {relative}")
        if relative.endswith(FORBIDDEN_SUFFIXES):
            raise ValueError(f"forbidden package suffix: {relative}")
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return files


def build(wheel: Path, output_dir: Path, verification_json: Path | None) -> dict[str, object]:
    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current") or "DETACHED"
    version = _version()
    sha12 = head[:12]
    output_dir.mkdir(parents=True, exist_ok=True)
    stage = output_dir / f"staging-{sha12}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    _copy(wheel, stage / "wheel" / wheel.name)
    _write(stage / "install.ps1", INSTALL_PS1)
    _write(stage / "verify_install.ps1", VERIFY_PS1)
    _write(stage / "uninstall.ps1", UNINSTALL_PS1)
    _write(
        stage / "README_DEPLOYMENT.md",
        (
            "# Workstation Living System Windows Deployment\n\n"
            "This package installs the exact verified wheel into an isolated venv. "
            "Providers are disabled and the Examiner remains shadow-disabled unless an owner explicitly changes config.\n"
        ),
    )
    _write(
        stage / "HANDOFF.md",
        (
            "# Deployment Handoff\n\n"
            f"- Head: `{head}`\n"
            f"- Branch: `{branch}`\n"
            "- Owner action: inspect, install with `install.ps1`, verify with `verify_install.ps1`, uninstall with `uninstall.ps1`.\n"
        ),
    )
    version_json = {
        "project": "workstation-living-system",
        "version": version,
        "head_sha": head,
        "branch": branch,
        "built_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "wheel": wheel.name,
        "wheel_sha256": _sha256(wheel),
    }
    _write(stage / "VERSION.json", json.dumps(version_json, indent=2, sort_keys=True))
    if verification_json is not None:
        _copy(verification_json, stage / "verification" / verification_json.name)
    else:
        _write(
            stage / "verification" / "PRE_VERIFICATION.json",
            json.dumps({"head_sha": head, "status": "PREFLIGHT_BUNDLE"}, indent=2, sort_keys=True),
        )

    manifest_files = _manifest(stage)
    package_manifest = {
        "title": "Workstation Living System Windows Deployment",
        "head_sha": head,
        "branch": branch,
        "version": version,
        "created_at": datetime.now(UTC).isoformat(),
        "files": manifest_files,
        "exclusions": sorted([*FORBIDDEN_PARTS, *FORBIDDEN_SUFFIXES]),
    }
    _write(stage / "PACKAGE_MANIFEST.json", json.dumps(package_manifest, indent=2, sort_keys=True))
    sums = [
        f"{item['sha256']}  {item['path']}"
        for item in _manifest(stage)
        if item["path"] != "SHA256SUMS.txt"
    ]
    _write(stage / "SHA256SUMS.txt", "\n".join(sums) + "\n")

    zip_name = f"Workstation_Living_System_{version}_{sha12}_Windows.zip"
    zip_path = output_dir / zip_name
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            info = zipfile.ZipInfo(path.relative_to(stage).as_posix())
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return {
        "passed": True,
        "zip": str(zip_path),
        "zip_sha256": _sha256(zip_path),
        "zip_size_bytes": zip_path.stat().st_size,
        "manifest": str(stage / "PACKAGE_MANIFEST.json"),
        "file_count": len([item for item in stage.rglob("*") if item.is_file()]),
        "version": version,
        "head_sha": head,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=REPOSITORY / "artifacts" / "deployment")
    parser.add_argument("--verification-json", type=Path)
    args = parser.parse_args()
    try:
        report = build(args.wheel.resolve(), args.output_dir.resolve(), args.verification_json)
    except Exception as exc:
        report = {"passed": False, "error_type": type(exc).__name__, "error": str(exc)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
