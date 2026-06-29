[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [switch]$SkipSoak,
    [switch]$KeepEnvironment
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$target = Join-Path $PSScriptRoot "run_task19_acceptance_windows.ps1"
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "Unified Task19 acceptance entry point is missing: $target"
}

$forward = @{}
if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
    $forward["PythonExe"] = $PythonExe
}
if ($SkipSoak) {
    $forward["SkipSoak"] = $true
}
if ($KeepEnvironment) {
    $forward["KeepEnvironment"] = $true
}

& $target @forward
exit $LASTEXITCODE
