<#
.SYNOPSIS
    Shared helpers for interactive Power BI sync scripts.
#>

function Get-DgPbiConnectTimeout {
    param([int]$DefaultSeconds = 120)

    if (-not $env:DG_PBI_CONNECT_TIMEOUT_SECONDS) {
        return $DefaultSeconds
    }

    try {
        $value = [int]$env:DG_PBI_CONNECT_TIMEOUT_SECONDS
        if ($value -lt 30) { return 30 }
        return $value
    } catch {
        return $DefaultSeconds
    }
}

function Get-DgCurrentSessionInfo {
    $sessionId = $null
    try {
        $sessionId = (Get-Process -Id $PID).SessionId
    } catch {}

    $info = [ordered]@{
        SessionId = $sessionId
        SessionName = ""
        State = ""
        RawLine = ""
        QueryUserAvailable = $false
    }

    try {
        $rows = & quser 2>$null
        $info.QueryUserAvailable = $true
        foreach ($line in $rows) {
            if ($line -match "^\s*>?\s*(?<user>\S+)\s+(?:(?<session>\S+)\s+)?(?<id>\d+)\s+(?<state>\S+)") {
                if ($sessionId -ne $null -and [int]$Matches.id -eq [int]$sessionId) {
                    $info.SessionName = if ($Matches.session) { $Matches.session } else { "" }
                    $info.State = $Matches.state
                    $info.RawLine = $line.Trim()
                    break
                }
            }
        }
    } catch {}

    return [pscustomobject]$info
}

function Test-DgInteractiveSignInReady {
    $session = Get-DgCurrentSessionInfo
    $sessionText = if ($session.SessionId -ne $null) { "session $($session.SessionId)" } else { "the current session" }

    if ($session.State -and $session.State -ne "Active") {
        return [pscustomobject]@{
            Ready = $false
            Message = "Interactive Power BI sign-in cannot run because $sessionText is '$($session.State)', not Active. The RDP console guard did not repair the sync user's desktop session. Check DG_PBI_SYNC_WINDOWS_USER and the DG_RDP_Console_Guard scheduled task."
            Details = @{
                session_id = $session.SessionId
                session_name = $session.SessionName
                state = $session.State
                raw_line = $session.RawLine
            }
        }
    }

    try {
        $locked = Get-Process -Name LogonUI -ErrorAction SilentlyContinue |
            Where-Object { $session.SessionId -eq $null -or $_.SessionId -eq $session.SessionId } |
            Select-Object -First 1
        if ($locked) {
            return [pscustomobject]@{
                Ready = $false
                Message = "Interactive Power BI sign-in cannot run because Windows is at the lock screen. The RDP console guard did not leave the sync user's desktop available."
                Details = @{
                    session_id = $session.SessionId
                    session_name = $session.SessionName
                    state = $session.State
                    logon_ui_pid = $locked.Id
                }
            }
        }
    } catch {}

    return [pscustomobject]@{
        Ready = $true
        Message = "Interactive desktop appears available."
        Details = @{
            session_id = $session.SessionId
            session_name = $session.SessionName
            state = $session.State
            raw_line = $session.RawLine
        }
    }
}

function Start-DgPbiConnectWatchdog {
    param(
        [string]$ApiBase,
        [string]$SyncType,
        [int]$TimeoutSeconds,
        [int]$ParentPid = $PID
    )

    if ($TimeoutSeconds -le 0) {
        return $null
    }

    $message = "Interactive Power BI sign-in did not complete within $TimeoutSeconds seconds. Windows may be locked, disconnected, or waiting at the Microsoft account picker."
    $script = @"
`$ErrorActionPreference = "SilentlyContinue"
Start-Sleep -Seconds $TimeoutSeconds
`$parent = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
if (`$parent) {
    try {
        `$payload = @{
            sync_type = "$SyncType"
            status = "failed"
            message = "$message"
        } | ConvertTo-Json -Depth 4
        Invoke-RestMethod -Uri "$ApiBase/api/scanner/pbi-sync/run-status" -Method POST -Body `$payload -ContentType "application/json; charset=utf-8" | Out-Null
    } catch {}
    Stop-Process -Id $ParentPid -Force -ErrorAction SilentlyContinue
}
"@

    try {
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
        return Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", $encoded
        )
    } catch {
        return $null
    }
}

function Stop-DgPbiConnectWatchdog {
    param($Watchdog)
    if ($Watchdog -and -not $Watchdog.HasExited) {
        Stop-Process -Id $Watchdog.Id -Force -ErrorAction SilentlyContinue
    }
}
