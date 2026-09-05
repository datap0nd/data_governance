# MX Analytics - Setup & Update
# Right-click > Run with PowerShell
#
# Does everything: downloads latest code, installs deps, sets up service.
# Run again any time to update. Auto-elevates to Admin if needed.
#
# This script NEVER deletes files. It extracts new code over the existing
# folder (overwriting updated files). Clean up old files yourself if needed.
#
# Uses a portable Python 3.13 (no system changes) so pbixray works.

param(
    [switch]$Unattended,
    [ValidateRange(0,5)][int]$FlowHeadlessSlots = 0, # 0 reads the saved setting
    [ValidateRange(0,5)][int]$FlowHeadedSlots = 0
)

# --- Self-elevate to Admin if needed ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $ElevationArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if (-not $Unattended) { $ElevationArguments = "-NoExit $ElevationArguments" }
    if ($Unattended) { $ElevationArguments += " -Unattended" }
    if ($FlowHeadlessSlots) { $ElevationArguments += " -FlowHeadlessSlots $FlowHeadlessSlots" }
    if ($FlowHeadedSlots) { $ElevationArguments += " -FlowHeadedSlots $FlowHeadedSlots" }
    Start-Process powershell.exe $ElevationArguments -Verb RunAs -WindowStyle Hidden
    exit
}

$ErrorActionPreference = "Stop"
trap {
    Write-Host ""
    Write-Host "SETUP FAILED: $_" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    if (-not $Unattended) { pause }
    exit 1
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Invoke-WebRequestWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [hashtable]$Headers = @{},
        [int]$MaxAttempts = 10,
        [int]$DelaySeconds = 5
    )
    $lastMessage = $null
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            Write-Host "  Download attempt $attempt of $MaxAttempts..." -ForegroundColor DarkGray
            Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -Headers $Headers
            return
        } catch {
            $lastMessage = $_.Exception.Message
            Write-Host "  Attempt $attempt failed: $lastMessage" -ForegroundColor Yellow
            if ($attempt -lt $MaxAttempts) {
                Write-Host "  Corporate proxy may be transient. Retrying in $DelaySeconds seconds..." -ForegroundColor DarkGray
                Start-Sleep -Seconds $DelaySeconds
            }
        }
    }
    throw "Download failed after $MaxAttempts attempts. Last error: $lastMessage"
}

