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
    [int]$ConnectTimeoutSeconds = 120,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

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
            sync_type = "usage"
            status    = $Status
            message   = $Message
        }
        if ($Details) {
            $payload.details = $Details
        }
        $json = $payload | ConvertTo-Json -Depth 6
        Invoke-RestMethod -Uri "$ApiBase/api/scanner/pbi-sync/run-status" -Method POST -Body $json -ContentType "application/json; charset=utf-8" | Out-Null
    } catch {
        Write-Host "Could not report sync status: $_" -ForegroundColor DarkYellow
    }
}

function New-UsageDiagnostic {
    param(
        [string]$Status,
        [string]$ReasonCode,
        [string]$Summary,
        [int]$RequestedDays,
        [int]$SuccessfulDays,
        [int]$FailedDays,
        [int]$SkippedDays,
        [int]$ZeroActivityDays,
        [string[]]$Remediation = @()
    )
    $impact = if ($Status -eq "failed") { "error" } elseif ($Status -eq "completed_with_warnings") { "warning" } else { "none" }
    $facts = @{
        requested_days     = $RequestedDays
        successful_days    = $SuccessfulDays
        failed_days        = $FailedDays
        skipped_days       = $SkippedDays
        zero_activity_days = $ZeroActivityDays
    }
    return @{
        status             = $Status
        reason_code        = $ReasonCode
        operator_summary   = $Summary
        requested_days     = $RequestedDays
        successful_days    = $SuccessfulDays
        failed_days        = $FailedDays
        skipped_days       = $SkippedDays
        zero_activity_days = $ZeroActivityDays
        diagnostic         = @{
            health_impact   = $impact
            reason_code     = $ReasonCode
            operator_summary = $Summary
            remediation     = $Remediation
            facts           = $facts
        }
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
            $watchdog = Start-DgPbiConnectWatchdog -ApiBase $ApiBase -SyncType "usage" -TimeoutSeconds $ConnectTimeoutSeconds
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
    $summary = "Power BI usage metadata is already current; no days were due."
    $details = New-UsageDiagnostic -Status "completed" -ReasonCode "power_bi_usage_already_current" -Summary $summary -RequestedDays 0 -SuccessfulDays 0 -FailedDays 0 -SkippedDays 0 -ZeroActivityDays 0
    Write-Host $summary -ForegroundColor Green
    Report-SyncStatus -Status "completed" -Message $summary -Details $details
    Pause-IfNeeded "Press Enter to close"
    exit 0
}

Write-Host "Fetching $($daysToFetch.Count) unsynced day(s)..." -ForegroundColor Cyan

$allEntries = @()
$syncedDaysList = @()
$failedDays = 0
$skippedDays = 0
$zeroActivityDays = 0
$authorizationDenied = $false

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
        if ($viewCount -eq 0) {
            $zeroActivityDays++
        }
        Write-Host " $viewCount views" -ForegroundColor $(if ($viewCount -gt 0) { "Green" } else { "Gray" })
    } catch {
        $errMsg = $_.ToString()
        $failedDays++
        Write-Host " FAILED: $errMsg" -ForegroundColor Red

        # Abort immediately on auth/permission errors - no point trying remaining days
        if ($errMsg -match "Unauthorized|Forbidden|403|401") {
            $authorizationDenied = $true
            Write-Host ""
            Write-Host "PERMISSION ERROR: Your account lacks the required role." -ForegroundColor Red
            Write-Host "Get-PowerBIActivityEvent requires one of:" -ForegroundColor Yellow
            Write-Host "  - Power BI Service Administrator" -ForegroundColor Yellow
            Write-Host "  - Fabric Administrator" -ForegroundColor Yellow
            Write-Host "  - Global Administrator" -ForegroundColor Yellow
            Write-Host "Workspace Admin alone is NOT sufficient." -ForegroundColor Yellow
            Write-Host ""
            $skippedDays = $daysToFetch.Count - $syncedDaysList.Count - $failedDays
            if ($syncedDaysList.Count -eq 0) {
                $summary = "Power BI rejected Activity Events access (HTTP 401/403); no usage data was imported."
                $details = New-UsageDiagnostic -Status "failed" -ReasonCode "power_bi_usage_authorization_denied" -Summary $summary -RequestedDays $daysToFetch.Count -SuccessfulDays 0 -FailedDays $failedDays -SkippedDays $skippedDays -ZeroActivityDays $zeroActivityDays -Remediation @(
                    "Assign the identity the Fabric administrator or Power BI service administrator role.",
                    "For a service principal, enable the tenant setting for service principals to use read-only admin APIs and include it in the allowed security group."
                )
                Report-SyncStatus -Status "failed" -Message $summary -Details $details
                Pause-IfNeeded "Press Enter to close"
                exit 1
            }
            break
        }
    }
}

