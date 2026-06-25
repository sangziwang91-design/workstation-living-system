[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("3.11", "3.13")]
    [string]$PythonVersion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$basePython = switch ($PythonVersion) {
    "3.11" { "D:\Tools\Python311\python.exe" }
    "3.13" { "D:\Tools\Python313\python.exe" }
    default { throw "Unsupported Python version: $PythonVersion" }
}

if (-not (Test-Path -LiteralPath $basePython)) {
    throw "Required local Python is missing: $basePython"
}

if (-not $env:RUNNER_TEMP) {
    throw "RUNNER_TEMP is not available."
}

if (-not $env:GITHUB_ENV -or -not $env:GITHUB_PATH) {
    throw "GitHub environment files are not available."
}

if ($env:PIP_CACHE_DIR) {
    New-Item -ItemType Directory -Force $env:PIP_CACHE_DIR | Out-Null
}

$versionTag = $PythonVersion.Replace(".", "")
$attempt = if ($env:GITHUB_RUN_ATTEMPT) { $env:GITHUB_RUN_ATTEMPT } else { "1" }
$venvRoot = Join-Path $env:RUNNER_TEMP "wls-$($env:GITHUB_RUN_ID)-$attempt-$($env:GITHUB_JOB)-py$versionTag"

if (Test-Path -LiteralPath $venvRoot) {
    Remove-Item -LiteralPath $venvRoot -Recurse -Force
}

& $basePython -m venv $venvRoot
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create virtual environment with $basePython"
}

$pythonExe = Join-Path $venvRoot "Scripts\python.exe"
$scriptsDir = Join-Path $venvRoot "Scripts"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Virtual-environment Python was not created: $pythonExe"
}

$actualVersion = (& $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($actualVersion -ne $PythonVersion) {
    throw "Python version mismatch. Expected $PythonVersion, got $actualVersion"
}

"PYTHON_VERSION=$PythonVersion" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
"PYTHON_ROOT=$venvRoot" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
"PYTHON_EXE=$pythonExe" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
"VIRTUAL_ENV=$venvRoot" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
$scriptsDir | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append

& $pythonExe --version
& $pythonExe -m pip --version

Write-Host "Local Python runtime ready: base=$basePython venv=$venvRoot"