$ServiceName = "MXAnalytics"
$FlowServiceName = "MXFlowsWorker"
$HeadedFlowTaskName = "Metronome_Flows_Headed"
$AutoUpdateTaskName = "Metronome_Auto_Update"
$CodeDir     = $PSScriptRoot
$ProjectDir  = Split-Path $CodeDir
$DbPath      = "$ProjectDir\governance.db"
$FlowsRoot = if ($env:DG_FLOWS_ROOT) { $env:DG_FLOWS_ROOT } else { Join-Path $ProjectDir 'metronome\flows' }
foreach ($FlowSourceFolder in @('ASAP', 'GSCM', 'Outlook', 'Local', 'Web')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $FlowsRoot $FlowSourceFolder) | Out-Null
}
$ReportsPath = "\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals"
$ScriptsPath = "\\MX-SHARE\Users\METOMX\Desktop;\\MX-SHARE\Users\meto.mx\Desktop;\\METO-MX02\Users\METOMX\Desktop"
$Port        = 8000
$Repository  = "datap0nd/data_governance"
$GitHubToken = $env:DG_GITHUB_TOKEN
$LatestSha   = $null
if ($env:DG_UPDATE_COMMIT_SHA) {
    $LatestSha = "$($env:DG_UPDATE_COMMIT_SHA)".Trim().ToLowerInvariant()
    if ($LatestSha -notmatch '^[0-9a-f]{40}$') {
        throw "DG_UPDATE_COMMIT_SHA must contain one exact 40-character Git commit"
    }
} elseif (-not $env:DG_UPDATE_ZIP_URL) {
    $ShaHeaders = @{
        "User-Agent" = "Metronome-Setup"
        "Accept" = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    if ($GitHubToken) { $ShaHeaders["Authorization"] = "Bearer $GitHubToken" }
    $LatestError = $null
    for ($attempt = 1; $attempt -le 3 -and -not $LatestSha; $attempt++) {
        try {
            $LatestSha = (Invoke-RestMethod `
                -Uri "https://api.github.com/repos/$Repository/commits/main" `
                -Headers $ShaHeaders -TimeoutSec 30).sha
            $LatestSha = "$LatestSha".Trim().ToLowerInvariant()
            if ($LatestSha -notmatch '^[0-9a-f]{40}$') {
                throw "GitHub returned an invalid main commit"
            }
        } catch {
            $LatestSha = $null
            $LatestError = $_.Exception.Message
            if ($attempt -lt 3) { Start-Sleep -Seconds ($attempt * 2) }
        }
    }
    if ($LatestSha) {
        Write-Host "  Latest GitHub main commit: $LatestSha" -ForegroundColor DarkGray
    } else {
        Write-Host "  WARNING: Could not resolve GitHub main: $LatestError" -ForegroundColor Yellow
        Write-Host "  The branch download will use a unique cache-busting URL." -ForegroundColor Yellow
    }
}
$CacheBuster = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$ZipUrl      = if ($GitHubToken) {
    if ($LatestSha) { "https://api.github.com/repos/$Repository/zipball/$LatestSha" }
    else            { "https://api.github.com/repos/$Repository/zipball/main?nocache=$CacheBuster" }
} else {
    if ($LatestSha) { "https://github.com/$Repository/archive/$LatestSha.zip" }
    else            { "https://github.com/$Repository/archive/refs/heads/main.zip?nocache=$CacheBuster" }
}
if ($env:DG_UPDATE_ZIP_URL) {
    $ZipUrl = $env:DG_UPDATE_ZIP_URL
}
$ZipHeaders = @{ "User-Agent" = "Metronome-Setup" }
if ($GitHubToken) {
    $ZipHeaders["Authorization"] = "Bearer $GitHubToken"
    $ZipHeaders["Accept"] = "application/vnd.github+json"
    $ZipHeaders["User-Agent"] = "Metronome-Setup"
}
$ZipPath     = "$ProjectDir\_update.zip"
$PyDir       = "$ProjectDir\python313"
$PyExe       = "$PyDir\python.exe"

# The copy of this script that is running is the PREVIOUS version - the update
# it downloads replaces setup.ps1 on disk, and the new one is relaunched after
# extraction. Capture the running copy's hash to detect that replacement.
$SetupScriptPath = Join-Path $CodeDir "setup.ps1"
$SetupHashBefore = ""
try { $SetupHashBefore = (Get-FileHash $SetupScriptPath -Algorithm SHA256).Hash } catch {}
$InstalledVersion = "unknown"
try { $InstalledVersion = (Get-Content "$CodeDir\VERSION" -ErrorAction Stop | Select-Object -First 1).Trim() } catch {}
Write-Host "Currently installed version: $InstalledVersion" -ForegroundColor Cyan
$FlowProfile = "$env:USERPROFILE\.metronome-flow-browser"
$HeadedFlowProfile = "$env:USERPROFILE\.metronome-flow-browser-headed"
$PyZipUrl    = "https://www.python.org/ftp/python/3.13.2/python-3.13.2-embed-amd64.zip"

# --- Safety check ---
if (-not (Test-Path "$CodeDir\app\main.py")) {
    Write-Host "ERROR: Run this from inside the data_governance-main folder." -ForegroundColor Red
    if (-not $Unattended) { pause }
    exit 1
}

Write-Host ""
Write-Host "MX Analytics Setup" -ForegroundColor Cyan
Write-Host "==================" -ForegroundColor Cyan
Write-Host "  Code dir:  $CodeDir" -ForegroundColor DarkGray
Write-Host "  Database:  $DbPath" -ForegroundColor DarkGray
if (Test-Path $DbPath) {
    $dbSize = [math]::Round((Get-Item $DbPath).Length / 1024)
    Write-Host "  DB exists: ${dbSize} KB (will be preserved)" -ForegroundColor Green
} else {
    Write-Host "  DB: new (will be created on first run)" -ForegroundColor Yellow
}

# --- Portable Python 3.13 ---
if (-not (Test-Path $PyExe)) {
    Write-Host "[1/5] Downloading portable Python 3.13..." -ForegroundColor Yellow
    $PyZipPath = "$ProjectDir\_python.zip"
    try {
        Invoke-WebRequest -Uri $PyZipUrl -OutFile $PyZipPath -UseBasicParsing
    } catch {
        if ($Unattended) {
            throw "Portable Python download failed during unattended setup: $($_.Exception.Message)"
        }
        Write-Host "  Direct download failed, trying Edge..." -ForegroundColor Yellow
        Start-Process "msedge" $PyZipUrl
        $timeout = 120
        $elapsed = 0
        $BrowserPyZip = "$env:USERPROFILE\Downloads\python-3.13.2-embed-amd64.zip"
        while ($true) {
            Start-Sleep -Seconds 3
            $elapsed += 3
            if ((Test-Path $BrowserPyZip) -and -not (Test-Path "$BrowserPyZip.partial")) {
                Start-Sleep -Seconds 1
                Move-Item $BrowserPyZip $PyZipPath -Force
                break
            }
            if ($elapsed -ge $timeout) {
                Write-Host "  Timed out. Download Python manually from:" -ForegroundColor Red
                Write-Host "  $PyZipUrl" -ForegroundColor White
                Write-Host "  Extract to: $PyDir" -ForegroundColor White
                if (-not $Unattended) { pause }
                exit 1
            }
        }
    }
    New-Item -ItemType Directory -Path $PyDir -Force | Out-Null
    Expand-Archive -Path $PyZipPath -DestinationPath $PyDir -Force

    # Enable pip: uncomment "import site" in python313._pth
    $pthFile = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
    if ($pthFile) {
        $content = Get-Content $pthFile.FullName
        $content = $content -replace '^#\s*import site', 'import site'
        Set-Content $pthFile.FullName $content
    }

    # Bootstrap pip
    Write-Host "  Installing pip..." -ForegroundColor DarkGray
    $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
    $getPipPath = "$PyDir\get-pip.py"
    try {
        Invoke-WebRequest -Uri $getPipUrl -OutFile $getPipPath -UseBasicParsing
    } catch {
        Write-Host "  Could not download get-pip.py" -ForegroundColor Red
        if (-not $Unattended) { pause }
        exit 1
    }
    & $PyExe $getPipPath --no-warn-script-location -q
    Write-Host "  Portable Python 3.13 ready." -ForegroundColor Green
} else {
    Write-Host "[1/5] Portable Python 3.13 already installed." -ForegroundColor DarkGray
}
Write-Host "  Python:   $PyExe" -ForegroundColor DarkGray

# --- Download latest code ---
Write-Host "[3/5] Downloading latest version..." -ForegroundColor Yellow

try {
    Invoke-WebRequestWithRetry -Uri $ZipUrl -OutFile $ZipPath -Headers $ZipHeaders
    Write-Host "  Downloaded via PowerShell." -ForegroundColor Green
} catch {
    Write-Host "  Direct download failed after 10 attempts: $_" -ForegroundColor Yellow
    if ($GitHubToken) {
        Write-Host "  Check that DG_GITHUB_TOKEN has read access to this private repo." -ForegroundColor Red
        Write-Host "  Required GitHub permission: Contents = Read." -ForegroundColor Red
        if (-not $Unattended) { pause }
        exit 1
    }
    if ($Unattended) {
        throw "GitHub archive download failed during unattended setup: $($_.Exception.Message)"
    }
    Write-Host "  Trying via Edge..." -ForegroundColor Yellow

    $BrowserZip = "$env:USERPROFILE\Downloads\data_governance-main.zip"

    Start-Process "msedge" $ZipUrl
    $timeout = 300
    $elapsed = 0
    while ($true) {
        Start-Sleep -Seconds 3
        $elapsed += 3
        if ((Test-Path $BrowserZip) -and -not (Test-Path "$BrowserZip.partial")) {
            Start-Sleep -Seconds 1
            break
        }
        if ($elapsed -ge $timeout) {
            Write-Host "  Timed out waiting for download." -ForegroundColor Red
            if (-not $Unattended) { pause }
            exit 1
        }
        if ($elapsed % 15 -eq 0) {
            Write-Host "  Waiting for download... ($elapsed s)" -ForegroundColor DarkGray
        }
    }
    Move-Item $BrowserZip $ZipPath -Force
    Write-Host "  Downloaded via Edge." -ForegroundColor Green
}

# Stop runtimes only after the complete update archive is available. A proxy,
# authentication, or download failure must leave the currently installed app
# and workers running.
Stop-ScheduledTask -TaskName $HeadedFlowTaskName -ErrorAction SilentlyContinue
foreach ($HeadedSlot in 2..5) {
    Stop-ScheduledTask -TaskName "$HeadedFlowTaskName$HeadedSlot" -ErrorAction SilentlyContinue
}
$NssmExe = "$CodeDir\tools\nssm.exe"
$ErrorActionPreference = "Continue"
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$HadExistingService = [bool]$existing
if ($existing) {
    Write-Host "[2/5] Stopping service..." -ForegroundColor Yellow
    & $NssmExe stop $ServiceName 2>&1 | Out-Null
    try {
        $existing.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Stopped,
            [TimeSpan]::FromSeconds(30)
        )
    } catch {
        throw "Metronome did not stop within 30 seconds. Update aborted before replacing code."
    }
} else {
    Write-Host "[2/5] No existing service." -ForegroundColor DarkGray
}
$existingFlowService = Get-Service -Name $FlowServiceName -ErrorAction SilentlyContinue
if ($existingFlowService) {
    & $NssmExe stop $FlowServiceName 2>&1 | Out-Null
    try {
        $existingFlowService.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Stopped,
            [TimeSpan]::FromSeconds(30)
        )
        Write-Host "  Flows worker stopped." -ForegroundColor Green
    } catch {
        throw "Flows worker did not stop within 30 seconds. Update aborted before replacing code."
    }
}

