param([switch]$Headed)

$ErrorActionPreference = "Stop"
$CodeDir = Split-Path $PSScriptRoot
$ProjectDir = Split-Path $CodeDir
$Python = Join-Path $ProjectDir "python313\python.exe"
$LogDir = Join-Path $ProjectDir "logs"
$WorkerLog = Join-Path $LogDir "flow_worker.log"

if (-not (Test-Path $Python)) {
    throw "Metronome Python was not found at $Python"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Start-Transcript -Path $WorkerLog -Append | Out-Null
Set-Location $CodeDir
$arguments = @(
    (Join-Path $CodeDir "app\flow_worker.py"),
    "--server", "http://127.0.0.1:8000",
    "--worker-id", "bi-desktop",
    "--name", "BI desktop"
)
if ($Headed) {
    $arguments += "--headed"
}

while ($true) {
    & $Python @arguments
    Write-Host "Flow worker stopped. Retrying in 10 seconds." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}
