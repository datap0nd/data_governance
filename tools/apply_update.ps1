# Metronome unattended update worker.
#
# This script is invoked by the fixed, elevated Metronome_Auto_Update task
# registered by setup.ps1. It deliberately contains no prompts, browser
# fallback, UAC relaunch, or service-account provisioning.

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
if (-not $RequestPath) {
    $RequestPath = $ExpectedRequestPath
}

$ServiceName = "MXAnalytics"
$FlowServiceName = "MXFlowsWorker"
$HeadedFlowTaskName = "Metronome_Flows_Headed"
$Repository = "datap0nd/data_governance"
$MutexName = "Global\Metronome_Auto_Update"
$PythonExe = Join-Path $ProjectDir "python313\python.exe"
$BackupHelper = Join-Path $PSScriptRoot "backup_sqlite.py"

$AttemptId = $null
$TargetSha = $null
$ReceiptPath = $null
$LogPath = $null
$WorkRoot = $null
$CodeBackup = $null
$DatabaseBackup = $null
$DatabasePath = $null
$PreviousVersion = $null
$MainWasRunning = $false
$FlowWasRunning = $false
$HeadedTaskWasRunning = $false
$MutationStarted = $false
$CodeMutationStarted = $false
$DatabaseMutationPossible = $false
$Mutex = $null
$MutexAcquired = $false
$TranscriptStarted = $false
$TerminalReceiptWritten = $false
$StartedAt = (Get-Date).ToUniversalTime().ToString("o")
$Receipt = [ordered]@{}

function Write-AtomicText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $nonce = [Guid]::NewGuid().ToString('N')
    $temp = "$Path.tmp.$PID.$nonce"
    $replaceBackup = "$Path.replaced.$PID.$nonce.bak"
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($temp, $Content, $utf8)
        if (Test-Path -LiteralPath $Path) {
            # Windows requires a non-empty backup path for File.Replace. The
            # destination swap is atomic; its short-lived backup is best-effort
            # cleanup and never changes the completed write result.
            [System.IO.File]::Replace($temp, $Path, $replaceBackup, $true)
        } else {
            [System.IO.File]::Move($temp, $Path)
        }
    } finally {
        try { [System.IO.File]::Delete($temp) } catch {}
        try { [System.IO.File]::Delete($replaceBackup) } catch {}
    }
}

function Save-Receipt {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string]$Message,
        [bool]$RollbackSucceeded = $false,
        [string]$ErrorDetail
    )

    if (-not $ReceiptPath) { return }
    $Receipt["attempt_id"] = $AttemptId
    $Receipt["target_commit"] = $TargetSha
    $Receipt["sha"] = $TargetSha
    $Receipt["status"] = $Status
    $Receipt["stage"] = $Stage
    $Receipt["message"] = $Message
    $Receipt["started_at"] = $StartedAt
    $Receipt["updated_at"] = (Get-Date).ToUniversalTime().ToString("o")
    if ($Status -in @("succeeded", "failed", "rolled_back")) {
        $Receipt["finished_at"] = $Receipt["updated_at"]
    }
    $Receipt["rollback_succeeded"] = $RollbackSucceeded
    if ($LogPath) { $Receipt["log_path"] = $LogPath }
    if ($CodeBackup) { $Receipt["code_backup"] = $CodeBackup }
    if ($DatabaseBackup) { $Receipt["database_backup"] = $DatabaseBackup }
    if ($ErrorDetail) {
        $Receipt["error"] = $ErrorDetail
    } elseif ($Receipt.Contains("error")) {
        $Receipt.Remove("error")
    }
    Write-AtomicText -Path $ReceiptPath -Content ($Receipt | ConvertTo-Json -Depth 6)
    if ($Status -in @("succeeded", "failed", "rolled_back")) {
        $script:TerminalReceiptWritten = $true
    }
}

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