# Quiesce every installed extra slot, including slots above today's capacity.
# This runs before replacing code, for manual and unattended updates alike.
foreach ($PoolSlot in 2..5) {
    $PoolServiceName = "MXFlowsWorker$PoolSlot"
    $PoolService = Get-Service -Name $PoolServiceName -ErrorAction SilentlyContinue
    if ($PoolService) {
        & $NssmExe stop $PoolServiceName 2>&1 | Out-Null
        try { $PoolService.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Stopped, [TimeSpan]::FromSeconds(30)) }
        catch { throw "Flow slot $PoolSlot did not stop. Update aborted before replacing code." }
    }
}

# Kill any orphaned worker or automation browser still holding a flow profile.
# A leftover process keeps the profile's .worker.lock, so every new service
# instance exits with "already running" and the worker never registers.
$FlowProcs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    ($_.CommandLine -match 'flow_worker\.py') -or
    ($_.CommandLine -match '\.metronome-flow-browser')
})
foreach ($proc in $FlowProcs) {
    Write-Host "  Killing leftover flow process PID $($proc.ProcessId): $($proc.Name)" -ForegroundColor Yellow
    $KillProcess = Start-Process taskkill.exe -ArgumentList @("/PID", $proc.ProcessId, "/T", "/F") -PassThru -WindowStyle Hidden
    if (-not $KillProcess.WaitForExit(10000)) {
        Stop-Process -Id $KillProcess.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  WARNING: Timed out waiting for taskkill on PID $($proc.ProcessId). Continuing setup." -ForegroundColor Yellow
    }
}

# Kill anything still holding the port
$portPid = (netstat -ano | Select-String ":$Port\s" | ForEach-Object {
    ($_ -split '\s+')[-1]
} | Where-Object { $_ -match '^\d+$' } | Select-Object -Unique)
foreach ($p in $portPid) {
    if ($p -and $p -ne "0") {
        Write-Host "  Killing PID $p holding port $Port" -ForegroundColor Yellow
        $KillProcess = Start-Process taskkill.exe -ArgumentList @("/PID", $p, "/F") -PassThru -WindowStyle Hidden
        if (-not $KillProcess.WaitForExit(10000)) {
            Stop-Process -Id $KillProcess.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  WARNING: Timed out waiting for taskkill on PID $p. Continuing setup." -ForegroundColor Yellow
        }
    }
}
$ErrorActionPreference = "Stop"

# --- Extract new code over existing folder (no deletion) ---
Write-Host "[4/5] Extracting update over existing code..." -ForegroundColor Yellow

# Extract to a temp folder first, then copy contents over
$TempExtract = "$ProjectDir\_extract_temp"
Expand-Archive -Path $ZipPath -DestinationPath $TempExtract -Force

# GitHub ZIP has a top-level folder (data_governance-main/) - copy its contents into $CodeDir
$Inner = Get-ChildItem $TempExtract -Directory | Select-Object -First 1
if ($Inner) {
    # Copy-Item does not reliably merge new files into existing nested
    # directories on Windows PowerShell 5. Robocopy /E performs a true merge
    # without /MIR or /PURGE, so it never deletes installed/local files.
    & robocopy.exe $Inner.FullName $CodeDir /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "Update file merge failed with robocopy exit code $LASTEXITCODE"
    }
    Remove-Item $TempExtract -Recurse -Force
}
Write-Host "  Files updated in: $CodeDir" -ForegroundColor Green

