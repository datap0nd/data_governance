$ProjectDir = "C:\Users\r.cunha\documents\Home\projects\data_governance"
$Downloads = "$env:USERPROFILE\Downloads"
$ZipUrl = "https://github.com/datap0nd/data_governance/archive/refs/heads/main.zip"
$ZipPath = "$Downloads\data_governance-main.zip"

# Download latest version from GitHub
Write-Host "Downloading latest version..." -ForegroundColor Cyan
try {
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host "Download complete." -ForegroundColor Green
} catch {
    Write-Host "Download failed: $_" -ForegroundColor Red
    exit 1
}

# Remove old folder (but keep governance.db so scan history is preserved)
$OldFolder = "$ProjectDir\data_governance-main"
$DbBackup = $null
if (Test-Path "$OldFolder\governance.db") {
    Write-Host "Backing up database..." -ForegroundColor Yellow
    Copy-Item "$OldFolder\governance.db" "$ProjectDir\governance.db.bak" -Force
    $DbBackup = "$ProjectDir\governance.db.bak"
}

if (Test-Path $OldFolder) {
    Write-Host "Removing old data_governance-main..." -ForegroundColor Yellow
    Remove-Item $OldFolder -Recurse -Force
}

# Extract ZIP
Write-Host "Extracting..." -ForegroundColor Yellow
Expand-Archive -Path $ZipPath -DestinationPath $ProjectDir -Force

# GitHub ZIPs extract as data_governance-main by default
# But if it extracted with a different name, find and rename it
$Extracted = Get-ChildItem $ProjectDir -Directory | Where-Object { $_.Name -like "data_governance*" -and $_.Name -ne "data_governance-main" -and $_.Name -ne "reports" -and $_.Name -ne "BI Report Originals" } | Select-Object -First 1

if ($Extracted) {
    Write-Host "Renaming $($Extracted.Name) to data_governance-main..." -ForegroundColor Yellow
    Rename-Item $Extracted.FullName "data_governance-main"
}

# Restore database backup
if ($DbBackup -and (Test-Path $DbBackup)) {
    Write-Host "Restoring database..." -ForegroundColor Yellow
    Copy-Item $DbBackup "$OldFolder\governance.db" -Force
    Remove-Item $DbBackup -Force
}

# Clean up downloaded ZIP
Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue

# Install dependencies
$PipIndex = "https://pypi.org/simple/"

Write-Host "Installing dependencies..." -ForegroundColor Yellow
Set-Location "$ProjectDir\data_governance-main"
pip install -r requirements.txt --index-url $PipIndex -q

Write-Host ""
Write-Host "Starting the app..." -ForegroundColor Green
Write-Host "Scanning .pbix files from: $ProjectDir\BI Report Originals" -ForegroundColor Cyan
Write-Host "Open http://localhost:8000 in your browser" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop" -ForegroundColor Cyan
Write-Host ""

$env:DG_REPORTS_PATH = "$ProjectDir\BI Report Originals"
Start-Process "chrome" "http://localhost:8000"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
