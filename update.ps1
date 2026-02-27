$ProjectDir = "C:\Users\r.cunha\documents\Home\projects\data_governance"
$Downloads = "$env:USERPROFILE\Downloads"

# Find the latest data_governance ZIP in Downloads
$Zip = Get-ChildItem "$Downloads\data_governance*.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1

if (-not $Zip) {
    Write-Host "No data_governance ZIP found in Downloads folder." -ForegroundColor Red
    exit 1
}

Write-Host "Found: $($Zip.Name)" -ForegroundColor Cyan

# Remove old folder
$OldFolder = "$ProjectDir\data_governance-main"
if (Test-Path $OldFolder) {
    Write-Host "Removing old data_governance-main..." -ForegroundColor Yellow
    Remove-Item $OldFolder -Recurse -Force
}

# Extract ZIP
Write-Host "Extracting..." -ForegroundColor Yellow
Expand-Archive -Path $Zip.FullName -DestinationPath $ProjectDir -Force

# GitHub ZIPs extract as data_governance-main by default
# But if it extracted with a different name, find and rename it
$Extracted = Get-ChildItem $ProjectDir -Directory | Where-Object { $_.Name -like "data_governance*" -and $_.Name -ne "data_governance-main" -and $_.Name -ne "reports" } | Select-Object -First 1

if ($Extracted) {
    Write-Host "Renaming $($Extracted.Name) to data_governance-main..." -ForegroundColor Yellow
    Rename-Item $Extracted.FullName "data_governance-main"
}

# Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Set-Location "$ProjectDir\data_governance-main"
pip install -r requirements.txt -q

Write-Host ""
Write-Host "Starting the app..." -ForegroundColor Green
Write-Host "Open http://localhost:8000 in your browser" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop" -ForegroundColor Cyan
Write-Host ""

$env:DG_TMDL_ROOT = "$ProjectDir\data_governance-main\test_data"
Start-Process "chrome" "http://localhost:8000"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