# --- Relaunch the freshly downloaded setup.ps1 if it changed ---
# Without this, fixes to setup itself (sign-in bootstrap, service handling)
# only take effect on the NEXT update - the run that downloaded them still
# executes the old logic to the end.
if (-not $env:DG_SETUP_RELAUNCHED) {
    $SetupHashAfter = ""
    try { $SetupHashAfter = (Get-FileHash $SetupScriptPath -Algorithm SHA256).Hash } catch {}
    if ($SetupHashAfter -and $SetupHashAfter -ne $SetupHashBefore) {
        Write-Host ""
        Write-Host "setup.ps1 itself was updated. Relaunching the new version..." -ForegroundColor Yellow
        $env:DG_SETUP_RELAUNCHED = "1"
        $RelaunchArguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $SetupScriptPath)
        if ($Unattended) { $RelaunchArguments += "-Unattended" }
        if ($FlowHeadlessSlots) { $RelaunchArguments += @('-FlowHeadlessSlots', "$FlowHeadlessSlots") }
        if ($FlowHeadedSlots) { $RelaunchArguments += @('-FlowHeadedSlots', "$FlowHeadedSlots") }
        & powershell.exe @RelaunchArguments
        exit $LASTEXITCODE
    }
}

# Publish versioned external flow transformations to the shared location used
# by BI desktop workers. A temporarily unavailable share must not prevent the
# application itself from updating; the affected flow will fail closed before
# SQL if its configured script is unavailable.
$BundledFlowTransforms = "$CodeDir\transforms"
$SharedFlowTransforms = "\\MX-SHARE\Users\METOMX\Desktop\Metronome\flow_scripts"
if (Test-Path $BundledFlowTransforms) {
    try {
        New-Item -ItemType Directory -Path $SharedFlowTransforms -Force | Out-Null
        Copy-Item -Path "$BundledFlowTransforms\*" -Destination $SharedFlowTransforms -Force
        Write-Host "  Flow transformations published to: $SharedFlowTransforms" -ForegroundColor Green
    } catch {
        Write-Host "  WARNING: Could not publish flow transformations to MX Share: $_" -ForegroundColor Yellow
    }
}

# --- Stamp VERSION with download timestamp and the deployed commit ---
# GitHub archive zips carry the exact commit SHA as the zip archive comment,
# so the stamp names the code revision, not just when it was downloaded.
$ver = (Get-Date -Format "yyyyMMdd-HHmmss")
$CommitSha = ""
try {
    $CommitSha = (& $PyExe -c "import zipfile; print(zipfile.ZipFile(r'$ZipPath').comment.decode()[:9])").Trim()
} catch {}
if (-not $CommitSha -and $Inner -and $Inner.Name -match '-([0-9a-f]{7,40})$') {
    $CommitSha = $Matches[1].Substring(0, [Math]::Min(9, $Matches[1].Length))
}
if ($CommitSha) { $ver = "$ver-$CommitSha" }
Set-Content "$CodeDir\VERSION" $ver
Write-Host "  Deployed version: $ver" -ForegroundColor Cyan

# --- Install dependencies ---
Write-Host "[5/5] Installing dependencies..." -ForegroundColor Yellow
Set-Location $CodeDir
$PipExe = "$PyDir\Scripts\pip.exe"
# Install bundled wheels first (pbixray + xpress9 + kaitaistruct, no network)
& $PipExe install --no-index --find-links vendor pbixray xpress9 kaitaistruct -q
if ($LASTEXITCODE -ne 0) {
    throw "Bundled Python dependency installation failed with exit code $LASTEXITCODE"
}
# reverse_geocoder is published as a source distribution. Ensure its legacy
# setuptools backend exists in the portable runtime, then reuse that backend
# instead of an isolated build environment that cannot import it.
& $PipExe install --upgrade setuptools wheel -q
if ($LASTEXITCODE -ne 0) {
    throw "Python build dependency installation failed with exit code $LASTEXITCODE"
}
# Install remaining deps from public PyPI (portable Python has clean config)
& $PipExe install --no-build-isolation -r requirements.txt -q
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency installation failed with exit code $LASTEXITCODE"
}

# Read the persisted capacity after the new code and Python are available.
. (Join-Path $CodeDir 'tools\flow_pool.ps1')
$PoolConfigArguments = @((Join-Path $CodeDir 'tools\flow_pool_config.py'), $DbPath)
if ($FlowHeadlessSlots) { $PoolConfigArguments += @('--capacity', "$FlowHeadlessSlots") }
$ConfiguredFlowSlots = [int](& $PyExe @PoolConfigArguments)
if ($LASTEXITCODE -ne 0 -or $ConfiguredFlowSlots -lt 1 -or $ConfiguredFlowSlots -gt 5) { throw 'Could not read Flow worker capacity.' }
$FlowSlots = @(1..$ConfiguredFlowSlots | ForEach-Object { Get-MetronomeFlowSlot -Slot $_ -BaseProfile $FlowProfile })
$MissingExtraSlots = @($FlowSlots | Where-Object { $_.Slot -gt 1 -and -not (Get-Service -Name $_.ServiceName -ErrorAction SilentlyContinue) })
$HeadedPoolArguments = @((Join-Path $CodeDir 'tools\flow_pool_config.py'), $DbPath, '--mode', 'headed')
if ($FlowHeadedSlots) { $HeadedPoolArguments += @('--capacity', "$FlowHeadedSlots") }
$ConfiguredHeadedSlots = [int](& $PyExe @HeadedPoolArguments)
if ($LASTEXITCODE -ne 0 -or $ConfiguredHeadedSlots -lt 1 -or $ConfiguredHeadedSlots -gt 5) { throw 'Could not read headed Flow worker capacity.' }
$HeadedSlots = @(1..$ConfiguredHeadedSlots | ForEach-Object { Get-MetronomeFlowSlot -Slot $_ -BaseProfile $HeadedFlowProfile -Headed })

# --- Create and start service ---
Write-Host "Starting service..." -ForegroundColor Yellow
$NssmExe = "$CodeDir\tools\nssm.exe"