if ($syncedDaysList.Count -eq 0 -and $failedDays -gt 0) {
    $summary = "Power BI could not fetch any of the $($daysToFetch.Count) requested usage day(s)."
    $details = New-UsageDiagnostic -Status "failed" -ReasonCode "power_bi_usage_all_days_failed" -Summary $summary -RequestedDays $daysToFetch.Count -SuccessfulDays 0 -FailedDays $failedDays -SkippedDays $skippedDays -ZeroActivityDays $zeroActivityDays -Remediation @(
        "Review the Metronome server log for the classified Activity Events failures, then rerun usage metadata."
    )
    Report-SyncStatus -Status "failed" -Message $summary -Details $details
    Pause-IfNeeded "Press Enter to close"
    exit 1
}

if ($allEntries.Count -eq 0 -and $syncedDaysList.Count -gt 0) {
    Write-Host "No view events found, but marking days as synced." -ForegroundColor Yellow
}

$resultStatus = if ($failedDays -gt 0) { "completed_with_warnings" } else { "completed" }
$reasonCode = if ($authorizationDenied) { "power_bi_usage_authorization_denied" } elseif ($failedDays -gt 0) { "power_bi_usage_partial_failure" } else { "power_bi_usage_completed" }
$summary = if ($authorizationDenied) {
    "Power BI fetched $($syncedDaysList.Count) usage day(s) before Activity Events access was rejected (HTTP 401/403); the remaining days will be retried."
} elseif ($failedDays -gt 0) {
    "Power BI fetched $($syncedDaysList.Count) of $($daysToFetch.Count) requested usage day(s); $failedDays day(s) will be retried."
} else {
    "Power BI fetched all $($syncedDaysList.Count) requested usage day(s)."
}
$remediation = if ($authorizationDenied) {
    @(
        "Assign the identity the Fabric administrator or Power BI service administrator role.",
        "For a service principal, enable the tenant setting for service principals to use read-only admin APIs and include it in the allowed security group."
    )
} elseif ($failedDays -gt 0) { @("Correct the Activity Events request failures and rerun usage metadata.") } else { @() }
$details = New-UsageDiagnostic -Status $resultStatus -ReasonCode $reasonCode -Summary $summary -RequestedDays $daysToFetch.Count -SuccessfulDays $syncedDaysList.Count -FailedDays $failedDays -SkippedDays $skippedDays -ZeroActivityDays $zeroActivityDays -Remediation $remediation

# POST to governance API
$output = @{
    entries             = $allEntries
    days_synced         = $syncedDaysList
    status              = $details.status
    reason_code         = $details.reason_code
    operator_summary    = $details.operator_summary
    requested_days      = $details.requested_days
    successful_days     = $details.successful_days
    failed_days         = $details.failed_days
    skipped_days        = $details.skipped_days
    zero_activity_days  = $details.zero_activity_days
    diagnostic          = $details.diagnostic
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
    $summary = "Power BI usage data was fetched but could not be imported into Metronome."
    $details = New-UsageDiagnostic -Status "failed" -ReasonCode "power_bi_usage_import_failed" -Summary $summary -RequestedDays $daysToFetch.Count -SuccessfulDays $syncedDaysList.Count -FailedDays $failedDays -SkippedDays $skippedDays -ZeroActivityDays $zeroActivityDays -Remediation @("Review the Metronome server log and API availability, then rerun usage metadata.")
    Report-SyncStatus -Status "failed" -Message $summary -Details $details
    Pause-IfNeeded "Press Enter to close"
    exit 1
}

Pause-IfNeeded "Press Enter to close"
