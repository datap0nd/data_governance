<#
.SYNOPSIS
    Fetch Power BI report view counts and POST to the governance API.
    Only fetches days not already synced. Safe to run repeatedly.
.PARAMETER ApiBase
    Base URL of the governance API. Default: http://localhost:8000
.PARAMETER DaysBack
    How many days back to check. Default: 30
#>
param(
    [string]$ApiBase = "http://localhost:8000",
    [int]$DaysBack = 30
)

$ErrorActionPreference = "Stop"

if (-not (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt)) {
    Write-Error "MicrosoftPowerBIMgmt module not installed. Run: Install-Module -Name MicrosoftPowerBIMgmt -Scope CurrentUser"
    Read-Host "Press Enter to exit"
    exit 1
}

Import-Module MicrosoftPowerBIMgmt -ErrorAction Stop

# Connect
Write-Host "Connecting to Power BI..." -ForegroundColor Yellow
try {
    Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
    Write-Host "Connected." -ForegroundColor Green
} catch {
    Write-Error "Failed to connect to Power BI: $_"
    Read-Host "Press Enter to exit"
    exit 1
}

# Get already-synced days from the governance API
Write-Host "Checking previously synced days..." -ForegroundColor Yellow
$syncedDays = @()
try {
    $syncedDays = Invoke-RestMethod -Uri "$ApiBase/api/scanner/pbi-usage-days" -Method GET
} catch {
    Write-Host "Could not fetch synced days (first run?): $_" -ForegroundColor Yellow
}

$syncedSet = @{}
foreach ($d in $syncedDays) {
    $syncedSet[$d] = $true
}

# Build list of days to fetch
$today = (Get-Date).ToUniversalTime().Date
$daysToFetch = @()
for ($i = 1; $i -le $DaysBack; $i++) {
    $day = $today.AddDays(-$i)
    $dayStr = $day.ToString("yyyy-MM-dd")
    if (-not $syncedSet.ContainsKey($dayStr)) {
        $daysToFetch += $day
    }
}

if ($daysToFetch.Count -eq 0) {
    Write-Host "All days already synced. Nothing to do." -ForegroundColor Green
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host "Fetching $($daysToFetch.Count) unsynced day(s)..." -ForegroundColor Cyan

$allEntries = @()
$syncedDaysList = @()

foreach ($day in $daysToFetch) {
    $dayStr = $day.ToString("yyyy-MM-dd")
    $startDt = "$($dayStr)T00:00:00.000Z"
    $endDt = "$($dayStr)T23:59:59.999Z"

    Write-Host "  Fetching $dayStr..." -ForegroundColor Gray -NoNewline

    try {
        $eventsJson = Get-PowerBIActivityEvent -StartDateTime $startDt -EndDateTime $endDt
        $events = $eventsJson | ConvertFrom-Json

        # Filter for ViewReport only
        $viewEvents = $events | Where-Object { $_.Activity -eq "ViewReport" -and $_.ReportName }

        # Aggregate by report name
        $grouped = @{}
        $userSets = @{}
        foreach ($ev in $viewEvents) {
            $rptName = $ev.ReportName
            if (-not $grouped.ContainsKey($rptName)) {
                $grouped[$rptName] = 0
                $userSets[$rptName] = @{}
            }
            $grouped[$rptName]++
            if ($ev.UserId) {
                $userSets[$rptName][$ev.UserId] = $true
            }
        }

        foreach ($rptName in $grouped.Keys) {
            $allEntries += @{
                report_name  = $rptName
                date         = $dayStr
                view_count   = $grouped[$rptName]
                unique_users = $userSets[$rptName].Count
            }
        }

        $syncedDaysList += $dayStr
        $viewCount = ($viewEvents | Measure-Object).Count
        Write-Host " $viewCount views" -ForegroundColor $(if ($viewCount -gt 0) { "Green" } else { "Gray" })
    } catch {
        Write-Host " FAILED: $_" -ForegroundColor Red
    }
}

if ($allEntries.Count -eq 0 -and $syncedDaysList.Count -gt 0) {
    Write-Host "No view events found, but marking days as synced." -ForegroundColor Yellow
}

# POST to governance API
$output = @{
    entries     = $allEntries
    days_synced = $syncedDaysList
}

$json = $output | ConvertTo-Json -Depth 5
try {
    $response = Invoke-RestMethod -Uri "$ApiBase/api/scanner/pbi-usage-import" -Method POST -Body $json -ContentType "application/json; charset=utf-8"
    Write-Host ""
    Write-Host "Usage sync complete!" -ForegroundColor Green
    Write-Host "  Days synced: $($syncedDaysList.Count)" -ForegroundColor Green
    Write-Host "  Report entries: $($response.total_entries)" -ForegroundColor Green
    Write-Host "  Matched to DB: $($response.matched)" -ForegroundColor Green
} catch {
    Write-Error "Failed to POST to governance API: $_"
}

Read-Host "Press Enter to close"