if (-not $HadExistingService) {
    & $NssmExe install $ServiceName $PyExe "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
}
& $NssmExe set $ServiceName Application $PyExe
& $NssmExe set $ServiceName AppParameters "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
& $NssmExe set $ServiceName AppDirectory $CodeDir
& $NssmExe set $ServiceName DisplayName "MX Analytics - Data Governance"
& $NssmExe set $ServiceName Description "BI data governance panel"
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

& $NssmExe set $ServiceName AppEnvironmentExtra `
    "DG_DB_PATH=$DbPath" `
    "DG_FLOWS_ROOT=$FlowsRoot" `
    "DG_REPORTS_PATH=$ReportsPath" `
    "DG_SCRIPTS_PATH=$ScriptsPath" `
    "DG_SCHTASK_REMOTES=MX-Share" `
    "DG_PBI_WORKSPACE=mx executive" `
    "DG_PBI_SYNC_WINDOWS_USER=$env:USERNAME" `
    "METRONOME_FLOW_PROFILE=$FlowProfile" `
    "DG_AI_MOCK=true"

# Run services as the current user (needed for network share access). Normal
# updates preserve the installed credentials so unattended setup never prompts.
$ExistingAutoUpdateTask = Get-ScheduledTask -TaskName $AutoUpdateTaskName -ErrorAction SilentlyContinue
$NeedsServiceCredential = (-not $HadExistingService) -or (-not $existingFlowService) -or (-not $ExistingAutoUpdateTask)
if (-not $Unattended -and $MissingExtraSlots.Count) { $NeedsServiceCredential = $true }
$ServicePassword = $null
$SetServiceCredentials = $false
if ($env:DG_SVC_PASSWORD) {
    $ServicePassword = $env:DG_SVC_PASSWORD
    $SetServiceCredentials = $true
} elseif ($NeedsServiceCredential) {
    if ($Unattended) {
        throw "Unattended setup cannot request a Windows password. Run setup.ps1 once manually to register the services and update task."
    }
    $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Enter your Windows ACCOUNT password (not a Windows Hello PIN) for Metronome services"
    $ServicePassword = $cred.GetNetworkCredential().Password
    $SetServiceCredentials = $true
} else {
    Write-Host "  Existing Windows service credentials preserved." -ForegroundColor DarkGray
}
if ($SetServiceCredentials) {
    & $NssmExe set $ServiceName ObjectName "$env:USERDOMAIN\$env:USERNAME" $ServicePassword
}

& $NssmExe set $ServiceName AppExit Default Restart
& $NssmExe set $ServiceName AppRestartDelay 5000

$LogDir = "$ProjectDir\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
& $NssmExe set $ServiceName AppStdout "$LogDir\mx_analytics.log"
& $NssmExe set $ServiceName AppStderr "$LogDir\mx_analytics_error.log"
& $NssmExe set $ServiceName AppStdoutCreationDisposition 4
& $NssmExe set $ServiceName AppStderrCreationDisposition 4
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateSeconds 86400
& $NssmExe set $ServiceName AppRotateBytes 10485760

# Headless flows use a background service. Headed flows use a separate,
# on-demand interactive task so the selected browser appears in the signed-in BI desktop.
# Separate browser profiles prevent Chromium profile contention. Both workers
# read the same account-scoped DPAPI credential stored outside source control.
if (-not $existingFlowService) {
    & $NssmExe install $FlowServiceName $PyExe "`"$CodeDir\app\flow_worker.py`" --server http://127.0.0.1:$Port --worker-id bi-desktop-headless --name BI-desktop-headless --profile-dir `"$FlowProfile`""
}
& $NssmExe set $FlowServiceName Application $PyExe
& $NssmExe set $FlowServiceName AppParameters "`"$CodeDir\app\flow_worker.py`" --server http://127.0.0.1:$Port --worker-id bi-desktop-headless --name BI-desktop-headless --profile-dir `"$FlowProfile`""
& $NssmExe set $FlowServiceName AppDirectory $CodeDir
& $NssmExe set $FlowServiceName DisplayName "Metronome - Flows Worker"
& $NssmExe set $FlowServiceName Description "Headless authenticated ASAP discovery and download worker"
& $NssmExe set $FlowServiceName Start SERVICE_AUTO_START
if ($SetServiceCredentials) {
    & $NssmExe set $FlowServiceName ObjectName "$env:USERDOMAIN\$env:USERNAME" $ServicePassword
}
& $NssmExe set $FlowServiceName AppExit Default Restart
& $NssmExe set $FlowServiceName AppRestartDelay 10000
& $NssmExe set $FlowServiceName AppStdout "$LogDir\flow_worker.log"
& $NssmExe set $FlowServiceName AppStderr "$LogDir\flow_worker_error.log"
& $NssmExe set $FlowServiceName AppStdoutCreationDisposition 4
& $NssmExe set $FlowServiceName AppStderrCreationDisposition 4
& $NssmExe set $FlowServiceName AppRotateFiles 1
& $NssmExe set $FlowServiceName AppRotateSeconds 86400
& $NssmExe set $FlowServiceName AppRotateBytes 10485760

