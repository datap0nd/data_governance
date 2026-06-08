<#
.SYNOPSIS
    Disconnect the current RDP session by transferring it to the console.

.DESCRIPTION
    Closing an RDP window usually leaves the Windows session disconnected or
    locked, which prevents interactive Power BI sign-in automation from seeing
    or clicking the Microsoft account picker. This script uses tscon to move the
    current session back to the console before disconnecting RDP.

    Run from the RDP session in an elevated PowerShell window.
#>

$ErrorActionPreference = "Stop"

function Get-CurrentSessionInfo {
    $sessionId = (Get-Process -Id $PID).SessionId
    $result = [ordered]@{
        SessionId = $sessionId
        SessionName = ""
        State = ""
        RawLine = ""
    }

    try {
        $rows = & quser 2>$null
        foreach ($line in $rows) {
            if ($line -match "^\s*>?\s*(?<user>\S+)\s+(?:(?<session>\S+)\s+)?(?<id>\d+)\s+(?<state>\S+)") {
                if ([int]$Matches.id -eq [int]$sessionId) {
                    $result.SessionName = if ($Matches.session) { $Matches.session } else { "" }
                    $result.State = $Matches.state
                    $result.RawLine = $line.Trim()
                    break
                }
            }
        }
    } catch {}

    return [pscustomobject]$result
}

$info = Get-CurrentSessionInfo
$tscon = Join-Path $env:SystemRoot "System32\tscon.exe"

if (-not (Test-Path $tscon)) {
    Write-Host "tscon.exe was not found. This helper only works on Windows editions that include Remote Desktop Services tools." -ForegroundColor Red
    exit 1
}

if (-not $info.SessionName) {
    Write-Host "Could not confirm that the current session is an RDP session." -ForegroundColor Red
    Write-Host "Current session: $($info.RawLine)" -ForegroundColor DarkGray
    Write-Host "Run this from an elevated PowerShell inside the RDP session." -ForegroundColor Yellow
    exit 1
}

if ($info.SessionName -notmatch "^rdp-tcp") {
    Write-Host "Current session does not look like an RDP session: $($info.RawLine)" -ForegroundColor Yellow
    Write-Host "Nothing to transfer." -ForegroundColor Yellow
    exit 0
}

Write-Host "Transferring session $($info.SessionId) to the console..." -ForegroundColor Cyan
Write-Host "After this succeeds, the RDP window will disconnect and the console session should stay available for the Power BI sync." -ForegroundColor DarkGray

& $tscon $info.SessionId /dest:console

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Could not transfer the session. Run PowerShell as Administrator inside the RDP session and try again." -ForegroundColor Red
    exit $LASTEXITCODE
}
