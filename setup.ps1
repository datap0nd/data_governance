# MX Analytics - Setup & Update
# Right-click > Run with PowerShell (as Administrator)
#
# Does everything: downloads latest code, installs deps, sets up service.
# Run again any time to update.

$ErrorActionPreference = "Stop"

$ServiceName = "MXAnalytics"
$CodeDir     = $PSScriptRoot
$ProjectDir  = Split-Path $CodeDir
$DbPath      = "$ProjectDir\governance.db"
$ReportsPath = "\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals"
$Port        = 8000
$Downloads   = "$env:USERPROFILE\Downloads"
$ZipUrl      = "https://github.com/datap0nd/data_governance/archive/refs/heads/main.zip"
$ZipPath     = "$Downloads\data_governance-main.zip"

# --- Safety check ---
if (-not (Test-Path "$CodeDir\app\main.py")) {
    Write-Host "ERROR: Run this from inside the data_governance-main folder." -ForegroundColor Red
    exit 1
}

# Move out of the code folder so it can be deleted later
Set-Location $ProjectDir

Write-Host ""
Write-Host "MX Analytics Setup" -ForegroundColor Cyan
Write-Host "==================" -ForegroundColor Cyan

# --- Find Python ---
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    $PythonExe = (Get-Command python3 -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    Write-Host "ERROR: Python not found on PATH." -ForegroundColor Red
    exit 1
}

# --- Stop and remove existing service ---
$ErrorActionPreference = "Continue"
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Stopping service..." -ForegroundColor Yellow
    & "$CodeDir\tools\nssm.exe" stop $ServiceName 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    & "$CodeDir\tools\nssm.exe" remove $ServiceName confirm 2>&1 | Out-Null
}
$ErrorActionPreference = "Stop"

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
        if (-not (Test-Path "$ZipPath.crdownload")) {
            Start-Sleep -Seconds 2
            break
        }
    }

    if ($elapsed -ge $timeout) {
        Write-Host "Timed out. Download the ZIP manually to $Downloads and re-run." -ForegroundColor Red
        exit 1
    }

    if ($elapsed % 15 -eq 0) {
        Write-Host "  Waiting for download... ($elapsed seconds)" -ForegroundColor DarkGray
    }
}
Write-Host "Download complete." -ForegroundColor Green

# --- Delete old code folder and extract new one ---
Write-Host "Replacing code..." -ForegroundColor Yellow
Remove-Item $CodeDir -Recurse -Force
Expand-Archive -Path $ZipPath -DestinationPath $ProjectDir -Force

# GitHub ZIP extracts as data_governance-main/ - rename if needed
$CodeDirName = Split-Path $CodeDir -Leaf
$Extracted = Get-ChildItem $ProjectDir -Directory |
    Where-Object { $_.Name -like "data_governance*" -and $_.Name -ne $CodeDirName -and $_.Name -ne "logs" } |
    Select-Object -First 1
if ($Extracted) {
    Rename-Item $Extracted.FullName $CodeDirName
}

Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue

# --- Install dependencies ---
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Set-Location $CodeDir
pip install -r requirements.txt -q --index-url "https://bart.sec.samsung.net/artifactory/api/pypi/pypi-remote/simple" --trusted-host bart.sec.samsung.net

# --- Create service ---
$NssmExe = "$CodeDir\tools\nssm.exe"
Write-Host "Creating service..." -ForegroundColor Yellow

& $NssmExe install $ServiceName $PythonExe "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
& $NssmExe set $ServiceName AppDirectory $CodeDir
& $NssmExe set $ServiceName DisplayName "MX Analytics - Data Governance"
& $NssmExe set $ServiceName Description "BI data governance panel"
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
& $NssmExe start $ServiceName
Start-Sleep -Seconds 3
$svc = Get-Service -Name $ServiceName
if ($svc.Status -eq "Running") {
    Write-Host ""
    Write-Host "Done. MX Analytics is running at http://localhost:$Port" -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "WARNING: Service not running. Check $LogDir\" -ForegroundColor Red
}
