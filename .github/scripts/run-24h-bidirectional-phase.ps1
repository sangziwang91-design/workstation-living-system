[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 6)]
    [int]$Phase,

    [Parameter(Mandatory = $true)]
    [string]$Campaign,

    [Parameter(Mandatory = $true)]
    [string]$CommitSha
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$evidenceRoot = "D:\actions-runner\_evidence\24h-bidirectional"
New-Item -ItemType Directory -Force $evidenceRoot | Out-Null
$evidenceFile = Join-Path $evidenceRoot "$Campaign.jsonl"

for ($cycle = 1; $cycle -le 5; $cycle++) {
    $service = Get-Service "actions.runner.*" | Select-Object -First 1
    $github443 = Test-NetConnection github.com -Port 443 -InformationLevel Quiet
    $broker443 = Test-NetConnection broker.actions.githubusercontent.com -Port 443 -InformationLevel Quiet
    $drive = Get-PSDrive -Name D
    $sevenZip = Test-Path "D:\Tools\7-Zip\7z.exe"

    $sample = [ordered]@{
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
    }

    $json = $sample | ConvertTo-Json -Compress
    Add-Content -Path $evidenceFile -Value $json -Encoding utf8
    Write-Host "BIDIRECTIONAL_HEARTBEAT $json"

    if ($env:GITHUB_STEP_SUMMARY) {
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "- Phase $Phase cycle $cycle: github=$github443 broker=$broker443 service=$($service.Status) at $($sample.observed_at_utc)"
    }

    if (-not $github443 -or -not $broker443 -or -not $sevenZip -or $service.Status -ne "Running") {
        throw "Bidirectional heartbeat failed in phase $Phase cycle $cycle."
    }

    if ($cycle -lt 5) {
        Start-Sleep -Seconds 3600
    }
}

Write-Host "Completed four-hour bidirectional phase $Phase. Evidence: $evidenceFile"
