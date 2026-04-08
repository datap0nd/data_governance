<#
.SYNOPSIS
    Fetch Power BI refresh schedules and POST to the governance API.
    Uses MSAL.NET for token caching with refresh tokens (~90 day persistence).
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
$PbiBase = "https://api.powerbi.com/v1.0/myorg"
$CacheFile = Join-Path $PSScriptRoot "msal_token_cache.bin"

# Power BI public client ID (same one the PBI PowerShell module uses)
$ClientId = "ea0616ba-638b-4df5-95eb-564571f60a21"
$Authority = "https://login.microsoftonline.com/organizations"
$Scopes = @("https://analysis.windows.net/powerbi/api/.default")

# ── Load PBI module (this loads all MSAL dependencies properly) ──

if (-not (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt)) {
    Write-Error "MicrosoftPowerBIMgmt module not installed. Run: Install-Module -Name MicrosoftPowerBIMgmt -Scope CurrentUser"
    Read-Host "Press Enter to exit"
    exit 1
}

Import-Module MicrosoftPowerBIMgmt -ErrorAction Stop

# ── MSAL token acquisition with file-based cache ──

function Get-MsalToken {
    # Build the public client app - keep it simple for MSAL compat
    $app = $null

    # Try full builder chain first
    try {
        $app = [Microsoft.Identity.Client.PublicClientApplicationBuilder]::Create($ClientId).WithAuthority($Authority, $true).WithDefaultRedirectUri().Build()
    } catch {}

    # Fallback: authority without validate param
    if (-not $app) {
        try {
            $app = [Microsoft.Identity.Client.PublicClientApplicationBuilder]::Create($ClientId).WithAuthority($Authority).WithDefaultRedirectUri().Build()
        } catch {}
    }

    # Fallback: no authority, custom redirect
    if (-not $app) {
        try {
            $app = [Microsoft.Identity.Client.PublicClientApplicationBuilder]::Create($ClientId).WithRedirectUri("http://localhost").Build()
        } catch {}
    }

    # Last resort: bare minimum
    if (-not $app) {
        try {
            $app = [Microsoft.Identity.Client.PublicClientApplicationBuilder]::Create($ClientId).Build()
        } catch {
            Write-Error "Failed to create MSAL app: $_"
            Read-Host "Press Enter to exit"
            exit 1
        }
    }

    # Attach file-based token cache
    # MSAL serializes both access tokens AND refresh tokens to this cache
    if (Test-Path $CacheFile) {
        try {
            $cacheData = [System.IO.File]::ReadAllBytes($CacheFile)
            $app.UserTokenCache.DeserializeMsalV3($cacheData)
        } catch {
            Write-Host "Could not load token cache, will require login." -ForegroundColor Yellow
        }
    }

    $token = $null

    # Try silent acquisition first (uses cached refresh token)
    try {
        $accounts = $app.GetAccountsAsync().GetAwaiter().GetResult()
        if ($accounts.Count -gt 0) {
            $account = $accounts | Select-Object -First 1
            Write-Host "Attempting silent login for $($account.Username)..." -ForegroundColor Cyan
            $result = $app.AcquireTokenSilent($Scopes, $account).ExecuteAsync().GetAwaiter().GetResult()
            $token = $result.AccessToken
            Write-Host "Silent login successful." -ForegroundColor Green
        }
    } catch {
        Write-Host "Silent login failed - interactive login required." -ForegroundColor Yellow
    }

    # Fall back to interactive login
    if (-not $token) {
        Write-Host "Opening browser for login..." -ForegroundColor Yellow
        try {
            $result = $app.AcquireTokenInteractive($Scopes).ExecuteAsync().GetAwaiter().GetResult()
            $token = $result.AccessToken
            Write-Host "Login successful." -ForegroundColor Green
        } catch {
            Write-Error "Login failed: $_"
            Read-Host "Press Enter to exit"
            exit 1
        }
    }

    # Save updated cache (includes refresh token for next time)
    try {
        $cacheData = $app.UserTokenCache.SerializeMsalV3()
        [System.IO.File]::WriteAllBytes($CacheFile, $cacheData)
    } catch {
        Write-Host "Could not save token cache: $_" -ForegroundColor Yellow
    }

    return $token
}

function Invoke-PbiApi {
    param([string]$Url, [string]$Token)
    $headers = @{ Authorization = "Bearer $Token" }
    return Invoke-RestMethod -Uri "$PbiBase/$Url" -Headers $headers -Method Get
}

# ── Main ──

$token = Get-MsalToken

# Find workspace
Write-Host "Finding workspace '$WorkspaceName'..." -ForegroundColor Cyan
try {
    $workspaces = Invoke-PbiApi -Url "groups" -Token $token
} catch {
    # Token might be invalid - clear cache and retry
    Write-Host "Token rejected, clearing cache and re-authenticating..." -ForegroundColor Yellow
    if (Test-Path $CacheFile) { Remove-Item $CacheFile -Force }
    $token = Get-MsalToken
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
