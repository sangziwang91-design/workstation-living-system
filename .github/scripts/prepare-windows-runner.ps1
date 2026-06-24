[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$sevenZip = "D:\Tools\7-Zip\7z.exe"

if (-not (Test-Path -LiteralPath $sevenZip)) {
    throw "Required 7-Zip binary is missing: $sevenZip"
}

if (-not $env:GITHUB_PATH) {
    throw "GITHUB_PATH is not available in this runner step."
}

$sevenZipDir = Split-Path -Path $sevenZip -Parent
$sevenZipDir | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
$env:Path = "$sevenZipDir;$env:Path"

& $sevenZip i | Select-Object -First 12
if ($LASTEXITCODE -ne 0) {
    throw "7-Zip execution failed with exit code $LASTEXITCODE"
}

Write-Host "Windows runner prerequisite ready: $sevenZip"
