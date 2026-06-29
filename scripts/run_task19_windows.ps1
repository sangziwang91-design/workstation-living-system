[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [switch]$SkipSoak
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repo

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $candidates = @(
        "D:\Tools\Python313\python.exe",
        "D:\Tools\Python311\python.exe",
        (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
        (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
    if (-not $candidates) {
        throw "No supported Python executable was found. Supply -PythonExe explicitly."
    }
    $PythonExe = $candidates[0]
}

$actualVersion = (& $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($actualVersion -notin @("3.11", "3.12", "3.13")) {
    throw "Unsupported Python version: $actualVersion"
}

$head = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($head)) {
    throw "Unable to determine the current Git head."
}

$statusBefore = (& git status --short) -join [Environment]::NewLine
if (-not [string]::IsNullOrWhiteSpace($statusBefore)) {
    throw "The verification checkout must be clean before execution:`n$statusBefore"
}

$tempBase = if (Test-Path "D:\actions-runner") {
    "D:\actions-runner\_temp"
} else {
    Join-Path ([System.IO.Path]::GetTempPath()) "wls-task19"
}
New-Item -ItemType Directory -Force $tempBase | Out-Null
$venv = Join-Path $tempBase "task19-$($head.Substring(0,12))-$actualVersion"
if (Test-Path -LiteralPath $venv) {
    Remove-Item -LiteralPath $venv -Recurse -Force
}

& $PythonExe -m venv $venv
if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
$verifyPython = Join-Path $venv "Scripts\python.exe"

& $verifyPython -m pip install --disable-pip-version-check --retries 10 --timeout 120 -e ".[dev,provider]"
if ($LASTEXITCODE -ne 0) { throw "dependency installation failed" }

$arguments = @("source/scripts/verify_task19_convergence.py")
if ($SkipSoak) { $arguments += "--skip-soak" }
& $verifyPython @arguments
$verificationExit = $LASTEXITCODE

$statusAfter = (& git status --short) -join [Environment]::NewLine
if (-not [string]::IsNullOrWhiteSpace($statusAfter)) {
    Write-Warning "Verification generated tracked changes unexpectedly:`n$statusAfter"
    if ($verificationExit -eq 0) { $verificationExit = 3 }
}

Write-Host "TASK19_WINDOWS_VERIFICATION head=$head python=$actualVersion exit=$verificationExit"
Write-Host "Reports are under artifacts\task19 and are intentionally ignored by Git."
exit $verificationExit
