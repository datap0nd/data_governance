# MX Analytics - Setup & Update (run as Administrator)
#
# This single script handles everything:
#   - First run: downloads code, installs deps, creates the service
#   - Later runs: downloads latest code, updates deps, reconfigures and restarts the service
#
# Prerequisites: Python 3.11+ installed and on PATH.
# After running, the app is at http://localhost:8000.

$ErrorActionPreference = "Stop"

$ServiceName = "MXAnalytics"
$ProjectDir  = Split-Path $PSScriptRoot                # one level up from this script
$CodeDir     = $PSScriptRoot                           # this script lives in the code folder
$CodeDirName = Split-Path $CodeDir -Leaf               # folder name (e.g. data_governance-main)
$DbPath      = "$ProjectDir\governance.db"
$ReportsPath = "\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals"
$NssmExe     = "$CodeDir\tools\nssm.exe"
$Port        = 8000
$Downloads   = "$env:USERPROFILE\Downloads"
$ZipUrl      = "https://github.com/datap0nd/data_governance/archive/refs/heads/main.zip"
$ZipPath     = "$Downloads\data_governance-main.zip"

Write-Host ""
Write-Host "MX Analytics Setup" -ForegroundColor Cyan
Write-Host "==================" -ForegroundColor Cyan
Write-Host "  Code:     $CodeDir" -ForegroundColor DarkGray
Write-Host "  Database: $DbPath" -ForegroundColor DarkGray
Write-Host "  Reports:  $ReportsPath" -ForegroundColor DarkGray
Write-Host ""

# --- Find Python ---
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    $PythonExe = (Get-Command python3 -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    Write-Host "ERROR: Python not found on PATH. Install Python 3.11+ first." -ForegroundColor Red
    exit 1
}

# --- Stop existing service ---
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Stopping $ServiceName service..." -ForegroundColor Yellow
    $ErrorActionPreference = "Continue"
    & $NssmExe stop $ServiceName 2>&1 | Out-Null
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
    $ErrorActionPreference = "Stop"
    Write-Host "Stopped and removed old service." -ForegroundColor Green
}

# --- Download latest code ---
Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
Write-Host "Downloading latest version..." -ForegroundColor Yellow
Start-Process "msedge" $ZipUrl

$timeout = 300
$elapsed = 0
while ($true) {
    Start-Sleep -Seconds 3
    $elapsed += 3

    if (Test-Path $ZipPath) {
        $partial = "$ZipPath.crdownload"
        if (-not (Test-Path $partial)) {
            Start-Sleep -Seconds 2
            break
        }
    }

    if ($elapsed -ge $timeout) {
        Write-Host "Timed out after 5 minutes. Download the ZIP manually to $Downloads and re-run." -ForegroundColor Red
        exit 1
    }

    if ($elapsed % 15 -eq 0) {
        Write-Host "  Waiting for download... ($elapsed seconds)" -ForegroundColor DarkGray
    }
}
Write-Host "Download complete." -ForegroundColor Green

# --- Replace code folder ---
if (Test-Path $CodeDir) {
    Write-Host "Replacing code folder..." -ForegroundColor Yellow
    Remove-Item $CodeDir -Recurse -Force
}

Expand-Archive -Path $ZipPath -DestinationPath $ProjectDir -Force

# GitHub ZIPs extract as data_governance-main by default; rename if needed
$Extracted = Get-ChildItem $ProjectDir -Directory |
    Where-Object { $_.Name -like "data_governance*" -and $_.Name -ne $CodeDirName -and $_.Name -ne "logs" } |
    Select-Object -First 1
if ($Extracted) {
    Rename-Item $Extracted.FullName $CodeDirName
}

Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue

# Update paths after code folder was replaced
$NssmExe = "$CodeDir\tools\nssm.exe"

# --- Verify NSSM ---
if (-not (Test-Path $NssmExe)) {
    Write-Host "ERROR: NSSM not found at $NssmExe" -ForegroundColor Red
    exit 1
}

# --- Install dependencies ---
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Set-Location $CodeDir
pip install -r requirements.txt -q --index-url "https://bart.sec.samsung.net/artifactory/api/pypi/pypi-remote/simple" --trusted-host bart.sec.samsung.net

# --- Create service ---
Write-Host "Installing $ServiceName service..." -ForegroundColor Yellow

& $NssmExe install $ServiceName $PythonExe "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
& $NssmExe set $ServiceName AppDirectory $CodeDir
& $NssmExe set $ServiceName DisplayName "MX Analytics - Data Governance"
& $NssmExe set $ServiceName Description "BI data governance panel - freshness monitoring, lineage mapping, TMDL checker"
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

& $NssmExe set $ServiceName AppEnvironmentExtra `
    "DG_DB_PATH=$DbPath" `
    "DG_REPORTS_PATH=$ReportsPath" `
    "DG_SIMULATE_FRESHNESS=false" `
    "DG_AI_MOCK=true"

& $NssmExe set $ServiceName AppExit Default Restart
& $NssmExe set $ServiceName AppRestartDelay 5000

$LogDir = "$ProjectDir\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
& $NssmExe set $ServiceName AppStdout "$LogDir\mx_analytics.log"
& $NssmExe set $ServiceName AppStderr "$LogDir\mx_analytics_error.log"
& $NssmExe set $ServiceName AppStdoutCreationDisposition 4
& $NssmExe set $ServiceName AppStderrCreationDisposition 4
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateSeconds 86400
& $NssmExe set $ServiceName AppRotateBytes 10485760

# --- Start ---
Write-Host "Starting $ServiceName..." -ForegroundColor Yellow
& $NssmExe start $ServiceName

Start-Sleep -Seconds 3
$svc = Get-Service -Name $ServiceName
if ($svc.Status -eq "Running") {
    Write-Host "" -ForegroundColor Green
    Write-Host "Done. MX Analytics is running." -ForegroundColor Green
    Write-Host "" -ForegroundColor Green
    Write-Host "  URL:       http://localhost:$Port" -ForegroundColor Cyan
    Write-Host "  DB:        $DbPath" -ForegroundColor Cyan
    Write-Host "  Reports:   $ReportsPath" -ForegroundColor Cyan
    Write-Host "  Logs:      $LogDir\" -ForegroundColor Cyan
    Write-Host "" -ForegroundColor Green
} else {
    Write-Host "WARNING: Service not running. Check logs at $LogDir\" -ForegroundColor Red
}