function Invoke-RobocopyChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [switch]$Mirror
    )

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $arguments = @(
        $Source, $Destination, $(if ($Mirror) { "/MIR" } else { "/E" }),
        "/R:2", "/W:1", "/XJ", "/COPY:DAT", "/DCOPY:DAT",
        "/NFL", "/NDL", "/NJH", "/NJS", "/NP"
    )
    & robocopy.exe @arguments | Out-Null
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        throw "robocopy failed with exit code $code ($Source -> $Destination)"
    }
}

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Resolve-DatabasePath {
    if ($env:DG_DB_PATH) { return $env:DG_DB_PATH }

    $parametersKey = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName\Parameters"
    try {
        $environment = (Get-ItemProperty -LiteralPath $parametersKey -Name AppEnvironmentExtra -ErrorAction Stop).AppEnvironmentExtra
        foreach ($entry in @($environment)) {
            if ($entry -is [string] -and $entry.StartsWith("DG_DB_PATH=", [System.StringComparison]::OrdinalIgnoreCase)) {
                return $entry.Substring("DG_DB_PATH=".Length)
            }
        }
    } catch {
        # A non-NSSM or older installation falls back to the documented path.
    }
    return (Join-Path $ProjectDir "governance.db")
}

function Download-ExactArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Sha,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $uri = "https://github.com/$Repository/archive/$Sha.zip"
    $headers = @{ "User-Agent" = "Metronome-Unattended-Update" }
    if ($env:DG_GITHUB_TOKEN) {
        $uri = "https://api.github.com/repos/$Repository/zipball/$Sha"
        $headers["Authorization"] = "Bearer $($env:DG_GITHUB_TOKEN)"
        $headers["Accept"] = "application/vnd.github+json"
    }
    $partial = "$Destination.partial"
    $lastError = $null
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        try {
            Write-Host "Downloading exact commit $Sha (attempt $attempt of 5)..."
            Invoke-WebRequest -Uri $uri -OutFile $partial -UseBasicParsing -Headers $headers -TimeoutSec 120
            if ((Get-Item -LiteralPath $partial).Length -lt 1024) {
                throw "Downloaded archive is unexpectedly small"
            }
            Move-Item -LiteralPath $partial -Destination $Destination -Force
            return
        } catch {
            $lastError = $_.Exception.Message
            if ($attempt -lt 5) { Start-Sleep -Seconds ([Math]::Min(20, $attempt * 3)) }
        }
    }
    throw "Could not download exact commit $Sha after 5 attempts: $lastError"
}

function Expand-AndValidateArchive {
    param(
        [Parameter(Mandatory = $true)][string]$ArchivePath,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Sha
    )

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destinationPrefix = [System.IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
    $zip = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        foreach ($entry in $zip.Entries) {
            $entryPath = [System.IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
            if (-not $entryPath.StartsWith($destinationPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Archive contains an unsafe path: $($entry.FullName)"
            }
        }
    } finally {
        $zip.Dispose()
    }

    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $Destination
    $roots = @(Get-ChildItem -LiteralPath $Destination -Directory)
    if ($roots.Count -ne 1) {
        throw "Expected exactly one archive root directory; found $($roots.Count)"
    }
    $root = $roots[0]
    $archiveRevision = ($root.Name -split '-')[-1].ToLowerInvariant()
    if ($archiveRevision.Length -lt 7 -or -not $Sha.ToLowerInvariant().StartsWith($archiveRevision)) {
        throw "Archive root '$($root.Name)' does not identify requested commit $Sha"
    }

    $required = @(
        "app\main.py",
        "app\database.py",
        "requirements.txt",
        "tools\nssm.exe",
        "tools\apply_update.ps1",
        "tools\backup_sqlite.py"
    )
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $root.FullName $relative) -PathType Leaf)) {
            throw "Update archive is missing required file: $relative"
        }
    }

    Invoke-CheckedNative -FilePath $PythonExe `
        -Arguments @("-m", "compileall", "-q", (Join-Path $root.FullName "app"), (Join-Path $root.FullName "tools")) `
        -Description "Staged Python compilation"
    Get-ChildItem -LiteralPath $root.FullName -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    return $root.FullName
}

