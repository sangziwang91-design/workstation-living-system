[CmdletBinding()]
param(
    [string]$WorkstationRoot = "D:\Workstation",
    [string]$InstallRoot = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ([string]::IsNullOrWhiteSpace($InstallRoot)) { $InstallRoot = Join-Path $WorkstationRoot ".wls" }
$PidFile = Join-Path $InstallRoot "state\daemon.pid"
if (-not (Test-Path $PidFile)) {
    Write-Host "No WLS daemon PID file exists."
    exit 0
}
$daemonPid = [int](Get-Content $PidFile -Raw)
$process = Get-Process -Id $daemonPid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $daemonPid -Force
    $process.WaitForExit(10000) | Out-Null
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "WLS daemon stopped." -ForegroundColor Green
