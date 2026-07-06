param(
    [Parameter(Mandatory = $false)]
    [string]$InstallRoot = "D:\WLS\wls-0.9.0.dev1-ui-20260706",

    [Parameter(Mandatory = $false)]
    [ValidateSet("status", "start", "stop", "restart", "open")]
    [string]$Action = "status",

    [Parameter(Mandatory = $false)]
    [int]$Port = 8876
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$WlsPython = Join-Path $InstallRoot "venv\Scripts\python.exe"
$WlsConfig = Join-Path $InstallRoot "home\config.json"
$OpsRoot = Join-Path $InstallRoot "ops"
$ServerScript = Join-Path $OpsRoot "run_wls_ui_server.py"
$RuntimeRoot = Join-Path $InstallRoot "runtime"
$PidFile = Join-Path $RuntimeRoot "wls-ui.pid"
$ReadyFile = Join-Path $RuntimeRoot "wls-ui-ready.json"
$StdoutFile = Join-Path $RuntimeRoot "wls-ui.stdout.txt"
$StderrFile = Join-Path $RuntimeRoot "wls-ui.stderr.txt"

function Assert-InstallReady {
    foreach ($PathValue in @($InstallRoot, $WlsPython, $WlsConfig, $ServerScript)) {
        if (-not (Test-Path -LiteralPath $PathValue)) {
            throw "Required WLS UI path is missing: $PathValue"
        }
    }
    if (-not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) {
        New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    }
}

function Get-ManagedPid {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        return $null
    }
    $Text = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    if ($Text -notmatch '^\d+$') {
        return $null
    }
    return [int]$Text
}

function Get-ManagedProcess {
    $PidValue = Get-ManagedPid
    if ($null -eq $PidValue) {
        return $null
    }
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$PidValue" -ErrorAction SilentlyContinue
    if ($null -eq $Process) {
        return $null
    }
    if ($Process.CommandLine -notlike "*run_wls_ui_server.py*" -or $Process.CommandLine -notlike "*$InstallRoot*") {
        return $null
    }
    return $Process
}

function Get-InstallUiProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -like "*$InstallRoot*" -and (
                $_.CommandLine -like "*run_wls_ui_server.py*" -or
                $_.CommandLine -like "*run_installed_visible_server.py*"
            )
        }
}

function Get-PortOwner {
    $Connection = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $Connection) {
        return $null
    }
    return [int]$Connection.OwningProcess
}

function Read-Ready {
    if (-not (Test-Path -LiteralPath $ReadyFile -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $ReadyFile -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Show-Status {
    Assert-InstallReady
    $Managed = Get-ManagedProcess
    $PortOwner = Get-PortOwner
    $Ready = Read-Ready
    [pscustomobject]@{
        install_root = $InstallRoot
        config = $WlsConfig
        managed_pid = if ($null -eq $Managed) { $null } else { [int]$Managed.ProcessId }
        install_ui_pids = @(Get-InstallUiProcesses | ForEach-Object { [int]$_.ProcessId })
        port = $Port
        port_owner_pid = $PortOwner
        ready_url = if ($null -eq $Ready) { $null } else { $Ready.url }
        ready_file = $ReadyFile
        stdout = $StdoutFile
        stderr = $StderrFile
    } | ConvertTo-Json -Depth 4
}

function Start-WlsUi {
    Assert-InstallReady
    $Existing = @(Get-InstallUiProcesses)
    $PortOwner = Get-PortOwner
    if ($null -ne $PortOwner) {
        $Owned = $Existing | Where-Object { [int]$_.ProcessId -eq $PortOwner } | Select-Object -First 1
        if ($null -ne $Owned) {
            Set-Content -LiteralPath $PidFile -Value "$PortOwner"
            Show-Status
            return
        }
        throw "Port $Port is already owned by PID $PortOwner. Stop that process or choose another port."
    }
    if ($Existing.Count -gt 0) {
        foreach ($Process in $Existing) {
            Stop-Process -Id ([int]$Process.ProcessId) -Force
        }
        Start-Sleep -Seconds 1
    }
    Remove-Item -LiteralPath $ReadyFile -Force -ErrorAction SilentlyContinue
    $Args = @(
        $ServerScript,
        "--config", $WlsConfig,
        "--host", "127.0.0.1",
        "--port", "$Port",
        "--ready-file", $ReadyFile
    )
    $Process = Start-Process -FilePath $WlsPython -ArgumentList $Args -WorkingDirectory $InstallRoot `
        -RedirectStandardOutput $StdoutFile -RedirectStandardError $StderrFile -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath $PidFile -Value "$($Process.Id)"
    $Deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $Deadline) {
        if ($Process.HasExited) {
            throw "WLS UI exited early with code $($Process.ExitCode). See $StderrFile"
        }
        $PortOwner = Get-PortOwner
        if ((Test-Path -LiteralPath $ReadyFile -PathType Leaf) -and $null -ne $PortOwner) {
            Set-Content -LiteralPath $PidFile -Value "$PortOwner"
            Show-Status
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Timed out waiting for WLS UI ready file and listening port: $ReadyFile"
}

function Stop-WlsUi {
    Assert-InstallReady
    $Processes = @(Get-InstallUiProcesses)
    foreach ($Process in $Processes) {
        Stop-Process -Id ([int]$Process.ProcessId) -Force
    }
    Start-Sleep -Seconds 1
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ReadyFile -Force -ErrorAction SilentlyContinue
    Show-Status
}

function Open-WlsUi {
    Assert-InstallReady
    Stop-WlsUi | Out-Null
    Start-WlsUi | Out-Null
    $Ready = Read-Ready
    if ($null -eq $Ready -or -not $Ready.url) {
        throw "WLS UI is running but no ready URL is available."
    }
    Start-Process $Ready.url
    Show-Status
}

switch ($Action) {
    "status" { Show-Status }
    "start" { Start-WlsUi }
    "stop" { Stop-WlsUi }
    "restart" {
        Stop-WlsUi | Out-Null
        Start-WlsUi
    }
    "open" { Open-WlsUi }
}
