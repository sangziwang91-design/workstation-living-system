[CmdletBinding()]
param(
    [string]$WorkstationRoot = "D:\Workstation",
    [string]$InstallRoot = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $InstallRoot = Join-Path $WorkstationRoot ".wls"
}
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$VenvRoot = Join-Path $InstallRoot "venv"
$ConfigPath = Join-Path $InstallRoot "config.json"
$Wheel = Get-ChildItem -Path (Join-Path $PackageRoot "dist") -Filter "workstation_living_system-*.whl" | Select-Object -First 1
if (-not $Wheel) { throw "Bundled WLS wheel was not found." }
$WheelHashFile = Join-Path $PackageRoot "WHEEL_SHA256.txt"
if (-not (Test-Path $WheelHashFile)) { throw "Wheel hash file is missing." }
$ExpectedWheelHash = (Get-Content $WheelHashFile -Raw).Trim().Split()[0].ToLowerInvariant()
$ActualWheelHash = (Get-FileHash -Algorithm SHA256 $Wheel.FullName).Hash.ToLowerInvariant()
if ($ExpectedWheelHash -ne $ActualWheelHash) { throw "Bundled wheel failed SHA-256 verification." }

function Resolve-Python {
    $candidates = @(
        @{ File = "py"; Args = @("-3.11") },
        @{ File = "py"; Args = @("-3.12") },
        @{ File = "py"; Args = @("-3.13") },
        @{ File = "python"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        try {
            $version = & $candidate.File @($candidate.Args) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version) {
                $parts = $version.Trim().Split('.')
                if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)) {
                    return $candidate
                }
            }
        } catch { }
    }
    throw "Python 3.11 or newer is required. Install Python, then rerun INSTALL.ps1."
}

if (-not (Test-Path $WorkstationRoot)) {
    New-Item -ItemType Directory -Path $WorkstationRoot -Force | Out-Null
}
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null

if ($Force -and (Test-Path $VenvRoot)) {
    Remove-Item -Recurse -Force $VenvRoot
}

$Python = Resolve-Python
if (-not (Test-Path $VenvRoot)) {
    & $Python.File @($Python.Args) -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) { throw "Failed to create WLS virtual environment." }
}

$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { throw "Virtual environment Python was not created." }

& $VenvPython -m pip install --disable-pip-version-check --no-index --force-reinstall $Wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "Failed to install WLS wheel." }

if (-not (Test-Path $ConfigPath)) {
    & $VenvPython -m wls --config $ConfigPath init --home $InstallRoot
    if ($LASTEXITCODE -ne 0) { throw "WLS initialization failed." }
}
$ConfigObject = Get-Content $ConfigPath -Raw | ConvertFrom-Json
if ($ConfigObject.read_only -ne $true) {
    throw "Installation stopped because the existing WLS config is not read-only. Review it explicitly before installation."
}

$Wrapper = @"
@echo off
"$VenvPython" -m wls --config "$ConfigPath" %*
"@
Set-Content -Path (Join-Path $InstallRoot "wls.cmd") -Value $Wrapper -Encoding ASCII

Copy-Item (Join-Path $PackageRoot "START.ps1") (Join-Path $InstallRoot "START.ps1") -Force
Copy-Item (Join-Path $PackageRoot "STOP.ps1") (Join-Path $InstallRoot "STOP.ps1") -Force
Copy-Item (Join-Path $PackageRoot "VERIFY.ps1") (Join-Path $InstallRoot "VERIFY.ps1") -Force
Copy-Item (Join-Path $PackageRoot "UNINSTALL.ps1") (Join-Path $InstallRoot "UNINSTALL.ps1") -Force
$InstalledDocs = Join-Path $InstallRoot "docs"
New-Item -ItemType Directory -Path $InstalledDocs -Force | Out-Null
Copy-Item (Join-Path $PackageRoot "docs\*") $InstalledDocs -Recurse -Force

& $VenvPython -m wls --config $ConfigPath self-check
if ($LASTEXITCODE -ne 0) { throw "WLS self-check failed after installation." }
& $VenvPython -m wls --config $ConfigPath verify
if ($LASTEXITCODE -ne 0) { throw "WLS integrity verification failed after installation." }

Write-Host ""
Write-Host "WLS installed successfully." -ForegroundColor Green
Write-Host "Install root: $InstallRoot"
Write-Host "Command:      $(Join-Path $InstallRoot 'wls.cmd')"
Write-Host "Safety mode:  read-only; no service or scheduled task was created."
