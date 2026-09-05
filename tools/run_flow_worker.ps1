param(
    [switch]$Headed,
    [string]$WorkerId = "bi-desktop-headless",
    [string]$WorkerName = "BI desktop - headless",
    [string]$ProfileDir = "",
    [int]$IdleExitSeconds = 0,
    [ValidateRange(1,5)][int]$Slot = 1
)

$ErrorActionPreference = "Stop"
$CodeDir = Split-Path $PSScriptRoot
$ProjectDir = Split-Path $CodeDir
$Python = Join-Path $ProjectDir "python313\python.exe"
$LogDir = Join-Path $ProjectDir "logs"
. (Join-Path $PSScriptRoot 'flow_pool.ps1')
$SlotConfig = Get-MetronomeFlowSlot -Slot $Slot -BaseProfile (Join-Path $env:USERPROFILE '.metronome-flow-browser')
if ($Headed -and $Slot -ne 1) { throw 'There is only one headed slot.' }
if (-not $PSBoundParameters.ContainsKey('WorkerId')) {
    $WorkerId = if ($Headed) { 'bi-desktop-headed' } else { $SlotConfig.WorkerId }
}
if (-not $PSBoundParameters.ContainsKey('WorkerName')) { $WorkerName = $WorkerId }
if (-not $ProfileDir) {
    $ProfileDir = if ($Headed) { Join-Path $env:USERPROFILE '.metronome-flow-browser-headed' } else { $SlotConfig.Profile }
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
