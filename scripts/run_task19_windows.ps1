[CmdletBinding()]
param(
    [string]$PythonExe = "D:\Tools\Python313\python.exe",
    [switch]$SkipSoak
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repo

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    $PythonExe = (Get-Command python.exe -ErrorAction Stop).Source
}

$head = (& git rev-parse HEAD).Trim()
$status = (& git status --short) -join [Environment]::NewLine
if ($status) {
    throw "Checkout must be clean before verification."
}

$venv = Join-Path ([System.IO.Path]::GetTempPath()) "wls-task19-$($head.Substring(0,12))"
& $PythonExe -m venv $venv
$python = Join-Path $venv "Scripts\python.exe"
& $python -m pip install --disable-pip-version-check -e ".[dev,provider]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$arguments = @("source/scripts/verify_task19_convergence.py")
if ($SkipSoak) { $arguments += "--skip-soak" }
& $python @arguments
$exitCode = $LASTEXITCODE

if ((& git rev-parse HEAD).Trim() -ne $head) { $exitCode = 6 }
if ((& git status --short)) { $exitCode = 7 }

Write-Host "TASK19_WINDOWS_VERIFICATION head=$head exit=$exitCode"
exit $exitCode
