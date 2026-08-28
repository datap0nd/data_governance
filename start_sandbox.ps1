# Metronome - Start Offline Sandbox
# Right-click this file > "Run with PowerShell".
#
# Runs a completely offline copy of Metronome with fake test data, so you
# can click around and test UI changes without touching the real app or any
# real data. The first run builds the data (takes a minute or two); later
# runs start immediately. Everything lives in the "local_sandbox" folder -
# delete that folder to remove every trace.
#
# The sandbox uses port 8001, so the real Metronome service on port 8000
# keeps running untouched. Close this window (or press Ctrl+C) to stop the
# sandbox app.

$ErrorActionPreference = "Stop"

trap {
    Write-Host ""
    Write-Host "SANDBOX FAILED: $_" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    pause
    exit 1
}

# --- Find the app code folder (this script sits inside it) ---
if (-not ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "app\main.py")))) {
    throw "Run this script from the app's code folder (the one that contains the 'app' subfolder)."
}
$CodeDir = $PSScriptRoot
$ProjectDir = Split-Path $CodeDir

# --- Find Python: the app's own embedded copy first, then PATH ---
$PyExe = Join-Path $ProjectDir "python313\python.exe"
if (-not (Test-Path $PyExe)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "Python not found. Run the normal update/setup script first." }
    $PyExe = $cmd.Source
}
Write-Host "Using Python: $PyExe" -ForegroundColor DarkGray

Set-Location $CodeDir
$Port = 8001

# --- Build the sandbox data on first run ---
if (-not (Test-Path (Join-Path $CodeDir "local_sandbox\sandbox_config.json"))) {
    Write-Host "Building the sandbox test data (first run only, takes a minute)..." -ForegroundColor Yellow
    & $PyExe tools\seed_sandbox.py
    if ($LASTEXITCODE -ne 0) { throw "Building the sandbox data failed with exit code $LASTEXITCODE" }
}

# --- Start the app and open the browser on it ---
Write-Host ""
Write-Host "Starting the sandbox app on http://localhost:$Port ..." -ForegroundColor Green
Write-Host "Close this window (or press Ctrl+C) to stop it." -ForegroundColor Green
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 4
    Start-Process "http://localhost:$using:Port"
} | Out-Null
& $PyExe tools\run_sandbox.py --port $Port
