# MX Analytics - Setup & Update
# Right-click > Run with PowerShell (as Administrator)
#
# Does everything: downloads latest code, installs deps, sets up service.
# Run again any time to update.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ServiceName = "MXAnalytics"
$CodeDir     = $PSScriptRoot
$ProjectDir  = Split-Path $CodeDir
$DbPath      = "$ProjectDir\governance.db"
$ReportsPath = "\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals"
$Port        = 8000
$ZipUrl      = "https://github.com/datap0nd/data_governance/archive/refs/heads/main.zip"
$ZipPath     = "$ProjectDir\_update.zip"

# --- Safety check ---
if (-not (Test-Path "$CodeDir\app\main.py")) {
    Write-Host "ERROR: Run this from inside the data_governance-main folder." -ForegroundColor Red
    pause
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
    pause
    exit 1
}
Write-Host "  Python: $PythonExe" -ForegroundColor DarkGray

# --- Stop existing service ---
$NssmExe = "$CodeDir\tools\nssm.exe"
$ErrorActionPreference = "Continue"
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[1/5] Stopping service..." -ForegroundColor Yellow
    & $NssmExe stop $ServiceName 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
} else {
    Write-Host "[1/5] No existing service." -ForegroundColor DarkGray
}
$ErrorActionPreference = "Stop"

# --- Download latest code ---
Write-Host "[2/5] Downloading latest version..." -ForegroundColor Yellow
Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue

try {
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host "  Downloaded via PowerShell." -ForegroundColor Green
} catch {
    Write-Host "  Direct download failed: $_" -ForegroundColor Yellow
    Write-Host "  Trying via Chrome..." -ForegroundColor Yellow

    # Fall back to browser download
    $BrowserZip = "$env:USERPROFILE\Downloads\data_governance-main.zip"
    Remove-Item $BrowserZip -Force -ErrorAction SilentlyContinue

    $chrome = (Get-Command chrome -ErrorAction SilentlyContinue).Source
    if (-not $chrome) {
        $chrome = "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
    }
    if (-not $chrome -or -not (Test-Path $chrome)) {
        $chrome = "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
    }
    if (-not (Test-Path $chrome)) {
        Write-Host "  Chrome not found. Download manually:" -ForegroundColor Red
        Write-Host "  $ZipUrl" -ForegroundColor White
        Write-Host "  Save to: $BrowserZip" -ForegroundColor White
        Write-Host "  Then re-run this script." -ForegroundColor Red
        pause
        exit 1
    }

    Start-Process $chrome $ZipUrl
    $timeout = 300
    $elapsed = 0
    while ($true) {
        Start-Sleep -Seconds 3
        $elapsed += 3
        if ((Test-Path $BrowserZip) -and -not (Test-Path "$BrowserZip.crdownload")) {
            Start-Sleep -Seconds 1
            break
        }
        if ($elapsed -ge $timeout) {
            Write-Host "  Timed out waiting for download." -ForegroundColor Red
            pause
            exit 1
        }
        if ($elapsed % 15 -eq 0) {
            Write-Host "  Waiting for download... ($elapsed s)" -ForegroundColor DarkGray
        }
    }
    # Move browser download to project dir
    Move-Item $BrowserZip $ZipPath -Force
    Write-Host "  Downloaded via Chrome." -ForegroundColor Green
}

# --- Replace code ---
Write-Host "[3/5] Replacing code..." -ForegroundColor Yellow
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
Write-Host "[4/5] Installing dependencies..." -ForegroundColor Yellow
Set-Location $CodeDir
pip install -r requirements.txt -q --index-url "https://bart.sec.samsung.net/artifactory/api/pypi/pypi-remote/simple" --trusted-host bart.sec.samsung.net

# --- Create and start service ---
Write-Host "[5/5] Creating service..." -ForegroundColor Yellow
$NssmExe = "$CodeDir\tools\nssm.exe"

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

& $NssmExe start $ServiceName
Start-Sleep -Seconds 3

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host ""
    Write-Host "Done. MX Analytics running at http://localhost:$Port" -ForegroundColor Green
    Start-Process "http://localhost:$Port"
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "WARNING: Service not running. Check $LogDir\" -ForegroundColor Red
    Write-Host ""
}
pause