# Slot 1 keeps its service, worker ID, profile and logs for historic recovery.
# New slots require explicit account credentials from interactive setup. An
# unattended update preserves installed slots and reports missing ones offline.
foreach ($FlowSlot in $FlowSlots | Where-Object { $_.Slot -gt 1 }) {
    $SlotService = $FlowSlot.ServiceName
    $InstalledSlot = Get-Service -Name $SlotService -ErrorAction SilentlyContinue
    if (-not $InstalledSlot -and -not $SetServiceCredentials) {
        Write-Host "  Slot $($FlowSlot.Slot) is configured but not installed. Run setup.ps1 manually to enroll its service account." -ForegroundColor Yellow
        continue
    }
    $SlotArguments = "`"$CodeDir\app\flow_worker.py`" --server http://127.0.0.1:$Port --worker-id $($FlowSlot.WorkerId) --name $($FlowSlot.WorkerId) --profile-dir `"$($FlowSlot.Profile)`""
    if (-not $InstalledSlot) { & $NssmExe install $SlotService $PyExe $SlotArguments }
    & $NssmExe set $SlotService Application $PyExe
    & $NssmExe set $SlotService AppParameters $SlotArguments
    & $NssmExe set $SlotService AppDirectory $CodeDir
    & $NssmExe set $SlotService DisplayName "Metronome - Flows Worker $($FlowSlot.Slot)"
    & $NssmExe set $SlotService Start SERVICE_AUTO_START
    & $NssmExe set $SlotService AppEnvironmentExtra "METRONOME_FLOW_PROFILE=$FlowProfile"
    if ($SetServiceCredentials) { & $NssmExe set $SlotService ObjectName "$env:USERDOMAIN\$env:USERNAME" $ServicePassword }
    & $NssmExe set $SlotService AppExit Default Restart
    & $NssmExe set $SlotService AppRestartDelay 10000
    & $NssmExe set $SlotService AppStdout "$LogDir\$($FlowSlot.LogName).log"
    & $NssmExe set $SlotService AppStderr "$LogDir\$($FlowSlot.LogName)_error.log"
    & $NssmExe set $SlotService AppRotateFiles 1
    & $NssmExe set $SlotService AppRotateSeconds 86400
    & $NssmExe set $SlotService AppRotateBytes 10485760
}
# Preserve unused services and profiles; don't auto-start them after an update.
foreach ($DisabledSlot in 1..5 | Where-Object { $_ -gt $ConfiguredFlowSlots }) {
    $DisabledService = (Get-MetronomeFlowSlot -Slot $DisabledSlot -BaseProfile $FlowProfile).ServiceName
    if (Get-Service -Name $DisabledService -ErrorAction SilentlyContinue) { & $NssmExe set $DisabledService Start SERVICE_DEMAND_START }
}

# Interactive tasks do not need a stored password. Enroll all five so changing
# visible capacity later takes effect immediately, without another setup run.
$ExistingHeadedTask = Get-ScheduledTask -TaskName $HeadedFlowTaskName -ErrorAction SilentlyContinue
$HeadedTaskPrincipal = if ($ExistingHeadedTask) { $ExistingHeadedTask.Principal } else {
    New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
}
$HeadedTaskSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 3) -MultipleInstances IgnoreNew
foreach ($VisibleSlot in 1..5 | ForEach-Object { Get-MetronomeFlowSlot -Slot $_ -BaseProfile $HeadedFlowProfile -Headed }) {
    $HeadedTaskArguments = "`"$CodeDir\app\flow_worker.py`" --server http://127.0.0.1:$Port --worker-id $($VisibleSlot.WorkerId) --name $($VisibleSlot.WorkerId) --profile-dir `"$($VisibleSlot.Profile)`" --headed --idle-exit-seconds 60"
    $HeadedTaskAction = New-ScheduledTaskAction -Execute $PyExe -Argument $HeadedTaskArguments -WorkingDirectory $CodeDir
    Register-ScheduledTask -TaskName $VisibleSlot.TaskName -Action $HeadedTaskAction `
        -Principal $HeadedTaskPrincipal -Settings $HeadedTaskSettings -Force | Out-Null
}
Write-Host "  Five headed Flows tasks registered; $ConfiguredHeadedSlots visible slots enabled on demand." -ForegroundColor Green

# Register one fixed, non-interactive bridge while setup already has the
# service account credential. The web app writes an exact-SHA request and
# starts this task; the bridge runs this same setup.ps1 with -Unattended.
$AutoUpdateScript = Join-Path $CodeDir "tools\apply_update.ps1"
$AutoUpdateRoot = Join-Path $ProjectDir "updates"
$AutoUpdateRequest = Join-Path $AutoUpdateRoot "pending_update.json"
if (-not (Test-Path $AutoUpdateScript -PathType Leaf)) {
    throw "Unattended update worker is missing: $AutoUpdateScript"
}
New-Item -ItemType Directory -Path $AutoUpdateRoot -Force | Out-Null
$AutoUpdatePowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$AutoUpdateArguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$AutoUpdateScript`" -RequestPath `"$AutoUpdateRequest`""
$AutoUpdateAction = New-ScheduledTaskAction -Execute $AutoUpdatePowerShell -Argument $AutoUpdateArguments
$AutoUpdateSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
if ($ServicePassword) {
    Register-ScheduledTask -TaskName $AutoUpdateTaskName -Action $AutoUpdateAction `
        -Settings $AutoUpdateSettings -Description "Run setup.ps1 for the exact GitHub main commit detected by Metronome" `
        -User "$env:USERDOMAIN\$env:USERNAME" -Password $ServicePassword -RunLevel Highest -Force | Out-Null
    Write-Host "  Unattended auto-update task registered." -ForegroundColor Green
} elseif ($ExistingAutoUpdateTask) {
    Set-ScheduledTask -TaskName $AutoUpdateTaskName -Action $AutoUpdateAction `
        -Settings $AutoUpdateSettings | Out-Null
    Write-Host "  Existing unattended auto-update task preserved." -ForegroundColor DarkGray
} else {
    throw "The unattended auto-update task needs the Windows account password during first setup."
}

if (Test-Path "$CodeDir\tools\install_rdp_console_guard.ps1") {
    Write-Host "Installing RDP console guard..." -ForegroundColor Yellow
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$CodeDir\tools\install_rdp_console_guard.ps1" -TargetUser $env:USERNAME
        if ($LASTEXITCODE -ne 0) {
            throw "installer exited with code $LASTEXITCODE"
        }
    } catch {
        Write-Host "  RDP console guard install failed: $_" -ForegroundColor Yellow
        Write-Host "  The app will still start, but interactive sync may fail after RDP disconnects." -ForegroundColor Yellow
    }
}

