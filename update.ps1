# MX Analytics - Update script
# Downloads latest code from GitHub and restarts the Windows service.
#
# Prerequisites:
#   - install_service.ps1 has been run once (service exists)
#   - Internet access to download from GitHub
#
# The database lives at $ProjectDir\governance.db (outside the code folder),
# so replacing the code folder does not affect scan history or data.

$ErrorActionPreference = "Stop"

$ServiceName = "MXAnalytics"
$ProjectDir  = "$env:USERPROFILE\documents\Home\projects\data_governance"
$CodeDir     = "$ProjectDir\data_governance-main"
$NssmExe     = "$ProjectDir\nssm\nssm.exe"
$Downloads   = "$env:USERPROFILE\Downloads"
$ZipUrl      = "https://github.com/datap0nd/data_governance/archive/refs/heads/main.zip"
$ZipPath     = "$Downloads\data_governance-main.zip"

# --- Stop service ---
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host "Stopping $ServiceName service..." -ForegroundColor Yellow
    & $NssmExe stop $ServiceName
    Start-Sleep -Seconds 3
}

# --- Download latest code ---
Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
Write-Host "Opening download link in Chrome..." -ForegroundColor Cyan
Start-Process "chrome" $ZipUrl

Write-Host "Waiting for download to complete..." -ForegroundColor Yellow
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
        Write-Host "Timed out after 5 minutes. Download manually to $Downloads and re-run." -ForegroundColor Red
        exit 1
    }

    if ($elapsed % 15 -eq 0) {
        Write-Host "  Still waiting... ($elapsed seconds)" -ForegroundColor DarkGray
    }
}
Write-Host "Download complete." -ForegroundColor Green

# --- Replace code folder ---
if (Test-Path $CodeDir) {
    Write-Host "Removing old code folder..." -ForegroundColor Yellow
    Remove-Item $CodeDir -Recurse -Force
}

Write-Host "Extracting..." -ForegroundColor Yellow
Expand-Archive -Path $ZipPath -DestinationPath $ProjectDir -Force

# GitHub ZIPs may extract with a different folder name
$Extracted = Get-ChildItem $ProjectDir -Directory |
    Where-Object { $_.Name -like "data_governance*" -and $_.Name -ne "data_governance-main" -and $_.Name -ne "nssm" -and $_.Name -ne "logs" } |
    Select-Object -First 1
if ($Extracted) {
    Rename-Item $Extracted.FullName "data_governance-main"
}

Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue

# --- Install/update dependencies ---
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Set-Location $CodeDir
pip install -r requirements.txt -q --index-url "https://bart.sec.samsung.net/artifactory/api/pypi/pypi-remote/simple" --trusted-host bart.sec.samsung.net

# --- Restart service ---
Write-Host "Starting $ServiceName service..." -ForegroundColor Yellow
& $NssmExe start $ServiceName

Start-Sleep -Seconds 3
$svc = Get-Service -Name $ServiceName
if ($svc.Status -eq "Running") {
    Write-Host "" -ForegroundColor Green
    Write-Host "Update complete. Service is running." -ForegroundColor Green
    Write-Host "  URL: http://localhost:8000" -ForegroundColor Cyan
    Write-Host "" -ForegroundColor Green
} else {
    Write-Host "WARNING: Service not running after update. Check logs:" -ForegroundColor Red
    Write-Host "  $ProjectDir\logs\" -ForegroundColor Yellow
    Write-Host "  Run: nssm status $ServiceName" -ForegroundColor Yellow
}
