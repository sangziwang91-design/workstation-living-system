param(
    [Parameter(Mandatory = $false)]
    [string]$InstallRoot = "D:\WLS\wls-0.9.0.dev1-py313",

    [Parameter(Mandatory = $false)]
    [string]$CampaignHome = "D:\WLS\campaigns\life-campaign-30",

    [Parameter(Mandatory = $false)]
    [string]$StartRound = "R01",

    [Parameter(Mandatory = $false)]
    [string]$EndRound = "R05",

    [Parameter(Mandatory = $false)]
    [string]$DevPython = "",

    [Parameter(Mandatory = $false)]
    [switch]$Execute,

    [Parameter(Mandatory = $false)]
    [switch]$FreshSnapshot,

    [Parameter(Mandatory = $false)]
    [switch]$AuthorizeLevel2,

    [Parameter(Mandatory = $false)]
    [string]$OwnerStopRound = "",

    [Parameter(Mandatory = $false)]
    [string]$OwnerStopReason = "Owner stopped round",

    [Parameter(Mandatory = $false)]
    [string]$AuthorizePartialContinuationRound = "",

    [Parameter(Mandatory = $false)]
    [string]$PartialContinuationReason = "Owner authorized partial continuation",

    [Parameter(Mandatory = $false)]
    [int]$R15DurationSeconds = 86400,

    [Parameter(Mandatory = $false)]
    [int]$R15HeartbeatSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-ExistingDirectory {
    param([string]$PathValue, [string]$Name)
    $resolved = Resolve-Path -LiteralPath $PathValue -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $resolved.Path -PathType Container)) {
        throw "$Name is not a directory: $($resolved.Path)"
    }
    return $resolved.Path
}

function Resolve-ExistingFile {
    param([string]$PathValue, [string]$Name)
    $resolved = Resolve-Path -LiteralPath $PathValue -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $resolved.Path -PathType Leaf)) {
        throw "$Name is not a file: $($resolved.Path)"
    }
    return $resolved.Path
}

$ScriptPath = $PSCommandPath
if (-not $ScriptPath) {
    $ScriptPath = $MyInvocation.MyCommand.Path
}
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptPath)
$Runner = Join-Path $RepoRoot "source\scripts\run_life_campaign_30.py"
$Spec = Join-Path $RepoRoot "source\verification\life_campaign_30.json"

$InstallRootResolved = Resolve-ExistingDirectory $InstallRoot "InstallRoot"
$LiveHome = Join-Path $InstallRootResolved "home"
$LiveHomeResolved = Resolve-ExistingDirectory $LiveHome "LiveHome"
$WlsPython = Join-Path $InstallRootResolved "venv\Scripts\python.exe"
$WlsPythonResolved = Resolve-ExistingFile $WlsPython "WlsPython"
$RunnerResolved = Resolve-ExistingFile $Runner "Runner"
$SpecResolved = Resolve-ExistingFile $Spec "Spec"

if ($DevPython -eq "") {
    $DevPythonResolved = (Get-Command python -ErrorAction Stop).Source
} else {
    $DevPythonResolved = Resolve-ExistingFile $DevPython "DevPython"
}

$CampaignParent = Split-Path -Parent $CampaignHome
if (-not (Test-Path -LiteralPath $CampaignParent -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $CampaignParent | Out-Null
}
$CampaignHomeFull = [System.IO.Path]::GetFullPath($CampaignHome)
$LiveHomeFull = [System.IO.Path]::GetFullPath($LiveHomeResolved)
if ($CampaignHomeFull -eq $LiveHomeFull) {
    throw "CampaignHome must not equal live home."
}
if ($CampaignHomeFull.StartsWith($LiveHomeFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "CampaignHome must not be inside live home."
}

$Arguments = @(
    $RunnerResolved,
    "--spec", $SpecResolved,
    "--install-root", $InstallRootResolved,
    "--live-home", $LiveHomeResolved,
    "--campaign-home", $CampaignHomeFull,
    "--wls-python", $WlsPythonResolved,
    "--start-round", $StartRound,
    "--end-round", $EndRound
)

if ($Execute) {
    $Arguments += "--execute"
}
if ($FreshSnapshot) {
    $Arguments += "--fresh-snapshot"
}
if ($AuthorizeLevel2) {
    $Arguments += "--authorize-level2"
}
if ($OwnerStopRound -ne "") {
    $Arguments += @("--owner-stop-round", $OwnerStopRound)
    $Arguments += @("--owner-stop-reason", $OwnerStopReason)
}
if ($AuthorizePartialContinuationRound -ne "") {
    $Arguments += @("--authorize-partial-continuation-round", $AuthorizePartialContinuationRound)
    $Arguments += @("--partial-continuation-reason", $PartialContinuationReason)
}
$Arguments += @("--r15-duration-seconds", "$R15DurationSeconds")
$Arguments += @("--r15-heartbeat-seconds", "$R15HeartbeatSeconds")

& $DevPythonResolved @Arguments
exit $LASTEXITCODE
