[CmdletBinding()]
param(
    [string]$WorkstationRoot = "D:\Workstation",
    [string]$InstallRoot = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ([string]::IsNullOrWhiteSpace($InstallRoot)) { $InstallRoot = Join-Path $WorkstationRoot ".wls" }
$Python = Join-Path $InstallRoot "venv\Scripts\python.exe"
$Config = Join-Path $InstallRoot "config.json"
$PidFile = Join-Path $InstallRoot "state\daemon.pid"
$LogDir = Join-Path $InstallRoot "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $PidFile) -Force | Out-Null
if (-not (Test-Path $Python)) { throw "WLS is not installed: $Python" }
if (Test-Path $PidFile) {
    $oldPid = [int](Get-Content $PidFile -Raw)
    $oldProcess = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($oldProcess) { throw "WLS daemon appears to be running with PID $oldPid." }
    Remove-Item $PidFile -Force
}
$stdout = Join-Path $LogDir "daemon.out.log"
$stderr = Join-Path $LogDir "daemon.err.log"
$process = Start-Process -FilePath $Python -ArgumentList @("-m", "wls", "--config", "`"$Config`"", "daemon") -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Set-Content -Path $PidFile -Value $process.Id -Encoding ASCII
Write-Host "WLS daemon started with PID $($process.Id)." -ForegroundColor Green
