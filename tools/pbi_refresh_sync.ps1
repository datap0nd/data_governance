<#
.SYNOPSIS
    Fetch Power BI refresh schedules and history, output as JSON.
.PARAMETER WorkspaceName
    Name of the Power BI workspace to scan.
.PARAMETER OutputPath
    Path to write the JSON output file.
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$WorkspaceName,

    [Parameter(Mandatory=$true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"

# Ensure module is available
if (-not (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt)) {
    Write-Error "MicrosoftPowerBIMgmt module not installed. Run: Install-Module -Name MicrosoftPowerBIMgmt -Scope CurrentUser"
    exit 1
}

Import-Module MicrosoftPowerBIMgmt -ErrorAction Stop

# Connect (uses cached token if available, otherwise pops login)
try {
    Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
} catch {
    Write-Error "Failed to connect to Power BI: $_"
    exit 1
}

# Find workspace
$ws = Get-PowerBIWorkspace | Where-Object { $_.Name -eq $WorkspaceName }
if (-not $ws) {
    Write-Error "Workspace '$WorkspaceName' not found"
    exit 1
}

$wsId = $ws.Id

# Get all reports (to map report name -> dataset ID)
$reportsRaw = Invoke-PowerBIRestMethod -Url "groups/$wsId/reports" -Method Get | ConvertFrom-Json

# Get all datasets
$datasetsRaw = Invoke-PowerBIRestMethod -Url "groups/$wsId/datasets" -Method Get | ConvertFrom-Json

# Build dataset ID -> report names map
$datasetReports = @{}
foreach ($r in $reportsRaw.value) {
    if (-not $datasetReports.ContainsKey($r.datasetId)) {
        $datasetReports[$r.datasetId] = @()
    }
    $datasetReports[$r.datasetId] += $r.name
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
            schedule     = $schedule
            last_refresh = $lastRefresh
        }
    }
}

$output = @{
    workspace  = $WorkspaceName
    synced_at  = (Get-Date).ToUniversalTime().ToString("o")
    reports    = $results
}

$output | ConvertTo-Json -Depth 5 | Out-File -FilePath $OutputPath -Encoding UTF8
Write-Host "Exported $($results.Count) report entries to $OutputPath"
