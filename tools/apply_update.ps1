# Metronome automatic-update bridge.
#
# The application detects a new GitHub main commit and starts the fixed,
# elevated Metronome_Auto_Update task. This bridge validates that request and
# invokes the same setup.ps1 used for manual installs. It is intentionally not
# a second installer.

[CmdletBinding()]
param(
    [string]$RequestPath
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$CodeDir = Split-Path -Parent $PSScriptRoot
$ProjectDir = Split-Path -Parent $CodeDir
$UpdatesRoot = Join-Path $ProjectDir "updates"
$ExpectedRequestPath = Join-Path $UpdatesRoot "pending_update.json"
$ReceiptsDir = Join-Path $UpdatesRoot "receipts"
$LogsDir = Join-Path $UpdatesRoot "logs"
$SetupScript = Join-Path $CodeDir "setup.ps1"
$MutexName = "Global\Metronome_Auto_Update"
if (-not $RequestPath) { $RequestPath = $ExpectedRequestPath }

$AttemptId = $null
$TargetSha = $null
$ReceiptPath = $null
$LogPath = $null
$Mutex = $null
$MutexAcquired = $false
$TranscriptStarted = $false
$TerminalReceiptWritten = $false
$StartedAt = (Get-Date).ToUniversalTime().ToString("o")

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-PathEqual {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right
    )
    return [string]::Equals(
        (Get-NormalizedPath -Path $Left),
        (Get-NormalizedPath -Path $Right),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Write-AtomicText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temp = "$Path.tmp.$PID.$([Guid]::NewGuid().ToString('N'))"
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($temp, $Content, $utf8)
        Move-Item -LiteralPath $temp -Destination $Path -Force
    } finally {
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    }
}

function Save-Receipt {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$ErrorDetail
    )
    if (-not $ReceiptPath) { return }
    $receipt = [ordered]@{
        attempt_id = $AttemptId
        target_commit = $TargetSha
        status = $Status
        stage = $Stage
        message = $Message
        started_at = $StartedAt
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
        log_path = $LogPath
    }
    if ($Status -in @("succeeded", "failed")) {
        $receipt["finished_at"] = $receipt["updated_at"]
        $script:TerminalReceiptWritten = $true
    }
    if ($ErrorDetail) { $receipt["error"] = $ErrorDetail }
    Write-AtomicText -Path $ReceiptPath -Content ($receipt | ConvertTo-Json -Depth 4)
}

function Remove-OwnedRequest {
    if (-not $AttemptId -or -not (Test-Path -LiteralPath $ExpectedRequestPath -PathType Leaf)) {
        return
    }
    try {
        $pending = Get-Content -LiteralPath $ExpectedRequestPath -Raw | ConvertFrom-Json
        if ($pending.attempt_id -eq $AttemptId) {
            Remove-Item -LiteralPath $ExpectedRequestPath -Force
        }
    } catch {}
}

try {
    if (-not (Test-PathEqual -Left $RequestPath -Right $ExpectedRequestPath)) {
        throw "Updater accepts only the fixed pending_update.json request path"
    }
    if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) {
        throw "Update request does not exist: $RequestPath"
    }
    $request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
    if ([int]$request.version -ne 1) { throw "Update request version must be 1" }

    $parsedAttempt = [Guid]::Empty
    if ($request.attempt_id -isnot [string] -or
        -not [Guid]::TryParse($request.attempt_id, [ref]$parsedAttempt)) {
        throw "Update request attempt_id must be a UUID"
    }
    $AttemptId = $parsedAttempt.ToString()
    if ($request.target_commit -isnot [string] -or
        $request.target_commit -cnotmatch '^[0-9a-fA-F]{40}$') {
        throw "Update request target_commit must be exactly 40 hexadecimal characters"
    }
    $TargetSha = $request.target_commit.ToLowerInvariant()
    if ($request.code_dir -isnot [string] -or
        -not (Test-PathEqual -Left $request.code_dir -Right $CodeDir)) {
        throw "Update request code_dir does not match this installation"
    }

    $ReceiptPath = Join-Path $ReceiptsDir "$AttemptId.json"
    if ($request.receipt_path -isnot [string] -or
        -not (Test-PathEqual -Left $request.receipt_path -Right $ReceiptPath)) {
        throw "Update request receipt_path is outside the fixed receipt location"
    }
    $LogPath = Join-Path $LogsDir "$AttemptId.log"
    New-Item -ItemType Directory -Path $ReceiptsDir -Force | Out-Null
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null

    $Mutex = New-Object System.Threading.Mutex($false, $MutexName)
    try {
        $MutexAcquired = $Mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $MutexAcquired = $true
    }
    if (-not $MutexAcquired) { exit 2 }

    Start-Transcript -Path $LogPath -Append | Out-Null
    $TranscriptStarted = $true
    if (-not (Test-Path -LiteralPath $SetupScript -PathType Leaf)) {
        throw "setup.ps1 was not found at $SetupScript"
    }

    Save-Receipt -Status "running" -Stage "launching_setup" `
        -Message "Launching setup.ps1 for the detected GitHub main commit."
    $env:DG_UPDATE_COMMIT_SHA = $TargetSha
    Remove-Item Env:DG_UPDATE_ZIP_URL -ErrorAction SilentlyContinue
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File $SetupScript -Unattended
    if ($LASTEXITCODE -ne 0) {
        throw "setup.ps1 exited with code $LASTEXITCODE"
    }

    $deployed = ""
    try {
        $deployed = (Get-Content -LiteralPath (Join-Path $CodeDir "VERSION") -First 1).Trim()
    } catch {}
    if (-not $deployed.EndsWith($TargetSha, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "setup.ps1 completed but VERSION does not identify commit $TargetSha"
    }
    Save-Receipt -Status "succeeded" -Stage "healthy" `
        -Message "setup.ps1 installed the detected GitHub main commit."
    exit 0
} catch {
    $failure = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
    try {
        Save-Receipt -Status "failed" -Stage "setup_failed" `
            -Message "setup.ps1 did not complete the automatic update." `
            -ErrorDetail $failure
    } catch {}
    Write-Error $failure
    exit 1
} finally {
    if ($TerminalReceiptWritten) { Remove-OwnedRequest }
    if ($MutexAcquired -and $Mutex) {
        try { $Mutex.ReleaseMutex() } catch {}
    }
    if ($Mutex) { $Mutex.Dispose() }
    if ($TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
    }
}
