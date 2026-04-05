# MX Analytics - Setup & Update
# Right-click > Run with PowerShell
#
# Does everything: downloads latest code, installs deps, sets up service.
# Run again any time to update. Auto-elevates to Admin if needed.
#
# This script NEVER deletes files. It extracts new code over the existing
# folder (overwriting updated files). Clean up old files yourself if needed.
#
# Uses a portable Python 3.13 (no system changes) so pbixray works.

# --- Self-elevate to Admin if needed ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ServiceName = "MXAnalytics"
$CodeDir     = $PSScriptRoot
$ProjectDir  = Split-Path $CodeDir
$DbPath      = "$ProjectDir\governance.db"
$ReportsPath = "\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals"
$ScriptsPath = "\\MX-SHARE\Users\METOMX\Desktop"
$Port        = 8000
$ZipUrl      = "https://github.com/datap0nd/data_governance/archive/refs/heads/main.zip"
$ZipPath     = "$ProjectDir\_update.zip"
$PyDir       = "$ProjectDir\python313"
$PyExe       = "$PyDir\python.exe"
$PyZipUrl    = "https://www.python.org/ftp/python/3.13.2/python-3.13.2-embed-amd64.zip"

# --- Safety check ---
if (-not (Test-Path "$CodeDir\app\main.py")) {
    Write-Host "ERROR: Run this from inside the data_governance-main folder." -ForegroundColor Red
    pause
    exit 1
}

Write-Host ""
Write-Host "MX Analytics Setup" -ForegroundColor Cyan
Write-Host "==================" -ForegroundColor Cyan
Write-Host "  Code dir:  $CodeDir" -ForegroundColor DarkGray
Write-Host "  Database:  $DbPath" -ForegroundColor DarkGray
if (Test-Path $DbPath) {
    $dbSize = [math]::Round((Get-Item $DbPath).Length / 1024)
    Write-Host "  DB exists: ${dbSize} KB (will be preserved)" -ForegroundColor Green
} else {
    Write-Host "  DB: new (will be created on first run)" -ForegroundColor Yellow
}

# --- Portable Python 3.13 ---
if (-not (Test-Path $PyExe)) {
    Write-Host "[1/5] Downloading portable Python 3.13..." -ForegroundColor Yellow
    $PyZipPath = "$ProjectDir\_python.zip"
    try {
        Invoke-WebRequest -Uri $PyZipUrl -OutFile $PyZipPath -UseBasicParsing
    } catch {
        Write-Host "  Direct download failed, trying Edge..." -ForegroundColor Yellow
        Start-Process "msedge" $PyZipUrl
        $timeout = 120
        $elapsed = 0
        $BrowserPyZip = "$env:USERPROFILE\Downloads\python-3.13.2-embed-amd64.zip"
        while ($true) {
            Start-Sleep -Seconds 3
            $elapsed += 3
            if ((Test-Path $BrowserPyZip) -and -not (Test-Path "$BrowserPyZip.partial")) {
                Start-Sleep -Seconds 1
                Move-Item $BrowserPyZip $PyZipPath -Force
                break
            }
            if ($elapsed -ge $timeout) {
                Write-Host "  Timed out. Download Python manually from:" -ForegroundColor Red
                Write-Host "  $PyZipUrl" -ForegroundColor White
                Write-Host "  Extract to: $PyDir" -ForegroundColor White
                pause
                exit 1
            }
        }
    }
    New-Item -ItemType Directory -Path $PyDir -Force | Out-Null
    Expand-Archive -Path $PyZipPath -DestinationPath $PyDir -Force

    # Enable pip: uncomment "import site" in python313._pth
    $pthFile = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
    if ($pthFile) {
        $content = Get-Content $pthFile.FullName
        $content = $content -replace '^#\s*import site', 'import site'
        Set-Content $pthFile.FullName $content
    }

    # Bootstrap pip
    Write-Host "  Installing pip..." -ForegroundColor DarkGray
    $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
    $getPipPath = "$PyDir\get-pip.py"
    try {
        Invoke-WebRequest -Uri $getPipUrl -OutFile $getPipPath -UseBasicParsing
    } catch {
        Write-Host "  Could not download get-pip.py" -ForegroundColor Red
        pause
        exit 1
    }
    & $PyExe $getPipPath --no-warn-script-location -q
    Write-Host "  Portable Python 3.13 ready." -ForegroundColor Green
} else {
    Write-Host "[1/5] Portable Python 3.13 already installed." -ForegroundColor DarkGray
}
Write-Host "  Python:   $PyExe" -ForegroundColor DarkGray

