param(
    [switch]$Headed,
    [string]$WorkerId = "bi-desktop-headless",
    [string]$WorkerName = "BI desktop - headless",
    [string]$ProfileDir = "",
    [int]$IdleExitSeconds = 0
)

$ErrorActionPreference = "Stop"
$CodeDir = Split-Path $PSScriptRoot
$ProjectDir = Split-Path $CodeDir
$Python = Join-Path $ProjectDir "python313\python.exe"
$LogDir = Join-Path $ProjectDir "logs"
if (-not $ProfileDir) {
    $ProfileDir = Join-Path $env:USERPROFILE ".metronome-flow-browser"
}
$WorkerLog = Join-Path $LogDir ("flow_worker_{0}.log" -f $WorkerId)

if (-not (Test-Path $Python)) {
    throw "Metronome Python was not found at $Python"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Start-Transcript -Path $WorkerLog -Append | Out-Null
Set-Location $CodeDir
$arguments = @(
    (Join-Path $CodeDir "app\flow_worker.py"),
    "--server", "http://127.0.0.1:8000",
    "--worker-id", $WorkerId,
    "--name", $WorkerName,
    "--profile-dir", $ProfileDir
)
if ($Headed) {
    $arguments += "--headed"
}
if ($IdleExitSeconds -gt 0) {
    $arguments += @("--idle-exit-seconds", $IdleExitSeconds)
}

while ($true) {
    & $Python @arguments
    if ($IdleExitSeconds -gt 0) {
        exit $LASTEXITCODE
    }
    Write-Host "Flow worker stopped. Retrying in 10 seconds." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
