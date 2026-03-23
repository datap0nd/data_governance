# MX Analytics - One-time service installation
# Run this once as Administrator to set up the Windows service.
#
# Prerequisites:
#   - Python 3.11+ installed and on PATH
#   - pip install -r requirements.txt already done
#
# What this does:
#   1. Downloads NSSM (Non-Sucking Service Manager) if not present
#   2. Creates the MXAnalytics Windows service
#   3. Configures environment variables (DB path, reports path)
#   4. Starts the service
#
# After install, the app runs at http://localhost:8000 automatically on boot.

$ErrorActionPreference = "Stop"

$ServiceName = "MXAnalytics"
$ProjectDir  = "C:\Users\r.cunha\documents\Home\projects\data_governance"
$CodeDir     = "$ProjectDir\data_governance-main"
$DbPath      = "$ProjectDir\governance.db"
$ReportsPath = "Z:\METOMX\Desktop\BI Report Originals"
$NssmDir     = "$ProjectDir\nssm"
$NssmExe     = "$NssmDir\nssm.exe"
$Port        = 8000

# --- Find Python ---
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    $PythonExe = (Get-Command python3 -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    Write-Host "ERROR: Python not found on PATH. Install Python 3.11+ first." -ForegroundColor Red
    exit 1
}
Write-Host "Using Python: $PythonExe" -ForegroundColor Cyan

# --- Download NSSM if missing ---
if (-not (Test-Path $NssmExe)) {
    Write-Host "Downloading NSSM..." -ForegroundColor Yellow
    $NssmZip = "$env:TEMP\nssm.zip"
    $NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
    Invoke-WebRequest -Uri $NssmUrl -OutFile $NssmZip
    Expand-Archive -Path $NssmZip -DestinationPath "$env:TEMP\nssm_extract" -Force

    # NSSM zip contains nssm-2.24/win64/nssm.exe
    New-Item -ItemType Directory -Path $NssmDir -Force | Out-Null
    Copy-Item "$env:TEMP\nssm_extract\nssm-2.24\win64\nssm.exe" $NssmExe -Force
    Remove-Item $NssmZip -Force -ErrorAction SilentlyContinue
    Remove-Item "$env:TEMP\nssm_extract" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "NSSM installed at $NssmExe" -ForegroundColor Green
}

# --- Remove existing service if present ---
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing $ServiceName service..." -ForegroundColor Yellow
    & $NssmExe stop $ServiceName 2>$null
    & $NssmExe remove $ServiceName confirm
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

# Restart on failure (wait 5 seconds, retry up to 3 times)
& $NssmExe set $ServiceName AppExit Default Restart
& $NssmExe set $ServiceName AppRestartDelay 5000

# Logging - write stdout/stderr to log files
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