# --- Stop existing service and free the port ---
$NssmExe = "$CodeDir\tools\nssm.exe"
$ErrorActionPreference = "Continue"
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[2/5] Stopping service..." -ForegroundColor Yellow
    & $NssmExe stop $ServiceName 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
} else {
    Write-Host "[2/5] No existing service." -ForegroundColor DarkGray
}

# Kill anything still holding the port
$portPid = (netstat -ano | Select-String ":$Port\s" | ForEach-Object {
    ($_ -split '\s+')[-1]
} | Where-Object { $_ -match '^\d+$' } | Select-Object -Unique)
foreach ($p in $portPid) {
    if ($p -and $p -ne "0") {
        Write-Host "  Killing PID $p holding port $Port" -ForegroundColor Yellow
        taskkill /PID $p /F 2>&1 | Out-Null
    }
}
$ErrorActionPreference = "Stop"

# --- Download latest code ---
Write-Host "[3/5] Downloading latest version..." -ForegroundColor Yellow

try {
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host "  Downloaded via PowerShell." -ForegroundColor Green
} catch {
    Write-Host "  Direct download failed: $_" -ForegroundColor Yellow
    Write-Host "  Trying via Edge..." -ForegroundColor Yellow

    $BrowserZip = "$env:USERPROFILE\Downloads\data_governance-main.zip"

    Start-Process "msedge" $ZipUrl
    $timeout = 300
    $elapsed = 0
    while ($true) {
        Start-Sleep -Seconds 3
        $elapsed += 3
        if ((Test-Path $BrowserZip) -and -not (Test-Path "$BrowserZip.partial")) {
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
    Move-Item $BrowserZip $ZipPath -Force
    Write-Host "  Downloaded via Edge." -ForegroundColor Green
}

# --- Extract new code over existing folder (no deletion) ---
Write-Host "[4/5] Extracting update over existing code..." -ForegroundColor Yellow

# Extract to a temp folder first, then copy contents over
$TempExtract = "$ProjectDir\_extract_temp"
Expand-Archive -Path $ZipPath -DestinationPath $TempExtract -Force

# GitHub ZIP has a top-level folder (data_governance-main/) - copy its contents into $CodeDir
$Inner = Get-ChildItem $TempExtract -Directory | Select-Object -First 1
if ($Inner) {
    Copy-Item "$($Inner.FullName)\*" $CodeDir -Recurse -Force
    Remove-Item $TempExtract -Recurse -Force
}
Write-Host "  Files updated in: $CodeDir" -ForegroundColor Green

# --- Stamp VERSION with download timestamp ---
$ver = (Get-Date -Format "yyyyMMdd-HHmmss")
Set-Content "$CodeDir\VERSION" $ver
Write-Host "  Version: $ver" -ForegroundColor DarkGray

# --- Install dependencies ---
Write-Host "[5/5] Installing dependencies..." -ForegroundColor Yellow
Set-Location $CodeDir
$PipExe = "$PyDir\Scripts\pip.exe"
# Install bundled wheels first (pbixray + xpress9 + kaitaistruct, no network)
& $PipExe install --no-index --find-links vendor pbixray xpress9 kaitaistruct -q
# Install remaining deps from public PyPI (portable Python has clean config)
& $PipExe install -r requirements.txt -q

# --- Create and start service ---
Write-Host "Starting service..." -ForegroundColor Yellow
$NssmExe = "$CodeDir\tools\nssm.exe"

& $NssmExe install $ServiceName $PyExe "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
& $NssmExe set $ServiceName AppDirectory $CodeDir
& $NssmExe set $ServiceName DisplayName "MX Analytics - Data Governance"
& $NssmExe set $ServiceName Description "BI data governance panel"
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

& $NssmExe set $ServiceName AppEnvironmentExtra `
    "DG_DB_PATH=$DbPath" `
    "DG_REPORTS_PATH=$ReportsPath" `
    "DG_SCRIPTS_PATH=$ScriptsPath" `
    "DG_SIMULATE_FRESHNESS=false" `
    "DG_AI_MOCK=true"

# Run service as current user (needed for network share access)
$cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Enter your Windows password so the service can access network shares"
& $NssmExe set $ServiceName ObjectName "$env:USERDOMAIN\$env:USERNAME" $cred.GetNetworkCredential().Password

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
