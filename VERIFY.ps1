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
if (-not (Test-Path $Python)) { throw "WLS virtual environment not found: $Python" }
if (-not (Test-Path $Config)) { throw "WLS configuration not found: $Config" }
$ConfigObject = Get-Content $Config -Raw | ConvertFrom-Json
if ($ConfigObject.read_only -ne $true) { throw "Verification requires the standalone installation to remain read-only." }
& $Python -m wls --config $Config self-check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m wls --config $Config verify
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m wls --config $Config status
exit $LASTEXITCODE
