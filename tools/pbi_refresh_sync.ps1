<#
.SYNOPSIS
    Fetch Power BI refresh schedules and POST to the governance API.
    Caches the access token to avoid repeated login prompts.
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
$TokenFile = Join-Path $PSScriptRoot "pbi_token.json"
$PbiBase = "https://api.powerbi.com/v1.0/myorg"

function Get-CachedToken {
    if (-not (Test-Path $TokenFile)) { return $null }
    try {
        $cache = Get-Content $TokenFile -Raw | ConvertFrom-Json
        $expiry = [datetime]::Parse($cache.expires_at).ToUniversalTime()
        # Allow 5 min buffer before expiry
        if ($expiry -gt (Get-Date).ToUniversalTime().AddMinutes(5)) {
            return $cache.access_token
        }
    } catch {}
    return $null
}

function Save-Token {
    param([string]$Token)
    # Access tokens typically expire in 60-90 min; save with 60 min expiry
    $expiry = (Get-Date).ToUniversalTime().AddMinutes(55).ToString("o")
    @{ access_token = $Token; expires_at = $expiry } | ConvertTo-Json | Set-Content $TokenFile -Force
}

function Get-PbiToken {
    # Try cached token first
    $cached = Get-CachedToken
    if ($cached) {
        Write-Host "Using cached token." -ForegroundColor Green
        return $cached
    }

    # Need interactive login
    Write-Host "Token expired or not found - logging in..." -ForegroundColor Yellow

    if (-not (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt)) {
        Write-Error "MicrosoftPowerBIMgmt module not installed. Run: Install-Module -Name MicrosoftPowerBIMgmt -Scope CurrentUser"
        Read-Host "Press Enter to exit"
        exit 1
    }

    Import-Module MicrosoftPowerBIMgmt -ErrorAction Stop

    try {
        Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
        Write-Host "Connected." -ForegroundColor Green
    } catch {
        Write-Error "Failed to connect to Power BI: $_"
        Read-Host "Press Enter to exit"
        exit 1
    }

    # Extract and cache the access token
    $tokenResult = Get-PowerBIAccessToken -AsString
    # Returns "Bearer <token>" - strip the prefix
    $token = $tokenResult.Replace("Bearer ", "").Trim()
    Save-Token -Token $token
    return $token
}

function Invoke-PbiApi {
    param([string]$Url, [string]$Token)
    $headers = @{ Authorization = "Bearer $Token" }
    $response = Invoke-RestMethod -Uri "$PbiBase/$Url" -Headers $headers -Method Get
    return $response
}

# Get token (cached or interactive)
$token = Get-PbiToken

# Find workspace
Write-Host "Finding workspace '$WorkspaceName'..." -ForegroundColor Cyan
try {
    $workspaces = Invoke-PbiApi -Url "groups" -Token $token
} catch {
    # Token might be invalid despite cache - retry with fresh login
    Write-Host "Cached token rejected, re-authenticating..." -ForegroundColor Yellow
    if (Test-Path $TokenFile) { Remove-Item $TokenFile -Force }
    $token = Get-PbiToken
    $workspaces = Invoke-PbiApi -Url "groups" -Token $token
}

$ws = $workspaces.value | Where-Object { $_.name -eq $WorkspaceName }
if (-not $ws) {
    Write-Error "Workspace '$WorkspaceName' not found"
    Read-Host "Press Enter to exit"
    exit 1
}

$wsId = $ws.id
Write-Host "Workspace: $WorkspaceName ($wsId)" -ForegroundColor Cyan

# Get all reports
$reportsRaw = Invoke-PbiApi -Url "groups/$wsId/reports" -Token $token

# Get all datasets
$datasetsRaw = Invoke-PbiApi -Url "groups/$wsId/datasets" -Token $token

# Build dataset ID -> report info map
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
        $schedRaw = Invoke-PbiApi -Url "groups/$wsId/datasets/$($ds.id)/refreshSchedule" -Token $token
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
        $histRaw = Invoke-PbiApi -Url "groups/$wsId/datasets/$($ds.id)/refreshes?`$top=1" -Token $token
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