function Prepare-CompleteWheelhouse {
    param(
        [Parameter(Mandatory = $true)][string]$StagedCode,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $requirements = Join-Path $StagedCode "requirements.txt"
    $vendor = Join-Path $StagedCode "vendor"
    if (-not (Test-Path -LiteralPath $vendor -PathType Container)) {
        throw "Staged update has no bundled vendor directory"
    }
    New-Item -ItemType Directory -Path $Destination | Out-Null

    # All network/build work happens while the old application is still up.
    # ``pip wheel`` downloads binary dependencies and builds wheels for any
    # source-only requirement (for example reverse_geocoder).
    Invoke-CheckedNative -FilePath $PythonExe `
        -Arguments @(
            "-m", "pip", "wheel", "--disable-pip-version-check", "--prefer-binary",
            "--wheel-dir", $Destination, "--find-links", $vendor, "-r", $requirements
        ) `
        -Description "Complete dependency wheelhouse preparation"

    # Resolve the complete graph again with indexes disabled. This proves that
    # the post-stop install cannot unexpectedly reach the network or discover a
    # missing transitive/build artifact.
    $verification = Join-Path (Split-Path -Parent $Destination) "wheelhouse-verification"
    New-Item -ItemType Directory -Path $verification | Out-Null
    Invoke-CheckedNative -FilePath $PythonExe `
        -Arguments @(
            "-m", "pip", "download", "--disable-pip-version-check", "--no-index",
            "--only-binary", ":all:", "--find-links", $Destination,
            "--dest", $verification, "-r", $requirements
        ) `
        -Description "Offline wheelhouse resolution"
}

function Stop-ServiceChecked {
    param([Parameter(Mandatory = $true)][string]$Name)

    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $service -or $service.Status -eq "Stopped") { return }
    Stop-Service -Name $Name -Force -ErrorAction Stop
    $service.WaitForStatus(
        [System.ServiceProcess.ServiceControllerStatus]::Stopped,
        [TimeSpan]::FromSeconds(45)
    )
}

function Start-ServiceChecked {
    param([Parameter(Mandatory = $true)][string]$Name)

    $service = Get-Service -Name $Name -ErrorAction Stop
    if ($service.Status -ne "Running") {
        Start-Service -Name $Name -ErrorAction Stop
        $service.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds(45)
        )
    }
}

function Stop-HeadedTask {
    $task = Get-ScheduledTask -TaskName $HeadedFlowTaskName -ErrorAction SilentlyContinue
    if (-not $task -or $task.State -ne "Running") { return }
    Stop-ScheduledTask -TaskName $HeadedFlowTaskName -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $state = (Get-ScheduledTask -TaskName $HeadedFlowTaskName -ErrorAction SilentlyContinue).State
    } while ($state -eq "Running" -and (Get-Date) -lt $deadline)
    if ($state -eq "Running") { throw "Headed Flows task did not stop within 30 seconds" }
}

function Start-PreviousRuntime {
    if ($MainWasRunning) { Start-ServiceChecked -Name $ServiceName }
    if ($FlowWasRunning -and (Get-Service -Name $FlowServiceName -ErrorAction SilentlyContinue)) {
        Start-ServiceChecked -Name $FlowServiceName
    }
}

function Resume-HeadedTask {
    if ($HeadedTaskWasRunning -and (Get-ScheduledTask -TaskName $HeadedFlowTaskName -ErrorAction SilentlyContinue)) {
        Start-ScheduledTask -TaskName $HeadedFlowTaskName -ErrorAction Stop
    }
}

function Stop-ExistingRuntime {
    Stop-HeadedTask
    Stop-ServiceChecked -Name $FlowServiceName
    Stop-ServiceChecked -Name $ServiceName
}

