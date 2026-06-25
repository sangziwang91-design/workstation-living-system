[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 6)]
    [int]$Phase,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Campaign,

    [Parameter(Mandatory = $true)]
    [string]$CommitSha,

    [Parameter(Mandatory = $true)]
    [string]$PythonExe,

    [ValidateRange(1, 5)]
    [int]$Cycles = 5,

    [ValidateRange(0, 7200)]
    [int]$SleepSeconds = 3600,

    [string]$EvidenceRoot = "D:\actions-runner\_evidence\24h-bidirectional",

    [string]$WlsStateRoot = "D:\actions-runner\_state\24h-wls"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python executable is missing: $PythonExe"
}

New-Item -ItemType Directory -Force $EvidenceRoot | Out-Null
New-Item -ItemType Directory -Force $WlsStateRoot | Out-Null
$evidenceFile = Join-Path $EvidenceRoot "$Campaign.jsonl"
$wlsHome = Join-Path $WlsStateRoot $Campaign
New-Item -ItemType Directory -Force $wlsHome | Out-Null

for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
    $service = Get-Service "actions.runner.*" | Select-Object -First 1
    if ($null -eq $service) {
        throw "No GitHub Actions runner service was found."
    }

    $github443 = Test-NetConnection github.com -Port 443 -InformationLevel Quiet
    $broker443 = Test-NetConnection broker.actions.githubusercontent.com -Port 443 -InformationLevel Quiet
    $drive = Get-PSDrive -Name D
    $sevenZip = Test-Path "D:\Tools\7-Zip\7z.exe"

    $probeLines = & $PythonExe source/scripts/run_local_soak_probe.py `
        --home $wlsHome `
        --campaign $Campaign `
        --commit-sha $CommitSha `
        --phase $Phase `
        --cycle $cycle
    $probeExit = $LASTEXITCODE
    $probeText = ($probeLines | ForEach-Object { "$_" }) -join [Environment]::NewLine

    if ([string]::IsNullOrWhiteSpace($probeText)) {
        throw "WLS soak probe returned no JSON output."
    }

    try {
        $wlsProbe = $probeText | ConvertFrom-Json
    }
    catch {
        throw "WLS soak probe returned invalid JSON: $probeText"
    }

    $sample = [ordered]@{
        schema_version = "2.0"
        campaign = $Campaign
        commit_sha = $CommitSha
        phase = $Phase
        cycle = $cycle
        observed_at_utc = [DateTime]::UtcNow.ToString("o")
        runner_name = $env:RUNNER_NAME
        runner_os = $env:RUNNER_OS
        runner_arch = $env:RUNNER_ARCH
        runner_service = $service.Status.ToString()
        github_443 = [bool]$github443
        broker_443 = [bool]$broker443
        seven_zip = [bool]$sevenZip
        d_free_bytes = [int64]$drive.Free
        wls = $wlsProbe
    }

    $json = $sample | ConvertTo-Json -Depth 20 -Compress
    Add-Content -Path $evidenceFile -Value $json -Encoding utf8
    Write-Host "BIDIRECTIONAL_WLS_HEARTBEAT $json"

    if ($env:GITHUB_STEP_SUMMARY) {
        $summary = "- Phase $Phase cycle $cycle: WLS=$($wlsProbe.cycle_status) integrity=$($wlsProbe.integrity.ok) github=$github443 broker=$broker443 service=$($service.Status) at $($sample.observed_at_utc)"
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $summary
    }

    $healthy = (
        $probeExit -eq 0 -and
        [bool]$wlsProbe.healthy -and
        [bool]$wlsProbe.integrity.ok -and
        [bool]$github443 -and
        [bool]$broker443 -and
        [bool]$sevenZip -and
        $service.Status -eq "Running"
    )
    if (-not $healthy) {
        throw "Bidirectional WLS heartbeat failed in phase $Phase cycle $cycle."
    }

    if ($cycle -lt $Cycles -and $SleepSeconds -gt 0) {
        Start-Sleep -Seconds $SleepSeconds
    }
}

Write-Host "Completed bidirectional WLS phase $Phase. Evidence: $evidenceFile"
