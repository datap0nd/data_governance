<#
.SYNOPSIS
    Reconnect a target user's disconnected RDP session to the console.

.DESCRIPTION
    Interactive sign-in automation needs a real desktop session. If the target
    account is left in a disconnected RDP session, this script uses tscon to
    move that session back to the console. By default it only repairs
    disconnected sessions, so it will not interrupt an active RDP user.
#>
param(
    [string]$TargetUser = $env:DG_PBI_SYNC_WINDOWS_USER,
    [switch]$AllowActiveRdpTransfer,
    [int]$MaxTransfers = 1,
    [string]$LogPath = (Join-Path $env:TEMP "dg_rdp_console_guard.log")
)

$ErrorActionPreference = "Stop"

function Write-GuardLog {
    param([string]$Message)
    $line = "[$([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))] $Message"
    try {
        Add-Content -Path $LogPath -Value $line -ErrorAction SilentlyContinue
    } catch {}
    Write-Host $line
}

function Get-ShortUserName {
    param([string]$Value)
    $raw = ""
    if ($null -ne $Value) {
        $raw = $Value.Trim()
    }
    if (-not $raw) { return "" }
    if ($raw.Contains("\")) { return ($raw.Split("\")[-1]).ToLowerInvariant() }
    if ($raw.Contains("@")) { return ($raw.Split("@")[0]).ToLowerInvariant() }
    return $raw.ToLowerInvariant()
}

if (-not $TargetUser) {
    $TargetUser = $env:USERNAME
}

$targetShort = Get-ShortUserName $TargetUser
if (-not $targetShort) {
    Write-GuardLog "No target user configured. Set DG_PBI_SYNC_WINDOWS_USER."
    exit 2
}

$tscon = Join-Path $env:SystemRoot "System32\tscon.exe"
if (-not (Test-Path $tscon)) {
    Write-GuardLog "tscon.exe was not found."
    exit 3
}

try {
    $sessionRows = & quser 2>$null
} catch {
    Write-GuardLog "Could not query sessions with quser: $_"
    exit 4
}

$sessions = @()
foreach ($line in $sessionRows) {
    if ($line -match "^\s*>?\s*(?<user>\S+)\s+(?:(?<session>\S+)\s+)?(?<id>\d+)\s+(?<state>\S+)") {
        $sessionName = ""
        if ($Matches.ContainsKey("session")) {
            $sessionName = $Matches.session
        }
        $sessions += [pscustomobject]@{
            User = $Matches.user
            ShortUser = Get-ShortUserName $Matches.user
            SessionName = $sessionName
            Id = [int]$Matches.id
            State = $Matches.state
            RawLine = $line.Trim()
        }
    }
}

if (-not $sessions.Count) {
    Write-GuardLog "No user sessions found."
    exit 0
}

$targetSessions = @($sessions | Where-Object { $_.ShortUser -eq $targetShort })
if (-not $targetSessions.Count) {
    Write-GuardLog "No session found for target user '$TargetUser'."
    exit 0
}

$consoleActive = $targetSessions | Where-Object {
    $_.SessionName -eq "console" -and $_.State -eq "Active"
} | Select-Object -First 1
if ($consoleActive) {
    Write-GuardLog "Target user '$TargetUser' is already active on console session $($consoleActive.Id)."
    exit 0
}

$candidates = @($targetSessions | Where-Object {
    $_.State -eq "Disc" -or (
        $AllowActiveRdpTransfer -and
        $_.State -eq "Active" -and
        $_.SessionName -like "rdp-tcp*"
    )
})

if (-not $candidates.Count) {
    $states = ($targetSessions | ForEach-Object { "$($_.SessionName):$($_.Id):$($_.State)" }) -join ", "
    Write-GuardLog "No repairable RDP session found for '$TargetUser'. Sessions: $states"
    exit 0
}

$transferred = 0
$failed = 0
foreach ($session in $candidates | Select-Object -First $MaxTransfers) {
    Write-GuardLog "Transferring target user '$TargetUser' session $($session.Id) ($($session.State)) to console."
    try {
        $output = & $tscon $session.Id /dest:console 2>&1
        if ($LASTEXITCODE -eq 0) {
            $transferred += 1
            Write-GuardLog "Transferred session $($session.Id) to console."
        } else {
            $failed += 1
            Write-GuardLog "tscon failed for session $($session.Id): $output"
        }
    } catch {
        $failed += 1
        Write-GuardLog "tscon threw for session $($session.Id): $_"
    }
}

if ($failed -gt 0 -and $transferred -eq 0) {
    exit 5
}

exit 0
