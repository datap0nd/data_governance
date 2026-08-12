param([switch]$Headed)

$ErrorActionPreference = "Stop"
$CodeDir = Split-Path $PSScriptRoot
$ProjectDir = Split-Path $CodeDir
$Python = Join-Path $ProjectDir "python313\python.exe"

if (-not (Test-Path $Python)) {
    throw "Metronome Python was not found at $Python"
}

Set-Location $CodeDir
$env:PYTHONPATH = $CodeDir
$arguments = @(
    "-m", "app.flow_worker",
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
