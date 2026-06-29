[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [switch]$SkipSoak,
    [switch]$KeepEnvironment
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repo

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = @(
        "D:\Tools\Python313\python.exe",
        "D:\Tools\Python311\python.exe",
        (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
        (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
    ) | Where-Object { $_ -and (Test-Path $_ -PathType Leaf) } | Select-Object -First 1
}
if (-not $PythonExe) { throw "Python 3.11-3.13 is required." }

$head = (& git rev-parse HEAD).Trim()
$status = (& git status --short) -join [Environment]::NewLine
if ($status) { throw "Checkout must be clean:`n$status" }

$tempRoot = if (Test-Path "D:\actions-runner") {
    "D:\actions-runner\_temp"
} else {
    Join-Path ([System.IO.Path]::GetTempPath()) "wls-task19"
}
New-Item -ItemType Directory -Force $tempRoot | Out-Null
$venv = Join-Path $tempRoot "acceptance-$($head.Substring(0,12))"
if (Test-Path $venv) { Remove-Item $venv -Recurse -Force }

$exitCode = 1
try {
    & $PythonExe -m venv $venv
    $python = Join-Path $venv "Scripts\python.exe"
    & $python -m pip install --disable-pip-version-check --retries 10 --timeout 120 -e ".[dev,provider]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

    $arguments = @("source/scripts/verify_task19_acceptance.py")
    if ($SkipSoak) { $arguments += "--skip-soak" }
    & $python @arguments
    $exitCode = $LASTEXITCODE

    if ((& git rev-parse HEAD).Trim() -ne $head) { $exitCode = 6 }
    if ((& git status --short)) { $exitCode = 7 }
}
finally {
    if (-not $KeepEnvironment -and (Test-Path $venv)) {
        Remove-Item $venv -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "TASK19_EXACT_HEAD_ACCEPTANCE head=$head exit=$exitCode"
exit $exitCode
