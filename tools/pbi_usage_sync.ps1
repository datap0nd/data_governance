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
    [int]$DaysBack = 30,
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
            sync_type = "usage"
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
    Pause-IfNeeded "Press Enter to close"
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
        $errMsg = $_.ToString()
        Write-Host " FAILED: $errMsg" -ForegroundColor Red

        # Abort immediately on auth/permission errors - no point trying remaining days
        if ($errMsg -match "Unauthorized|Forbidden|403|401") {
            Write-Host ""
            Write-Host "PERMISSION ERROR: Your account lacks the required role." -ForegroundColor Red
            Write-Host "Get-PowerBIActivityEvent requires one of:" -ForegroundColor Yellow
            Write-Host "  - Power BI Service Administrator" -ForegroundColor Yellow
            Write-Host "  - Fabric Administrator" -ForegroundColor Yellow
            Write-Host "  - Global Administrator" -ForegroundColor Yellow
            Write-Host "Workspace Admin alone is NOT sufficient." -ForegroundColor Yellow
            Write-Host ""
            Report-SyncStatus -Status "failed" -Message "Power BI activity event permission error."
            Pause-IfNeeded "Press Enter to close"
            exit 1
        }
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
    Report-SyncStatus -Status "failed" -Message "Failed to POST Power BI usage data to governance API: $_"
}

Pause-IfNeeded "Press Enter to close"