function Set-BoundedServiceLogPolicy {
    param([Parameter(Mandatory = $true)][string]$Name)

    # The updater already runs elevated and the service is stopped. Updating
    # only NSSM's rotation values preserves the executable, account identity,
    # password, startup mode, and every application argument.
    $parametersKey = "HKLM:\SYSTEM\CurrentControlSet\Services\$Name\Parameters"
    if (-not (Test-Path -LiteralPath $parametersKey -PathType Container)) { return }
    foreach ($setting in @{
        AppRotateFiles = 1
        AppRotateOnline = 1
        AppRotateSeconds = 86400
        AppRotateBytes = 10485760
    }.GetEnumerator()) {
        New-ItemProperty -LiteralPath $parametersKey -Name $setting.Key `
            -Value ([int]$setting.Value) -PropertyType DWord -Force | Out-Null
    }
}

function Wait-AppVersion {
    param(
        [Parameter(Mandatory = $true)][string]$Sha,
        [int]$TimeoutSeconds = 180
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = "no response"
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/version" -TimeoutSec 5
            $reported = "$($response.commit)".Trim().ToLowerInvariant()
            if ($reported -eq $Sha.ToLowerInvariant()) { return }
            $lastError = "service reported commit '$reported'"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "Metronome did not become healthy at commit $Sha within $TimeoutSeconds seconds ($lastError)"
}

function Wait-AppReady {
    param([int]$TimeoutSeconds = 120)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/version" -TimeoutSec 5
            if ($response.version) { return }
        } catch {}
        Start-Sleep -Seconds 2
    }
    throw "Previous Metronome version did not become healthy after rollback"
}

function Restore-PreviousInstallation {
    Stop-ExistingRuntime
    if ($CodeMutationStarted) {
        Invoke-RobocopyChecked -Source $CodeBackup -Destination $CodeDir -Mirror
    }

    # Do not replace live data when failure happened before the new app could
    # run migrations. This preserves any writes committed between the online
    # snapshot and the bounded service stop.
    if ($DatabaseMutationPossible) {
        Remove-Item -LiteralPath "$DatabasePath-wal" -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath "$DatabasePath-shm" -Force -ErrorAction SilentlyContinue
        Invoke-CheckedNative -FilePath $PythonExe `
            -Arguments @($BackupHelper, $DatabaseBackup, $DatabasePath) `
            -Description "SQLite rollback restore"
    }

    Start-PreviousRuntime
    if ($MainWasRunning) { Wait-AppReady }
    Resume-HeadedTask
}

function Test-ReparsePoint {
    param([Parameter(Mandatory = $true)]$Item)

    return (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Test-TreeContainsReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Root)

    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($Root)
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        $currentItem = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (Test-ReparsePoint -Item $currentItem) { return $true }
        foreach ($childPath in [System.IO.Directory]::EnumerateFileSystemEntries($current)) {
            $child = Get-Item -LiteralPath $childPath -Force -ErrorAction Stop
            if (Test-ReparsePoint -Item $child) { return $true }
            if ($child.PSIsContainer) { $stack.Push($child.FullName) }
        }
    }
    return $false
}

function Get-CanonicalAttemptId {
    param([Parameter(Mandatory = $true)][string]$Value)

    $parsed = [Guid]::Empty
    if (-not [Guid]::TryParse($Value, [ref]$parsed)) { return $null }
    $canonical = $parsed.ToString()
    if (-not [string]::Equals($canonical, $Value, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $null
    }
    return $canonical
}

function Add-RetentionCandidate {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Candidates,
        [Parameter(Mandatory = $true)][string]$Attempt,
        [Parameter(Mandatory = $true)][datetime]$LastWriteTimeUtc
    )

    $ticks = $LastWriteTimeUtc.Ticks
    if (-not $Candidates.ContainsKey($Attempt) -or $ticks -gt $Candidates[$Attempt]) {
        $Candidates[$Attempt] = $ticks
    }
}

function Invoke-SafeUpdateRetention {
    param([Parameter(Mandatory = $true)][string]$CurrentAttemptId)

    $normalizedRoot = Get-NormalizedPath -Path $UpdatesRoot
    $attemptsRoot = Join-Path $normalizedRoot "attempts"
    $logsRoot = Join-Path $normalizedRoot "logs"
    $receiptsRoot = Join-Path $normalizedRoot "receipts"
    foreach ($root in @($normalizedRoot, $attemptsRoot, $logsRoot, $receiptsRoot)) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
        if (Test-ReparsePoint -Item $rootItem) {
            throw "Retention skipped because update storage contains a reparse-point root: $root"
        }
    }
    if (-not (Test-PathEqual -Left (Split-Path -Parent $attemptsRoot) -Right $normalizedRoot) -or
        -not (Test-PathEqual -Left (Split-Path -Parent $logsRoot) -Right $normalizedRoot) -or
        -not (Test-PathEqual -Left (Split-Path -Parent $receiptsRoot) -Right $normalizedRoot)) {
        throw "Retention roots are not direct children of the normalized update root"
    }

    $candidates = @{}
    if (Test-Path -LiteralPath $attemptsRoot -PathType Container) {
        foreach ($item in @(Get-ChildItem -LiteralPath $attemptsRoot -Directory -Force -ErrorAction Stop)) {
            if (Test-ReparsePoint -Item $item) { continue }
            $id = Get-CanonicalAttemptId -Value $item.Name
            if ($id) { Add-RetentionCandidate -Candidates $candidates -Attempt $id -LastWriteTimeUtc $item.LastWriteTimeUtc }
        }
    }
    foreach ($spec in @(
        @{ Root = $logsRoot; Extension = ".log" },
        @{ Root = $receiptsRoot; Extension = ".json" }
    )) {
        if (-not (Test-Path -LiteralPath $spec.Root -PathType Container)) { continue }
        foreach ($item in @(Get-ChildItem -LiteralPath $spec.Root -File -Force -ErrorAction Stop)) {
            if ((Test-ReparsePoint -Item $item) -or $item.Extension -ne $spec.Extension) { continue }
            $id = Get-CanonicalAttemptId -Value $item.BaseName
            if ($id) { Add-RetentionCandidate -Candidates $candidates -Attempt $id -LastWriteTimeUtc $item.LastWriteTimeUtc }
        }
    }

    $currentId = Get-CanonicalAttemptId -Value $CurrentAttemptId
    if (-not $currentId) { throw "Current retention attempt id is not canonical" }
    $keep = @{ $currentId = $true }
    $prior = @(
        $candidates.GetEnumerator() |
            Where-Object { $_.Key -ne $currentId } |
            Sort-Object -Property Value -Descending |
            Select-Object -First 2
    )
    foreach ($entry in $prior) { $keep[$entry.Key] = $true }

    foreach ($id in @($candidates.Keys)) {
        if ($keep.ContainsKey($id)) { continue }
        $attemptPath = Join-Path $attemptsRoot $id
        if (Test-Path -LiteralPath $attemptPath) {
            if (-not (Test-PathEqual -Left (Split-Path -Parent $attemptPath) -Right $attemptsRoot)) {
                continue
            }
            if (Test-TreeContainsReparsePoint -Root $attemptPath) {
                # Never traverse or remove a workspace that contains a link.
                continue
            }
            Remove-Item -LiteralPath $attemptPath -Recurse -Force -ErrorAction Stop
        }

        foreach ($artifact in @(
            (Join-Path $logsRoot "$id.log"),
            (Join-Path $receiptsRoot "$id.json")
        )) {
            if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { continue }
            $parent = Split-Path -Parent $artifact
            if (-not ((Test-PathEqual -Left $parent -Right $logsRoot) -or
                      (Test-PathEqual -Left $parent -Right $receiptsRoot))) { continue }
            $artifactItem = Get-Item -LiteralPath $artifact -Force -ErrorAction Stop
            if (Test-ReparsePoint -Item $artifactItem) { continue }
            Remove-Item -LiteralPath $artifact -Force -ErrorAction Stop
        }
    }
}

try {
    if (-not (Test-PathEqual -Left $RequestPath -Right $ExpectedRequestPath)) {
        throw "Updater accepts only the fixed pending_update.json request path"
    }
    if (-not (Test-Path -LiteralPath $RequestPath -PathType Leaf)) {
        throw "Update request does not exist: $RequestPath"
    }
    $request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
    if ([int]$request.version -ne 1) {
        throw "Update request version must be 1"
    }
    $parsedAttempt = [Guid]::Empty
    if ($request.attempt_id -isnot [string] -or -not [Guid]::TryParse($request.attempt_id, [ref]$parsedAttempt)) {
        throw "Update request attempt_id must be a UUID"
    }
    $AttemptId = $parsedAttempt.ToString()
    if (-not [string]::Equals($AttemptId, $request.attempt_id, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Update request attempt_id must use canonical UUID form"
    }
    if ($request.target_commit -isnot [string] -or $request.target_commit -cnotmatch '^[0-9a-fA-F]{40}$') {
        throw "Update request target_commit must be exactly 40 hexadecimal characters"
    }
    $TargetSha = $request.target_commit.ToLowerInvariant()
    if ($request.trigger_source -notin @("automatic", "manual")) {
        throw "Update request trigger_source must be automatic or manual"
    }
    $ReceiptsDir = Join-Path $UpdatesRoot "receipts"
    $ExpectedReceiptPath = Join-Path $ReceiptsDir "$AttemptId.json"
    $ReceiptPath = $ExpectedReceiptPath
    if ($request.receipt_path -isnot [string] -or -not (Test-PathEqual -Left $request.receipt_path -Right $ExpectedReceiptPath)) {
        throw "Update request receipt_path is outside the fixed receipt location"
    }
    $LogPath = Join-Path (Join-Path $UpdatesRoot "logs") "$AttemptId.log"

    if ($request.code_dir -isnot [string] -or -not (Test-PathEqual -Left $request.code_dir -Right $CodeDir)) {
        throw "Update request code_dir does not match the updater installation"
    }
    if ($request.database_path -isnot [string] -or -not [System.IO.Path]::IsPathRooted($request.database_path)) {
        throw "Update request database_path must be an absolute path"
    }
    $ConfiguredDatabasePath = Resolve-DatabasePath
    if (-not (Test-PathEqual -Left $request.database_path -Right $ConfiguredDatabasePath)) {
        throw "Update request database_path does not match the installed service configuration"
    }
    $DatabasePath = Get-NormalizedPath -Path $request.database_path
    $Receipt["from_commit"] = $request.from_commit
    $Receipt["trigger_source"] = $request.trigger_source
    $Receipt["request_created_at"] = $request.created_at

    New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
    $Mutex = New-Object System.Threading.Mutex($false, $MutexName)
    try {
        $MutexAcquired = $Mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $MutexAcquired = $true
    }
    if (-not $MutexAcquired) {
        # The mutex owner may be processing this exact attempt. Never overwrite
        # its live receipt with a false terminal failure from a duplicate run.
        exit 2
    }

    if (Test-Path -LiteralPath $ReceiptPath) {
        $receiptItem = Get-Item -LiteralPath $ReceiptPath -Force -ErrorAction Stop
        if ($receiptItem.Length -gt 131072) {
            throw "Existing update receipt is unexpectedly large"
        }
        try {
            $existingReceipt = Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json
        } catch {
            throw "Existing update receipt is not valid JSON"
        }
        if ($existingReceipt.attempt_id -ne $AttemptId -or
            "$($existingReceipt.target_commit)".ToLowerInvariant() -ne $TargetSha) {
            throw "Existing update receipt does not match this exact attempt"
        }
        $existingStatus = "$($existingReceipt.status)".ToLowerInvariant()
        if ($existingStatus -in @("succeeded", "failed", "rolled_back")) {
            # A terminal receipt is the durable idempotency boundary.
            exit 0
        }
        foreach ($property in $existingReceipt.PSObject.Properties) {
            $Receipt[$property.Name] = $property.Value
        }
        $existingLog = $existingReceipt.PSObject.Properties["log_path"]
        $existingCodeBackup = $existingReceipt.PSObject.Properties["code_backup"]
        $existingDatabaseBackup = $existingReceipt.PSObject.Properties["database_backup"]
        if ($existingLog -and $existingLog.Value) { $LogPath = "$($existingLog.Value)" }
        if ($existingCodeBackup -and $existingCodeBackup.Value) { $CodeBackup = "$($existingCodeBackup.Value)" }
        if ($existingDatabaseBackup -and $existingDatabaseBackup.Value) { $DatabaseBackup = "$($existingDatabaseBackup.Value)" }
        throw "A previous updater process stopped before recording a terminal result; retained rollback artifacts require recovery"
    }

    Start-Transcript -Path $LogPath -Append | Out-Null
    $TranscriptStarted = $true
    Save-Receipt -Status "running" -Stage "validating_request" -Message "Update request accepted."

    if (Test-Path -LiteralPath (Join-Path $CodeDir ".git")) {
        Save-Receipt -Status "failed" -Stage "developer_checkout" `
            -Message "Refused to overwrite a .git developer checkout; deploy the service from a release installation." `
            -ErrorDetail "Automatic update cannot modify a Git working copy."
        exit 3
    }
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw "Portable Python runtime not found: $PythonExe"
    }
    if (-not (Test-Path -LiteralPath $BackupHelper -PathType Leaf)) {
        throw "SQLite backup helper not found: $BackupHelper"
    }

    $mainService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $mainService) { throw "Existing $ServiceName service was not found" }
    if ($mainService.Status -ne "Running") {
        throw "$ServiceName must be running before an in-app update"
    }
    $MainWasRunning = $true
    $flowService = Get-Service -Name $FlowServiceName -ErrorAction SilentlyContinue
    $FlowWasRunning = [bool]($flowService -and $flowService.Status -eq "Running")
    $headedTask = Get-ScheduledTask -TaskName $HeadedFlowTaskName -ErrorAction SilentlyContinue
    $HeadedTaskWasRunning = [bool]($headedTask -and $headedTask.State -eq "Running")

    if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) {
        throw "Metronome database was not found: $DatabasePath"
    }
    try { $PreviousVersion = (Get-Content -LiteralPath (Join-Path $CodeDir "VERSION") -First 1).Trim() } catch {}

    $WorkRoot = Join-Path (Join-Path $UpdatesRoot "attempts") $AttemptId
    if (Test-Path -LiteralPath $WorkRoot) {
        throw "Update workspace already exists for attempt $AttemptId"
    }
    New-Item -ItemType Directory -Path $WorkRoot | Out-Null
    $ArchivePath = Join-Path $WorkRoot "$TargetSha.zip"
    $ExtractPath = Join-Path $WorkRoot "staged"
    $CodeBackup = Join-Path $WorkRoot "previous-code"
    $DatabaseBackup = Join-Path $WorkRoot "previous-governance.db"
    $Wheelhouse = Join-Path $WorkRoot "wheelhouse"

    Save-Receipt -Status "running" -Stage "downloading" -Message "Downloading the exact requested commit."
    Download-ExactArchive -Sha $TargetSha -Destination $ArchivePath
    Save-Receipt -Status "running" -Stage "validating_archive" -Message "Validating and compiling staged code."
    $StagedCode = Expand-AndValidateArchive -ArchivePath $ArchivePath -Destination $ExtractPath -Sha $TargetSha

    Save-Receipt -Status "running" -Stage "preparing_dependencies" -Message "Preparing and proving a complete offline dependency wheelhouse."
    Prepare-CompleteWheelhouse -StagedCode $StagedCode -Destination $Wheelhouse

    Save-Receipt -Status "running" -Stage "snapshotting" -Message "Creating rollback snapshots before stopping services."
    Invoke-RobocopyChecked -Source $CodeDir -Destination $CodeBackup -Mirror
    Invoke-CheckedNative -FilePath $PythonExe `
        -Arguments @($BackupHelper, $DatabasePath, $DatabaseBackup) `
        -Description "WAL-aware SQLite backup"

    Save-Receipt -Status "running" -Stage "stopping" -Message "Stopping existing Metronome runtimes without changing their identities."
    $MutationStarted = $true
    Stop-ExistingRuntime
    # Capture the final quiesced database state as well. If this second backup
    # fails, backup_sqlite.py leaves the already-valid online snapshot intact.
    Invoke-CheckedNative -FilePath $PythonExe `
        -Arguments @($BackupHelper, $DatabasePath, $DatabaseBackup) `
        -Description "Quiesced SQLite rollback backup"

    Save-Receipt -Status "running" -Stage "installing" -Message "Applying staged code and installing prepared offline dependencies."
    $CodeMutationStarted = $true
    Invoke-RobocopyChecked -Source $StagedCode -Destination $CodeDir
    Invoke-CheckedNative -FilePath $PythonExe `
        -Arguments @(
            "-m", "pip", "install", "--disable-pip-version-check", "--no-index",
            "--find-links", $Wheelhouse, "-r", (Join-Path $CodeDir "requirements.txt")
        ) `
        -Description "Offline Python dependency validation/install"

    Set-BoundedServiceLogPolicy -Name $ServiceName
    if (Get-Service -Name $FlowServiceName -ErrorAction SilentlyContinue) {
        Set-BoundedServiceLogPolicy -Name $FlowServiceName
    }

    $VersionStamp = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $TargetSha
    Write-AtomicText -Path (Join-Path $CodeDir "VERSION") -Content ($VersionStamp + [Environment]::NewLine)

    Save-Receipt -Status "running" -Stage "restarting" -Message "Restarting the existing services and checking the deployed commit."
    $DatabaseMutationPossible = $true
    Start-PreviousRuntime
    Wait-AppVersion -Sha $TargetSha
    Resume-HeadedTask

    $Receipt["previous_version"] = $PreviousVersion
    $Receipt["deployed_version"] = $VersionStamp
    $Receipt["finished_at"] = (Get-Date).ToUniversalTime().ToString("o")
    Save-Receipt -Status "succeeded" -Stage "healthy" -Message "Metronome is healthy at the requested commit."
    exit 0
} catch {
    $failure = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
    $rollbackSucceeded = $false
    $rollbackMessage = $null

    if ($MutationStarted -and $CodeBackup -and $DatabaseBackup) {
        try {
            try {
                Save-Receipt -Status "running" -Stage "rolling_back" -Message "Update failed; restoring code, database, and prior service states."
            } catch {
                # Receipt I/O must never prevent rollback from running.
            }
            Restore-PreviousInstallation
            $rollbackSucceeded = $true
            $rollbackMessage = " Previous installation restored and healthy."
        } catch {
            $rollbackMessage = " Rollback also failed: $($_.Exception.GetType().Name): $($_.Exception.Message)"
        }
    }

    $Receipt["finished_at"] = (Get-Date).ToUniversalTime().ToString("o")
    $status = if ($rollbackSucceeded) { "rolled_back" } else { "failed" }
    Save-Receipt -Status $status -Stage $(if ($rollbackSucceeded) { "rolled_back" } else { "failed" }) `
        -Message ($failure + $rollbackMessage) -RollbackSucceeded $rollbackSucceeded `
        -ErrorDetail ($failure + $rollbackMessage)
    Write-Error ($failure + $rollbackMessage)
    exit 1
} finally {
    if ($TerminalReceiptWritten -and $AttemptId -and $MutexAcquired) {
        try {
            Invoke-SafeUpdateRetention -CurrentAttemptId $AttemptId
        } catch {
            # Retention is maintenance only. It must never rewrite the terminal
            # receipt or change the update's success/rollback exit result.
            Write-Warning "Update retention skipped: $($_.Exception.Message)"
        }
    }
    if ($MutexAcquired -and $Mutex) {
        try { $Mutex.ReleaseMutex() } catch {}
    }
    if ($Mutex) { $Mutex.Dispose() }
    if ($TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
    }
}
