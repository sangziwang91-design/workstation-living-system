[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [string]$PythonExe = "python",
    [switch]$DryRun,
    [string]$SourceRepo = "D:\WLS-Dev\workstation-living-system-private",
    [switch]$SkipCurrentStateGate,
    [switch]$SkipBackup
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PackageRoot = $PSScriptRoot

function Write-Step { param([string]$Message) Write-Host "[WLS-INSTALL] $Message" -ForegroundColor Cyan }

function Assert-PackageHashes {
    Write-Step "Verifying package hashes..."
    $sumPath = Join-Path $PackageRoot "SHA256SUMS.txt"
    if (-not (Test-Path -LiteralPath $sumPath -PathType Leaf)) { throw "SHA256SUMS.txt missing" }
    foreach ($line in Get-Content -LiteralPath $sumPath) {
        if (-not $line.Trim()) { continue }
        $parts = $line -split "\s+", 2
        if ($parts.Count -ne 2) { throw "invalid SHA256SUMS line: $line" }
        $expected = $parts[0].ToLowerInvariant()
        $relative = $parts[1]
        $path = Join-Path $PackageRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "missing package file: $relative" }
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "hash mismatch: $relative (expected $expected, got $actual)" }
    }
    Write-Step "Package hashes verified."
}

function Assert-CurrentStateGate {
    if ($SkipCurrentStateGate) {
        Write-Step "CURRENT_STATE gate skipped by user request."
        return
    }
    Write-Step "Checking CURRENT_STATE gate..."
    $statePath = Join-Path $SourceRepo "CURRENT_STATE.yaml"
    if (-not (Test-Path -LiteralPath $statePath)) {
        Write-Step "CURRENT_STATE.yaml not found at $statePath - skipping gate."
        return
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        $gateScript = @"
import yaml, sys, json
with open(r'$statePath', encoding='utf-8') as f:
    state = yaml.safe_load(f)
version = state.get('system', {}).get('development_version', 'unknown')
branch = state.get('branches', [{}])[0] if isinstance(state.get('branches'), list) else state.get('branches', {})
branch_name = branch.get('name', 'unknown') if isinstance(branch, dict) else 'unknown'
status = branch.get('status', 'unknown') if isinstance(branch, dict) else 'unknown'
targets = state.get('completed_evolution_targets', [])
et004 = any(t.get('status','').startswith('IMPLEMENTED') for t in targets if t.get('id')=='EVOLUTION-TARGET-004')
print(json.dumps({'version': version, 'branch': branch_name, 'branch_status': status, 'et004_complete': et004}))
"@
        $gateResult = & python -c $gateScript 2>$null | ConvertFrom-Json
        if ($gateResult) {
            Write-Step "Version: $($gateResult.version) | Branch: $($gateResult.branch) ($($gateResult.branch_status)) | ET-004: $($gateResult.et004_complete)"
            if ($gateResult.branch_status -ne "ACTIVE") {
                Write-Warning "Branch status is $($gateResult.branch_status), not ACTIVE. Use -SkipCurrentStateGate to bypass."
                if (-not $SkipCurrentStateGate) { throw "CURRENT_STATE gate failed: branch not ACTIVE" }
            }
        }
    }
    Write-Step "CURRENT_STATE gate passed."
}

function Backup-ExistingInstall {
    if ($SkipBackup) {
        Write-Step "Backup skipped by user request."
        return
    }
    if (-not (Test-Path -LiteralPath $InstallRoot)) { return }
    $items = Get-ChildItem -LiteralPath $InstallRoot -Force -ErrorAction SilentlyContinue
    if (-not $items) { return }
    $backupRoot = "$InstallRoot.backup.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Write-Step "Backing up existing install to $backupRoot ..."
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $payloadPaths = @()
    foreach ($item in $items) {
        $dest = Join-Path $backupRoot $item.Name
        if ($item.PSIsContainer) {
            Copy-Item -LiteralPath $item.FullName -Destination $dest -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            Copy-Item -LiteralPath $item.FullName -Destination $dest -Force -ErrorAction SilentlyContinue
        }
        $payloadPaths += $item.Name
    }
    $manifest = @{ backed_up_at = (Get-Date).ToUniversalTime().ToString("o"); paths = $payloadPaths }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $backupRoot "backup_manifest.json") -Encoding UTF8
    Write-Step "Backup complete: $backupRoot"
}

