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
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

function Pause-IfNeeded {
    param([string]$Message = "Press Enter to close")
    if (-not $NoPause) {
        Read-Host $Message
    }
}

function Report-SyncStatus {
    param(
        [string]$Status,
        [string]$Message
    )
    try {
        $payload = @{
            sync_type = "refresh"
            status    = $Status
            message   = $Message
        } | ConvertTo-Json -Depth 4
        Invoke-RestMethod -Uri "$ApiBase/api/scanner/pbi-sync/run-status" -Method POST -Body $payload -ContentType "application/json; charset=utf-8" | Out-Null
    } catch {
        Write-Host "Could not report sync status: $_" -ForegroundColor DarkYellow
    }
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
                "-TimeoutSeconds", "90"
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
    try {
        Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
        Write-Host "Connected." -ForegroundColor Green
    } catch {
        Write-Error "Failed to connect to Power BI: $_"
        Report-SyncStatus -Status "failed" -Message "Failed to connect to Power BI interactively: $_"
        Pause-IfNeeded "Press Enter to exit"
        exit 1
    } finally {
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

# POST to governance API
$json = $output | ConvertTo-Json -Depth 5
try {
    $response = Invoke-RestMethod -Uri "$ApiBase/api/scanner/pbi-import" -Method POST -Body $json -ContentType "application/json; charset=utf-8"
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