# Bootstrap the dedicated automation browser profile once. The website URL is
# read from this machine's SQLite configuration and is never stored in source.
# Do not block setup on a visible login. Once the local DPAPI credential is
# enrolled in Flows, this helper can validate/refresh the browser session
# without user interaction. Otherwise the worker reports a clear UI action.
if (-not $Unattended) {
$FlowCredentialPath = Join-Path $FlowProfile ".asap_credentials"
if ((Test-Path $DbPath) -and (Test-Path $FlowCredentialPath)) {
    # Create any portal the new code registers before reading the site list.
    # Migrations otherwise run when the service starts, which is after this
    # point, so a newly shipped portal would be missing here and its one-time
    # sign-in silently skipped.
    $MigrateScript = "$CodeDir\tools\apply_migrations.py"
    if (Test-Path $MigrateScript) {
        try { & $PyExe $MigrateScript $DbPath | Out-Null }
        catch { Write-Host "  WARNING: could not apply migrations before sign-in: $_" -ForegroundColor Yellow }
    }
    $AuthUrlScript = "$CodeDir\tools\get_flow_auth_url.py"
    if (Test-Path $AuthUrlScript) {
        # One line per portal: "adapter<TAB>url". ASAP and GSCM are separate
        # websites and each needs its own sign-in against this profile.
        $FlowAuthTargets = @(& $PyExe $AuthUrlScript $DbPath) | Where-Object { $_ -and $_.Trim() }
        foreach ($FlowAuthTarget in $FlowAuthTargets) {
            $Parts = $FlowAuthTarget.Trim() -split "`t", 2
            if ($Parts.Count -ne 2) { continue }
            $FlowAuthAdapter = $Parts[0].Trim()
            $FlowAuthUrl = $Parts[1].Trim()
            if (-not $FlowAuthUrl) { continue }
            $PortalLabel = if ($FlowAuthAdapter -eq "gscm_portal") { "GSCM" } else { "ASAP" }
            # Headless and headed flows use SEPARATE browser profiles (they
            # cannot share a Chromium profile concurrently), and Samsung SSO
            # sessions live per profile. Signing in only the headless profile
            # left every headed run parked on the SSO form, so bootstrap both.
            $AuthenticationProfiles = @(
                @{ Path = $FlowProfile;       Label = "headless service" },
                @{ Path = $HeadedFlowProfile; Label = "headed on-demand" }
            ) + @($FlowSlots | Where-Object { $_.Slot -gt 1 } | ForEach-Object { @{ Path = $_.Profile; Label = "headless slot $($_.Slot)" } }) + @($HeadedSlots | Where-Object { $_.Slot -gt 1 } | ForEach-Object { @{ Path = $_.Profile; Label = "headed slot $($_.Slot)" } })
            foreach ($ProfileTarget in $AuthenticationProfiles) {
                Write-Host "Authenticating the $($ProfileTarget.Label) Flows browser for $PortalLabel..." -ForegroundColor Yellow
                Write-Host "  Complete $PortalLabel sign-in in the selected browser window if prompted." -ForegroundColor DarkGray
                try {
                    # The embedded Python runtime runs in isolated mode and can
                    # ignore PYTHONPATH for ``-m app...``. The worker script
                    # bootstraps its package root before importing app modules.
                    & $PyExe "$CodeDir\app\flow_worker.py" `
                        --profile-dir $ProfileTarget.Path `
                        --authenticate-url $FlowAuthUrl `
                        --authenticate-adapter $FlowAuthAdapter `
                        --authentication-timeout-minutes 10
                    if ($LASTEXITCODE -ne 0) {
                        throw "authentication helper exited with code $LASTEXITCODE"
                    }
                    Write-Host "  $($ProfileTarget.Label) Flows browser authenticated for $PortalLabel." -ForegroundColor Green
                } catch {
                    Write-Host "  WARNING: $PortalLabel authentication was not completed for the $($ProfileTarget.Label) profile: $_" -ForegroundColor Yellow
                    Write-Host "  Runs in that mode will wait for sign-in (headed) or fail as not signed in (headless) until setup is rerun." -ForegroundColor Yellow
                }
            }
        }
    }
} elseif (Test-Path $DbPath) {
    Write-Host "ASAP automatic sign-in is not configured yet." -ForegroundColor DarkGray
    Write-Host "  Open Metronome > Flows > Catalog > ASAP and store the encrypted BI-desktop credential once." -ForegroundColor DarkGray
}
} else {
    Write-Host "Flows browser sign-in was left unchanged (unattended update mode)." -ForegroundColor DarkGray
}

# The worker starts before the app on purpose: it retries registration for
# up to 10 minutes while Metronome boots, and this ordering keeps the app's
# own ensure-worker watchdog from winning the race and making our start
# report a scary (but harmless) "An instance of the service is already
# running".
Write-Host "Starting headless Flows worker service..." -ForegroundColor Yellow
$WorkerStartedAt = Get-Date
$WorkerStartOutput = (& $NssmExe start $FlowServiceName 2>&1 | Out-String)
if ($WorkerStartOutput -match 'already running') {
    Write-Host "  Flows worker service was already running - OK." -ForegroundColor DarkGray
}

$ExpectedWorkerIds = @('bi-desktop-headless')
foreach ($FlowSlot in $FlowSlots | Where-Object { $_.Slot -gt 1 }) {
    if (Get-Service -Name $FlowSlot.ServiceName -ErrorAction SilentlyContinue) {
        & $NssmExe start $FlowSlot.ServiceName 2>&1 | Out-Null
        $ExpectedWorkerIds += $FlowSlot.WorkerId
    }
}

& $NssmExe start $ServiceName
Start-Sleep -Seconds 3

$MetronomeHealthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $VersionResponse = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/version" -TimeoutSec 5
        if ($VersionResponse) {
            $MetronomeHealthy = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $MetronomeHealthy -and $Unattended) {
    throw "Metronome failed its localhost health check after unattended setup. Check $LogDir\"
}

