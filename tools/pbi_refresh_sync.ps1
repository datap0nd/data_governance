<#
.SYNOPSIS
    Fetch Power BI refresh schedules and POST to the governance API.
    Run this from your desktop (taskbar shortcut) - NOT from the service.
.PARAMETER WorkspaceName
    Name of the Power BI workspace to scan. Default: mx executive
.PARAMETER ApiBase
    Base URL of the governance API. Default: http://localhost:8000
#>
param(
    [string]$WorkspaceName = "mx executive",
    [string]$ApiBase = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"

# Ensure module is available
if (-not (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt)) {
    Write-Error "MicrosoftPowerBIMgmt module not installed. Run: Install-Module -Name MicrosoftPowerBIMgmt -Scope CurrentUser"
    Read-Host "Press Enter to exit"
    exit 1
}

Import-Module MicrosoftPowerBIMgmt -ErrorAction Stop

# Connect (uses cached token if available, otherwise pops login)
Write-Host "Connecting to Power BI..." -ForegroundColor Yellow
try {
    Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
    Write-Host "Connected." -ForegroundColor Green
} catch {
    Write-Error "Failed to connect to Power BI: $_"
    Read-Host "Press Enter to exit"
    exit 1
}

# Find workspace
$ws = Get-PowerBIWorkspace | Where-Object { $_.Name -eq $WorkspaceName }
if (-not $ws) {
    Write-Error "Workspace '$WorkspaceName' not found"
    Read-Host "Press Enter to exit"
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
}

Read-Host "Press Enter to close"
