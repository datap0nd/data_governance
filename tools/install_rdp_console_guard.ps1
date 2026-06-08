<#
.SYNOPSIS
    Install scheduled tasks that keep the sync account's RDP session repairable.
#>
param(
    [string]$TargetUser = $env:USERNAME,
    [string]$TaskName = "DG_RDP_Console_Guard",
    [string]$DisconnectTaskName = "DG_RDP_Console_Guard_OnDisconnect"
)

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "Run this script from an elevated PowerShell window." -ForegroundColor Red
    exit 1
}

if (-not $TargetUser) {
    Write-Host "No target user provided." -ForegroundColor Red
    exit 2
}

$guardScript = Join-Path $PSScriptRoot "rdp_console_guard.ps1"
if (-not (Test-Path $guardScript)) {
    Write-Host "Guard script not found: $guardScript" -ForegroundColor Red
    exit 3
}

try {
    wevtutil sl "Microsoft-Windows-TerminalServices-LocalSessionManager/Operational" /e:true 2>$null
} catch {
    Write-Host "Could not enable Terminal Services operational log: $_" -ForegroundColor Yellow
}

$action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$guardScript`" -TargetUser `"$TargetUser`""

Write-Host "Installing periodic RDP console guard for user '$TargetUser'..." -ForegroundColor Cyan
$periodicOutput = & schtasks.exe /Create /TN $TaskName /TR $action /SC MINUTE /MO 5 /RU SYSTEM /RL HIGHEST /F 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host $periodicOutput -ForegroundColor Red
    throw "Could not install periodic RDP console guard task."
}

Write-Host "Installing RDP disconnect event guard..." -ForegroundColor Cyan
$eventLog = "Microsoft-Windows-TerminalServices-LocalSessionManager/Operational"
$eventQuery = "*[System[(EventID=24)]]"
try {
    $eventOutput = & schtasks.exe /Create /TN $DisconnectTaskName /TR $action /SC ONEVENT /EC $eventLog /MO $eventQuery /RU SYSTEM /RL HIGHEST /F 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw $eventOutput
    }
} catch {
    Write-Host "Could not install event-triggered guard. The periodic guard is still installed: $_" -ForegroundColor Yellow
}

Write-Host "Running guard once..." -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $guardScript -TargetUser $TargetUser

Write-Host "RDP console guard installed." -ForegroundColor Green