function Invoke-CleanInstallVerify {
    param([string]$PythonPath, [string]$ConfigPath)
    Write-Step "Running clean-install verification..."
    $checks = @()

    $selfCheck = & $PythonPath -m wls --config $ConfigPath self-check 2>&1 | ConvertFrom-Json
    $checks += @{ name = "self-check"; passed = $selfCheck.ok }

    $verify = & $PythonPath -m wls --config $ConfigPath verify 2>&1 | ConvertFrom-Json
    $checks += @{ name = "verify"; passed = $verify.ok }

    $status = & $PythonPath -m wls --config $ConfigPath status 2>&1 | ConvertFrom-Json
    $checks += @{ name = "status"; passed = ($status.version -eq "0.9.0.dev2") }

    $allPassed = ($checks | Where-Object { -not $_.passed }).Count -eq 0
    if (-not $allPassed) {
        $failed = ($checks | Where-Object { -not $_.passed } | ForEach-Object { $_.name }) -join ", "
        Write-Warning "Clean-install verification: FAILED checks: $failed"
    } else {
        Write-Step "Clean-install verification: all checks passed."
    }
    return @{ passed = $allPassed; checks = $checks }
}

# === PREFLIGHT ===
Assert-PackageHashes
Assert-CurrentStateGate
Backup-ExistingInstall

$root = [System.IO.Path]::GetFullPath($InstallRoot)
$venv = Join-Path $root "venv"
$wlsHome = Join-Path $root "home"
$receiptPath = Join-Path $root "INSTALL_RECEIPT.json"
$wheel = Get-ChildItem -LiteralPath (Join-Path $PackageRoot "wheel") -Filter "*.whl" | Select-Object -First 1
if ($null -eq $wheel) { throw "wheel missing" }

$version = & $PythonExe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 42)"
if ($LASTEXITCODE -ne 0) { throw "Python >=3.11 is required" }

if ($DryRun) {
    [PSCustomObject]@{ dry_run = $true; install_root = $root; wheel = $wheel.Name; preflight = "passed" } | ConvertTo-Json -Depth 4
    exit 0
}

if ((Test-Path -LiteralPath $root) -and (Get-ChildItem -LiteralPath $root -Force | Select-Object -First 1)) {
    throw "refusing to overwrite non-empty install root: $root (backup completed above if needed)"
}

$created = New-Object System.Collections.Generic.List[string]
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# === TRANSACTIONAL INSTALL ===
try {
    Write-Step "Creating install root: $root"
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $created.Add($root)

    Write-Step "Creating venv..."
    & $PythonExe -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    $created.Add($venv)
    $python = Join-Path $venv "Scripts\python.exe"

    Write-Step "Installing wheel (no pycache compile)..."
    $env:PYTHONDONTWRITEBYTECODE = "1"
    & $python -m pip install --disable-pip-version-check --no-index --no-deps $wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw "wheel install failed" }
    Remove-Item Env:\PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue

    Write-Step "Initializing WLS home..."
    & $python -m wls init --home $wlsHome
    if ($LASTEXITCODE -ne 0) { throw "wls init failed" }
    $created.Add($wlsHome)

    $configPath = Join-Path $wlsHome "config.json"
    $config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
    $config.provider = [PSCustomObject]@{ type = "deterministic"; fallback = "deterministic"; goal_mode = "disabled"; memory_mode = "disabled" }
    $config.plugin_modules = @()
    [System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 20), $utf8NoBom)

    $receipt = [PSCustomObject]@{
        package_digest = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PackageRoot "PACKAGE_MANIFEST.json")).Hash.ToLowerInvariant()
        installed_at = (Get-Date).ToUniversalTime().ToString("o")
        install_root = $root
        wheel = $wheel.Name
        wheel_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheel.FullName).Hash.ToLowerInvariant()
        providers_disabled = $true
        examiner_shadow_disabled = $true
        created_paths = $created.ToArray()
    }
    [System.IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 20), $utf8NoBom)

    Write-Step "Installation complete. Running verification..."
    $verifyResult = Invoke-CleanInstallVerify -PythonPath $python -ConfigPath $configPath

    $result = [PSCustomObject]@{
        installed = $true
        install_root = $root
        receipt = $receiptPath
        verification = $verifyResult
    }
    $result | ConvertTo-Json -Depth 6
} catch {
    Write-Host "[WLS-INSTALL] FAILED: $_" -ForegroundColor Red
    Write-Host "[WLS-INSTALL] Rolling back..." -ForegroundColor Yellow
    foreach ($path in ($created.ToArray() | Sort-Object Length -Descending)) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    throw
}
