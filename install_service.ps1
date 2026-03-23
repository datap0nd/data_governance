# MX Analytics - One-time service installation
# Run this once as Administrator to set up the Windows service.
#
# Prerequisites:
#   - Python 3.11+ installed and on PATH
#   - pip install -r requirements.txt already done
#
# Extract the ZIP anywhere you like. This script figures out all paths
# automatically from wherever it is located.
#
# After install, the app runs at http://localhost:8000 automatically on boot.

$ErrorActionPreference = "Stop"

$ServiceName = "MXAnalytics"
$CodeDir     = $PSScriptRoot                          # wherever this script lives
$ProjectDir  = Split-Path $CodeDir                    # one level up
$DbPath      = "$ProjectDir\governance.db"
$ReportsPath = "\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals"
$NssmExe     = "$CodeDir\tools\nssm.exe"              # bundled in the repo
$Port        = 8000

Write-Host "Code folder:    $CodeDir" -ForegroundColor DarkGray
Write-Host "Project folder: $ProjectDir" -ForegroundColor DarkGray
Write-Host "Database:       $DbPath" -ForegroundColor DarkGray

# --- Find Python ---
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    $PythonExe = (Get-Command python3 -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    Write-Host "ERROR: Python not found on PATH. Install Python 3.11+ first." -ForegroundColor Red
    exit 1
}
Write-Host "Python:         $PythonExe" -ForegroundColor DarkGray

# --- Verify NSSM is bundled ---
if (-not (Test-Path $NssmExe)) {
    Write-Host "ERROR: NSSM not found at $NssmExe" -ForegroundColor Red
    Write-Host "The tools\nssm.exe file should be included in the download." -ForegroundColor Red
    exit 1
}

# --- Remove existing service if present ---
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing $ServiceName service..." -ForegroundColor Yellow
    $ErrorActionPreference = "Continue"
    & $NssmExe stop $ServiceName 2>&1 | Out-Null
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
    $ErrorActionPreference = "Stop"
    Write-Host "Removed." -ForegroundColor Green
}

# --- Install service ---
Write-Host "Installing $ServiceName service..." -ForegroundColor Yellow

& $NssmExe install $ServiceName $PythonExe "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
& $NssmExe set $ServiceName AppDirectory $CodeDir
& $NssmExe set $ServiceName DisplayName "MX Analytics - Data Governance"
& $NssmExe set $ServiceName Description "BI data governance panel - freshness monitoring, lineage mapping, TMDL checker"
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

# Environment variables - DB outside code dir, real reports path
& $NssmExe set $ServiceName AppEnvironmentExtra `
    "DG_DB_PATH=$DbPath" `
    "DG_REPORTS_PATH=$ReportsPath" `
    "DG_SIMULATE_FRESHNESS=false" `
    "DG_AI_MOCK=true"

# Restart on failure (wait 5 seconds)
& $NssmExe set $ServiceName AppExit Default Restart
& $NssmExe set $ServiceName AppRestartDelay 5000

# Logging
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
    Write-Host "Service installed and running." -ForegroundColor Green
    Write-Host "" -ForegroundColor Green
    Write-Host "  URL:       http://localhost:$Port" -ForegroundColor Cyan
    Write-Host "  DB:        $DbPath" -ForegroundColor Cyan
    Write-Host "  Reports:   $ReportsPath" -ForegroundColor Cyan
    Write-Host "  Logs:      $LogDir\" -ForegroundColor Cyan
    Write-Host "" -ForegroundColor Green
    Write-Host "  Manage:    nssm start/stop/restart $ServiceName" -ForegroundColor DarkGray
    Write-Host "  Uninstall: nssm remove $ServiceName confirm" -ForegroundColor DarkGray
} else {
    Write-Host "WARNING: Service installed but not running. Check logs at $LogDir\" -ForegroundColor Red
    Write-Host "Run: nssm status $ServiceName" -ForegroundColor Yellow
}
