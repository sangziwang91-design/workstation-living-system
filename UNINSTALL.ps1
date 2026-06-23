[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$WorkstationRoot = "D:\Workstation",
    [string]$InstallRoot = "",
    [switch]$RemoveState
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ([string]::IsNullOrWhiteSpace($InstallRoot)) { $InstallRoot = Join-Path $WorkstationRoot ".wls" }
$StopScript = Join-Path $InstallRoot "STOP.ps1"
if (Test-Path $StopScript) { & $StopScript -InstallRoot $InstallRoot }
if ($RemoveState) {
    if ($PSCmdlet.ShouldProcess($InstallRoot, "Remove WLS application, configuration, state, memories, and secrets")) {
        Remove-Item -Recurse -Force $InstallRoot
    }
} else {
    foreach ($path in @("venv", "wls.cmd", "START.ps1", "STOP.ps1", "VERIFY.ps1", "UNINSTALL.ps1", "docs")) {
        $target = Join-Path $InstallRoot $path
        if (Test-Path $target) { Remove-Item -Recurse -Force $target }
    }
    Write-Host "WLS application removed; state, config, memories, and secrets were preserved in $InstallRoot." -ForegroundColor Yellow
}