function Describe-WorkerRegistrationFailure {
    param($Port, $CodeDir, $LogDir, $WorkerStartedAt, $ExpectedWorkerIds = @('bi-desktop-headless'))
    try {
        $Workers = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/flows/workers" -TimeoutSec 5)
    } catch {
        return "Metronome is not answering on port $Port, so the worker cannot register. Check $LogDir\mx_analytics_error.log."
    }
    $MissingWorkers = @($ExpectedWorkerIds | Where-Object {
        $ExpectedId = $_
        -not @($Workers | Where-Object { $_.worker_id -eq $ExpectedId -and $_.status -ne 'offline' -and $_.last_seen_at -and ([datetime]$_.last_seen_at) -ge $WorkerStartedAt.AddSeconds(-5) }).Count
    })
    if ($MissingWorkers.Count -and $MissingWorkers -notcontains 'bi-desktop-headless') {
        return "Background slots not registered after update: $($MissingWorkers -join ', '). Check their flow_worker2..5_error.log files and System > Flow workers."
    }
    $Row = $Workers | Where-Object { $_.worker_id -eq "bi-desktop-headless" } | Select-Object -First 1
    $LiveWorkers = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match 'flow_worker\.py'
    })
    if (-not $Row) {
        if ($LiveWorkers.Count -gt 0) {
            return "STILL STARTING: the worker process is running (PID $($LiveWorkers[0].ProcessId)) and retries registration for up to 10 minutes while Metronome boots. Verify in the app (Flows) in a few minutes; if it stays offline, check $LogDir\flow_worker_error.log."
        }
        return "The worker never reached the server and no worker process is running. Check $LogDir\flow_worker_error.log for a '.worker.lock' holder or a Python traceback."
    }
    # Freshness FIRST: a dead worker's database row keeps its old
    # code_version forever, so version can only be judged on a live row.
    $RowFresh = $Row.status -ne "offline" -and $Row.last_seen_at -and
        ([datetime]$Row.last_seen_at) -ge $WorkerStartedAt.AddSeconds(-5)
    if (-not $RowFresh) {
        if ($LiveWorkers.Count -gt 0) {
            return "STILL STARTING: the registered row is from before the update, and the new worker process (PID $($LiveWorkers[0].ProcessId)) retries registration for up to 10 minutes while Metronome boots. Verify in the app (Flows) in a few minutes; if it stays offline, check $LogDir\flow_worker_error.log."
        }
        return "The registered row is from before the update (status=$($Row.status), last seen $($Row.last_seen_at)) and no worker process is running. Check $LogDir\flow_worker_error.log for a '.worker.lock' holder or a Python traceback."
    }
    $Expected = (Get-Content "$CodeDir\VERSION" -ErrorAction SilentlyContinue | Select-Object -First 1)
    $RowVersion = $Row.capabilities.code_version
    if ($Expected -and $RowVersion -and ("$RowVersion".Trim() -ne "$Expected".Trim())) {
        return "A LIVE worker running OLD code (version $RowVersion, deployed $Expected) is registered - a leftover process survived the update. Re-run setup.ps1, which kills leftovers, or end PID $($LiveWorkers[0].ProcessId) in Task Manager."
    }
    return "The worker looks registered and current; the poll may simply have raced it. Verify in the app under Flows."
}

    # Poll until the service registers with Metronome. The worker itself
    # retries registration for up to 120 seconds while the app boots, so the
    # poll allows the same window.
    $WorkerOnline = $false
    try {
        $ExistingWorkers = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/flows/workers" -TimeoutSec 5)
        $WorkerOnline = @($ExistingWorkers | Where-Object {
            $ExpectedWorkerIds -contains $_.worker_id -and $_.status -ne "offline" -and
            $_.last_seen_at -and ([datetime]$_.last_seen_at) -ge $WorkerStartedAt.AddSeconds(-5)
        }).Count -eq $ExpectedWorkerIds.Count
    } catch {}
    if (-not $WorkerOnline) {
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            Start-Sleep -Seconds 2
            try {
                $RegisteredWorkers = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/flows/workers" -TimeoutSec 5)
                if (@($RegisteredWorkers | Where-Object {
                    $ExpectedWorkerIds -contains $_.worker_id -and $_.status -ne "offline" -and
                    $_.last_seen_at -and ([datetime]$_.last_seen_at) -ge $WorkerStartedAt.AddSeconds(-5)
                }).Count -eq $ExpectedWorkerIds.Count) {
                    $WorkerOnline = $true
                    break
                }
            } catch {}
        }
    }
    if ($WorkerOnline) {
        Write-Host "  Flows worker registered with Metronome." -ForegroundColor Green
    } else {
        $Reason = Describe-WorkerRegistrationFailure -Port $Port -CodeDir $CodeDir -LogDir $LogDir -WorkerStartedAt $WorkerStartedAt -ExpectedWorkerIds $ExpectedWorkerIds
        if ($Reason -like "STILL STARTING:*") {
            Write-Host "  Flows worker has not registered yet. $Reason" -ForegroundColor DarkYellow
        } else {
            Write-Host "  WARNING: Flows worker did not register. $Reason" -ForegroundColor Yellow
        }
    }

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($MetronomeHealthy -and $svc -and $svc.Status -eq "Running") {
    Write-Host ""
    Write-Host "Done. MX Analytics running at http://localhost:$Port" -ForegroundColor Green
    Write-Host "Deployed version: $ver" -ForegroundColor Cyan
    Write-Host "  Also shown in the web UI top bar and at http://localhost:$Port/api/version" -ForegroundColor DarkGray
    if (-not $Unattended) { Start-Process "http://localhost:$Port" }
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "WARNING: Service not running. Check $LogDir\" -ForegroundColor Red
    Write-Host ""
}
if (-not $Unattended) { pause }
