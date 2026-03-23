# MX Analytics - Update script
# Downloads latest code from GitHub and restarts the Windows service.
#
# Run from the current code folder. It replaces the code folder with
# the latest version. The database is not affected (it lives one level up).

$ErrorActionPreference = "Stop"

$ServiceName = "MXAnalytics"
$CodeDir     = $PSScriptRoot
$ProjectDir  = Split-Path $CodeDir
$NssmExe     = "$CodeDir\tools\nssm.exe"
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

# GitHub ZIPs extract as data_governance-main by default
$Extracted = Get-ChildItem $ProjectDir -Directory |
    Where-Object { $_.Name -like "data_governance*" -and $_.Name -ne (Split-Path $CodeDir -Leaf) -and $_.Name -ne "logs" } |
    Select-Object -First 1
if ($Extracted) {
    Rename-Item $Extracted.FullName (Split-Path $CodeDir -Leaf)
}

Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue

# --- Install/update dependencies ---
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Set-Location $CodeDir
pip install -r requirements.txt -q --index-url "https://bart.sec.samsung.net/artifactory/api/pypi/pypi-remote/simple" --trusted-host bart.sec.samsung.net

# --- Restart service ---
Write-Host "Starting $ServiceName service..." -ForegroundColor Yellow
$NssmExe = "$CodeDir\tools\nssm.exe"
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
}
