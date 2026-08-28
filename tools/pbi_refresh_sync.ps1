<#
.SYNOPSIS
    Fetch Power BI refresh schedules and POST to the governance API.
.PARAMETER WorkspaceName
    Name of the Power BI workspace to scan. Defaults to DG_PBI_WORKSPACE.
.PARAMETER ApiBase
    Base URL of the governance API. Default: http://localhost:8000
#>
param(
    [string]$WorkspaceName = $env:DG_PBI_WORKSPACE,
    [string]$ApiBase = "http://localhost:8000",
    [string]$PreferredAccount = $env:DG_PBI_ACCOUNT,
    [int]$ConnectTimeoutSeconds = 120,
    [string]$AttemptId = $env:DG_PBI_ATTEMPT_ID,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$script:SyncTerminalReported = $false

$helperScript = Join-Path $PSScriptRoot "pbi_sync_helpers.ps1"
if (Test-Path $helperScript) {
    . $helperScript
    $ConnectTimeoutSeconds = Get-DgPbiConnectTimeout -DefaultSeconds $ConnectTimeoutSeconds
}

function Pause-IfNeeded {
    param([string]$Message = "Press Enter to close")
    if (-not $NoPause) {
        Read-Host $Message
    }
}

function Report-SyncStatus {
    param(
        [string]$Status,
        [string]$Message,
        $Details = $null
    )
    try {
        $payload = @{
            sync_type = "refresh"
            status    = $Status
            message   = $Message
        }
        $correlatedDetails = @{}
        if ($AttemptId) {
            $payload.attempt_id = $AttemptId
            $correlatedDetails.attempt_id = $AttemptId
        }
        if ($Details -is [System.Collections.IDictionary]) {
            foreach ($key in $Details.Keys) {
                if ($key -ne "attempt_id") {
                    $correlatedDetails[$key] = $Details[$key]
                }
            }
        } elseif ($Details) {
            $correlatedDetails.callback_details = $Details
        }
        if ($correlatedDetails.Count -gt 0) {
            $payload.details = $correlatedDetails
        }
        $json = $payload | ConvertTo-Json -Depth 6
        Invoke-RestMethod -Uri "$ApiBase/api/scanner/pbi-sync/run-status" -Method POST -Body $json -ContentType "application/json; charset=utf-8" | Out-Null
        if ($Status -in @("completed", "failed", "skipped", "stopped")) {
            $script:SyncTerminalReported = $true
        }
    } catch {
        Write-Host "Could not report sync status: $_" -ForegroundColor DarkYellow
    }
}

function Start-CorrelatedPbiConnectWatchdog {
    param(
        [string]$ApiBase,
        [string]$SyncType,
        [int]$TimeoutSeconds,
        [string]$AttemptId,
        [int]$ParentPid = $PID
    )

    if ($TimeoutSeconds -le 0) {
        return $null
    }
    if (-not $AttemptId -and (Get-Command Start-DgPbiConnectWatchdog -ErrorAction SilentlyContinue)) {
        return Start-DgPbiConnectWatchdog -ApiBase $ApiBase -SyncType $SyncType -TimeoutSeconds $TimeoutSeconds -ParentPid $ParentPid
    }

    $message = "Interactive Power BI sign-in did not complete within $TimeoutSeconds seconds. Windows may be locked, disconnected, or waiting at the Microsoft account picker."
    $script = @"
`$ErrorActionPreference = "SilentlyContinue"
Start-Sleep -Seconds $TimeoutSeconds
`$parent = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
if (`$parent) {
    try {
        `$payload = @{
            sync_type = "$SyncType"
            status = "failed"
            message = "$message"
            attempt_id = "$AttemptId"
            details = @{ attempt_id = "$AttemptId" }
        } | ConvertTo-Json -Depth 4
        Invoke-RestMethod -Uri "$ApiBase/api/scanner/pbi-sync/run-status" -Method POST -Body `$payload -ContentType "application/json; charset=utf-8" | Out-Null
    } catch {}
    Stop-Process -Id $ParentPid -Force -ErrorAction SilentlyContinue
}
"@

    try {
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
        return Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", $encoded
        )
    } catch {
        return $null
    }
}

# Several Power BI cmdlets below intentionally run at script scope.  A
# terminating module/API error must still close the exact correlated attempt;
# otherwise Metronome would display it as running until the 20-minute waiter
# times out.  Local catches may provide a more specific failure first.
trap {
    $failureText = "Unhandled Power BI refresh sync failure: $($_.Exception.Message)"
    if (-not $script:SyncTerminalReported) {
        Report-SyncStatus -Status "failed" -Message $failureText
    }
    Write-Host $failureText -ForegroundColor Red
    Pause-IfNeeded "Press Enter to close"
    exit 1
}

if (-not (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt)) {
    Write-Error "MicrosoftPowerBIMgmt module not installed. Run: Install-Module -Name MicrosoftPowerBIMgmt -Scope CurrentUser"
    Report-SyncStatus -Status "failed" -Message "MicrosoftPowerBIMgmt module is not installed."
    Pause-IfNeeded "Press Enter to exit"
    exit 1
}

Import-Module MicrosoftPowerBIMgmt -ErrorAction Stop

if (-not $WorkspaceName) {
    Write-Error "No Power BI workspace configured. Set DG_PBI_WORKSPACE or pass -WorkspaceName."
    Report-SyncStatus -Status "failed" -Message "No Power BI workspace configured."
    Pause-IfNeeded "Press Enter to exit"
    exit 1
}

function Test-ServicePrincipalConfig {
    return [bool]($env:DG_PBI_TENANT_ID -and $env:DG_PBI_CLIENT_ID -and $env:DG_PBI_CLIENT_SECRET)
}

function Connect-DgPowerBI {
    if (Test-ServicePrincipalConfig) {
        Write-Host "Connecting to Power BI with service principal..." -ForegroundColor Yellow
        try {
            $secureSecret = ConvertTo-SecureString $env:DG_PBI_CLIENT_SECRET -AsPlainText -Force
            $credential = New-Object System.Management.Automation.PSCredential($env:DG_PBI_CLIENT_ID, $secureSecret)
            Connect-PowerBIServiceAccount -ServicePrincipal -Tenant $env:DG_PBI_TENANT_ID -Credential $credential -ErrorAction Stop | Out-Null
            Write-Host "Connected with service principal." -ForegroundColor Green
            return
        } catch {
            Write-Error "Failed to connect to Power BI with service principal: $_"
            Report-SyncStatus -Status "failed" -Message "Failed to connect to Power BI with service principal: $_"
            Pause-IfNeeded "Press Enter to exit"
            exit 1
        }
    }

    if (Get-Command Test-DgInteractiveSignInReady -ErrorAction SilentlyContinue) {
        $preflight = Test-DgInteractiveSignInReady
        if (-not $preflight.Ready) {
            Write-Host $preflight.Message -ForegroundColor Red
            Report-SyncStatus -Status "failed" -Message $preflight.Message -Details $preflight.Details
            Pause-IfNeeded "Press Enter to exit"
            exit 2
        }
        Write-Host "Interactive sign-in preflight: $($preflight.Message)" -ForegroundColor DarkGray
    }

    # Spawn the auto-clicker so the MSAL "Pick an account" popup is dismissed automatically.
    # This fallback requires an unlocked interactive Windows session.
    $clicker = $null
    $clickerScript = Join-Path $PSScriptRoot "pbi_auto_click_picker.ps1"
    if (Test-Path $clickerScript) {
        try {
            $clickerArgs = @(
                "-ExecutionPolicy", "Bypass",
                "-NoProfile",
                "-STA",
                "-File", $clickerScript,
                "-TimeoutSeconds", "$ConnectTimeoutSeconds"
            )
            if ($PreferredAccount) {
                $clickerArgs += @("-PreferredAccount", $PreferredAccount)
            }
            $clicker = Start-Process powershell.exe -PassThru -WindowStyle Hidden -ArgumentList $clickerArgs
            Write-Host "Auto-clicker started (PID $($clicker.Id))." -ForegroundColor DarkGray
        } catch {
            Write-Host "Could not start auto-clicker: $_" -ForegroundColor DarkYellow
        }
    }

    Write-Host "Connecting to Power BI..." -ForegroundColor Yellow
    $watchdog = $null
    try {
        if (Get-Command Start-DgPbiConnectWatchdog -ErrorAction SilentlyContinue) {
            $watchdog = Start-CorrelatedPbiConnectWatchdog -ApiBase $ApiBase -SyncType "refresh" -TimeoutSeconds $ConnectTimeoutSeconds -AttemptId $AttemptId
        }
        Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
        Write-Host "Connected." -ForegroundColor Green
    } catch {
        Write-Error "Failed to connect to Power BI: $_"
        Report-SyncStatus -Status "failed" -Message "Failed to connect to Power BI interactively: $_"
        Pause-IfNeeded "Press Enter to exit"
        exit 1
    } finally {
        if (Get-Command Stop-DgPbiConnectWatchdog -ErrorAction SilentlyContinue) {
            Stop-DgPbiConnectWatchdog -Watchdog $watchdog
        }
        if ($clicker -and -not $clicker.HasExited) {
            Stop-Process -Id $clicker.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

Connect-DgPowerBI

# Find workspace
$ws = Get-PowerBIWorkspace | Where-Object { $_.Name -eq $WorkspaceName }
if (-not $ws) {
    Write-Error "Workspace '$WorkspaceName' not found"
    Report-SyncStatus -Status "failed" -Message "Workspace not found."
    Pause-IfNeeded "Press Enter to exit"
    exit 1
}

$wsId = $ws.Id
Write-Host "Workspace: $WorkspaceName ($wsId)" -ForegroundColor Cyan

# Get all reports (to map report name -> dataset ID)
$reportsRaw = Invoke-PowerBIRestMethod -Url "groups/$wsId/reports" -Method Get | ConvertFrom-Json

# Get all datasets
$datasetsRaw = Invoke-PowerBIRestMethod -Url "groups/$wsId/datasets" -Method Get | ConvertFrom-Json

# Build dataset ID -> report info map (name + webUrl)
$datasetReports = @{}
$reportUrls = @{}
foreach ($r in $reportsRaw.value) {
    if (-not $datasetReports.ContainsKey($r.datasetId)) {
        $datasetReports[$r.datasetId] = @()
    }
    $datasetReports[$r.datasetId] += $r.name
    $reportUrls[$r.name] = $r.webUrl
}

$results = @()

foreach ($ds in $datasetsRaw.value) {
    $reportNames = $datasetReports[$ds.id]
    if (-not $reportNames) { continue }

    # Get refresh schedule
    $schedule = $null
    try {
        $schedRaw = Invoke-PowerBIRestMethod -Url "groups/$wsId/datasets/$($ds.id)/refreshSchedule" -Method Get | ConvertFrom-Json
        $schedule = @{
            enabled  = $schedRaw.enabled
            days     = @($schedRaw.days)
            times    = @($schedRaw.times)
            timezone = $schedRaw.localTimeZoneId
        }
    } catch {
        $schedule = @{ enabled = $false; days = @(); times = @(); timezone = "" }
    }

    # Get last refresh from history
    $lastRefresh = $null
    try {
        $histRaw = Invoke-PowerBIRestMethod -Url "groups/$wsId/datasets/$($ds.id)/refreshes?`$top=1" -Method Get | ConvertFrom-Json
        if ($histRaw.value -and $histRaw.value.Count -gt 0) {
            $entry = $histRaw.value[0]
            $lastRefresh = @{
                start_time = $entry.startTime
                end_time   = $entry.endTime
                status     = $entry.status
                error      = if ($entry.serviceExceptionJson) { $entry.serviceExceptionJson } else { $null }
            }
        }
    } catch {
        # No refresh history available
    }

    foreach ($rptName in $reportNames) {
        $results += @{
            report_name  = $rptName
            dataset_name = $ds.name
            dataset_id   = $ds.id
            web_url      = $reportUrls[$rptName]
            schedule     = $schedule
            last_refresh = $lastRefresh
        }
    }
}

Write-Host "Found $($results.Count) report entries." -ForegroundColor Cyan

$output = @{
    workspace  = $WorkspaceName
    synced_at  = (Get-Date).ToUniversalTime().ToString("o")
    reports    = $results
}
if ($AttemptId) {
    $output.attempt_id = $AttemptId
}

# POST to governance API
$json = $output | ConvertTo-Json -Depth 5
try {
    $response = Invoke-RestMethod -Uri "$ApiBase/api/scanner/pbi-import" -Method POST -Body $json -ContentType "application/json; charset=utf-8"
    if ($response.status -eq "stopped") {
        Write-Host ""
        Write-Host "Sync stopped." -ForegroundColor Yellow
        if ($response.message) {
            Write-Host "  $($response.message)" -ForegroundColor Yellow
        }
        Pause-IfNeeded "Press Enter to close"
        exit 0
    }
    if ($response.status -ne "completed") {
        Write-Host ""
        $ignoredMessage = if ($response.message) { $response.message } else { "Sync callback was ignored." }
        Write-Host $ignoredMessage -ForegroundColor Yellow
        Pause-IfNeeded "Press Enter to close"
        exit 1
    }
    $script:SyncTerminalReported = $true
    Write-Host ""
    Write-Host "Sync complete!" -ForegroundColor Green
    Write-Host "  Matched: $($response.matched)" -ForegroundColor Green
    Write-Host "  Unmatched: $($response.unmatched.Count)" -ForegroundColor $(if ($response.unmatched.Count -gt 0) { "Yellow" } else { "Green" })
    if ($response.unmatched.Count -gt 0) {
        Write-Host "  Unmatched reports: $($response.unmatched -join ', ')" -ForegroundColor Yellow
    }
} catch {
    Write-Error "Failed to POST to governance API: $_"
    Report-SyncStatus -Status "failed" -Message "Failed to POST Power BI refresh data to governance API: $_"
}

Pause-IfNeeded "Press Enter to close"
