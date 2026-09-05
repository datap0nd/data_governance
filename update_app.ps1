# Metronome - Update App
# Right-click this file > "Run with PowerShell".
#
# Updates the app to the newest version in a way an office proxy cannot
# sabotage: it first asks GitHub what the newest version is, then makes the
# normal update script download exactly that version through a one-time web
# address the proxy has never seen, so a stale cached copy can never be
# installed. Then it runs the normal update script, which installs
# dependencies and restarts the app service.
#
# Safe to run from anywhere: your Downloads folder or the app folder itself.

$ErrorActionPreference = "Stop"
$Repo = "datap0nd/data_governance"

# --- Run as Administrator (the update itself needs it) ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

trap {
    Write-Host ""
    Write-Host "UPDATE FAILED: $_" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    pause
    exit 1
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Find the installed app folder ---
# 1) the folder this script sits in, 2) wherever the Windows service points.
$CodeDir = $null
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "app\main.py"))) {
    $CodeDir = $PSScriptRoot
} else {
    $svc = Get-CimInstance Win32_Service -Filter "Name='MXAnalytics'" -ErrorAction SilentlyContinue
    if ($svc -and $svc.PathName -match '"?([^"]*?nssm\.exe)') {
        $candidate = Split-Path (Split-Path $Matches[1])
        if (Test-Path (Join-Path $candidate "app\main.py")) { $CodeDir = $candidate }
    }
}
if (-not $CodeDir) {
    throw "Could not find the installed app on this machine. Copy this file into the app's code folder (the one that contains the 'app' subfolder) and run it again."
}
$ProjectDir = Split-Path $CodeDir
Write-Host "App folder: $CodeDir" -ForegroundColor DarkGray

# --- Ask GitHub for the newest version ---
$GitHubToken = $env:DG_GITHUB_TOKEN
$Headers = @{ "User-Agent" = "Metronome-Update"; "Accept" = "application/vnd.github+json"; "Cache-Control" = "no-cache" }
if ($GitHubToken) { $Headers["Authorization"] = "Bearer $GitHubToken" }
$LatestSha = $null
try {
    $LatestSha = (Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/commits/main?_metronome_check=$([Guid]::NewGuid().ToString('N'))" -Headers $Headers -TimeoutSec 20).sha
    if ($LatestSha -notmatch '^[0-9a-fA-F]{40}$') {
        throw "GitHub did not return a valid main commit."
    }
    Write-Host "Newest version on GitHub: $LatestSha" -ForegroundColor Green
} catch {
    throw "Could not confirm the newest version. Retry the update: $($_.Exception.Message)"
}

# --- Point the update at exactly that version (uncacheable address) ---
if ($LatestSha) {
    $env:DG_UPDATE_COMMIT_SHA = $LatestSha
    $env:DG_UPDATE_ZIP_URL = if ($GitHubToken) {
        "https://api.github.com/repos/$Repo/zipball/$LatestSha"
    } else {
        "https://github.com/$Repo/archive/$LatestSha.zip"
    }
}
# A leftover archive from an earlier failed run must never be reused.
Remove-Item (Join-Path $ProjectDir "_update.zip") -Force -ErrorAction SilentlyContinue

# --- Run the one authoritative setup script ---
# Never reconstruct it from a second template: that can revive an installer
# older than the code that was just downloaded.
$UpdateScript = Join-Path $CodeDir "setup.ps1"
if (-not (Test-Path $UpdateScript -PathType Leaf)) {
    throw "Could not find setup.ps1 in $CodeDir."
}
Write-Host "Running the update: $UpdateScript" -ForegroundColor Yellow
Write-Host ""
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $UpdateScript
exit $LASTEXITCODE
