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

# --- Self-elevate to Admin if needed ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe "-NoExit -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ErrorActionPreference = "Stop"
trap {
    Write-Host ""
    Write-Host "SETUP FAILED: $_" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    pause
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
            Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -Headers $Headers -TimeoutSec 60
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
$CodeDir     = $PSScriptRoot
$ProjectDir  = Split-Path $CodeDir
$DbPath      = "$ProjectDir\governance.db"
$ReportsPath = "\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals"
$ScriptsPath = "\\MX-SHARE\Users\METOMX\Desktop;\\MX-SHARE\Users\meto.mx\Desktop;\\METO-MX02\Users\METOMX\Desktop"
$Port        = 8000
$GitHubToken = $env:DG_GITHUB_TOKEN
$ZipUrl      = if ($GitHubToken) {
    "https://api.github.com/repos/datap0nd/data_governance/zipball/main"
} else {
    "https://github.com/datap0nd/data_governance/archive/refs/heads/main.zip"
}
if ($env:DG_UPDATE_ZIP_URL) {
    $ZipUrl = $env:DG_UPDATE_ZIP_URL
}
$ZipHeaders = @{}
if ($GitHubToken) {
    $ZipHeaders["Authorization"] = "Bearer $GitHubToken"
    $ZipHeaders["Accept"] = "application/vnd.github+json"
    $ZipHeaders["User-Agent"] = "Metronome-Setup"
}
$ZipPath     = "$ProjectDir\_update.zip"
$PyDir       = "$ProjectDir\python313"
$PyExe       = "$PyDir\python.exe"
$FlowProfile = "$env:USERPROFILE\.metronome-flow-browser"
$HeadedFlowProfile = "$env:USERPROFILE\.metronome-flow-browser-headed"
$PyZipUrl    = "https://www.python.org/ftp/python/3.13.2/python-3.13.2-embed-amd64.zip"

# The tracked distribution binary has a different name so routine source
# overlays never try to replace a live, Windows-locked nssm.exe. Install it
# only when this is a new machine and no service wrapper exists yet.
$NssmExe = "$CodeDir\tools\nssm.exe"
$NssmDistribution = "$CodeDir\tools\nssm.dist.exe"
if (-not (Test-Path $NssmExe)) {
    if (-not (Test-Path $NssmDistribution)) {
        throw "NSSM service wrapper distribution is missing: $NssmDistribution"
    }
    Copy-Item $NssmDistribution $NssmExe -Force
}

# --- Safety check ---
if (-not (Test-Path "$CodeDir\app\main.py")) {
    Write-Host "ERROR: Run this from inside the data_governance-main folder." -ForegroundColor Red
    pause
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
                pause
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
        pause
        exit 1
    }
    & $PyExe $getPipPath --no-warn-script-location -q
    Write-Host "  Portable Python 3.13 ready." -ForegroundColor Green
} else {
    Write-Host "[1/5] Portable Python 3.13 already installed." -ForegroundColor DarkGray
}
Write-Host "  Python:   $PyExe" -ForegroundColor DarkGray

# --- Download latest code ---
Write-Host "[2/5] Downloading latest version before stopping services..." -ForegroundColor Yellow

try {
    Invoke-WebRequestWithRetry -Uri $ZipUrl -OutFile $ZipPath -Headers $ZipHeaders
    Write-Host "  Downloaded via PowerShell." -ForegroundColor Green
} catch {
    Write-Host "  Direct download failed after 10 attempts: $_" -ForegroundColor Yellow
    if ($GitHubToken) {
        Write-Host "  Check that DG_GITHUB_TOKEN has read access to this private repo." -ForegroundColor Red
        Write-Host "  Required GitHub permission: Contents = Read." -ForegroundColor Red
        pause
        exit 1
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
            pause
            exit 1
        }
        if ($elapsed % 15 -eq 0) {
            Write-Host "  Waiting for download... ($elapsed s)" -ForegroundColor DarkGray
        }
    }
    Move-Item $BrowserZip $ZipPath -Force
    Write-Host "  Downloaded via Edge." -ForegroundColor Green
}

# Only interrupt the running app after the replacement archive is safely on
# disk. Proxy/download failures above therefore leave Metronome untouched.
Write-Host "[3/5] Stopping services..." -ForegroundColor Yellow
Stop-ScheduledTask -TaskName $HeadedFlowTaskName -ErrorAction SilentlyContinue
$ErrorActionPreference = "Continue"
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    & $NssmExe stop $ServiceName 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
}
$existingFlowService = Get-Service -Name $FlowServiceName -ErrorAction SilentlyContinue
if ($existingFlowService) {
    & $NssmExe stop $FlowServiceName 2>&1 | Out-Null
}

# Kill anything still holding the port.
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
    # NSSM is the service wrapper used by this setup process. Windows can keep
    # the installed executable locked briefly even after both services stop,
    # so a routine source update must not try to overwrite it. The existing
    # installed copy is reused when the services are registered below.
    $DownloadedNssm = Join-Path $Inner.FullName "tools\nssm.exe"
    if ((Test-Path $NssmExe) -and (Test-Path $DownloadedNssm)) {
        Move-Item $DownloadedNssm "$TempExtract\nssm.exe.skipped" -Force
        Write-Host "  Preserving installed tools\nssm.exe (in use by Windows)." -ForegroundColor DarkGray
    }
    # Restore the original updater behavior. Copy-Item overlays the downloaded
    # source tree and never mirrors, purges, or removes installed/local files.
    Copy-Item "$($Inner.FullName)\*" $CodeDir -Recurse -Force
    Remove-Item $TempExtract -Recurse -Force
}
Write-Host "  Files updated in: $CodeDir" -ForegroundColor Green

# --- Stamp VERSION with download timestamp ---
$ver = (Get-Date -Format "yyyyMMdd-HHmmss")
Set-Content "$CodeDir\VERSION" $ver
Write-Host "  Version: $ver" -ForegroundColor DarkGray

# --- Install dependencies ---
Write-Host "[5/5] Installing dependencies..." -ForegroundColor Yellow
Set-Location $CodeDir
$PipExe = "$PyDir\Scripts\pip.exe"
# Install bundled wheels first (pbixray + xpress9 + kaitaistruct, no network)
& $PipExe install --no-index --find-links vendor pbixray xpress9 kaitaistruct -q
# Install remaining deps from public PyPI (portable Python has clean config)
& $PipExe install -r requirements.txt -q

# --- Create and start service ---
Write-Host "Starting service..." -ForegroundColor Yellow
$NssmExe = "$CodeDir\tools\nssm.exe"

& $NssmExe install $ServiceName $PyExe "-m uvicorn app.main:app --host 0.0.0.0 --port $Port"
& $NssmExe set $ServiceName AppDirectory $CodeDir
& $NssmExe set $ServiceName DisplayName "MX Analytics - Data Governance"
& $NssmExe set $ServiceName Description "BI data governance panel"
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

& $NssmExe set $ServiceName AppEnvironmentExtra `
    "DG_DB_PATH=$DbPath" `
    "DG_REPORTS_PATH=$ReportsPath" `
    "DG_SCRIPTS_PATH=$ScriptsPath" `
    "DG_SCHTASK_REMOTES=MX-Share" `
    "DG_PBI_WORKSPACE=mx executive" `
    "DG_PBI_SYNC_WINDOWS_USER=$env:USERNAME" `
    "METRONOME_FLOW_PROFILE=$FlowProfile" `
    "DG_AI_MOCK=true"

# Run service as current user (needed for network share access)
if ($env:DG_SVC_PASSWORD) {
    $ServicePassword = $env:DG_SVC_PASSWORD
} else {
    $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Enter your Windows password so the service can access network shares"
    $ServicePassword = $cred.GetNetworkCredential().Password
}
& $NssmExe set $ServiceName ObjectName "$env:USERDOMAIN\$env:USERNAME" $ServicePassword

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
# on-demand interactive task so Edge appears in the signed-in BI desktop.
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
& $NssmExe set $FlowServiceName ObjectName "$env:USERDOMAIN\$env:USERNAME" $ServicePassword
& $NssmExe set $FlowServiceName AppExit Default Restart
& $NssmExe set $FlowServiceName AppRestartDelay 10000
& $NssmExe set $FlowServiceName AppStdout "$LogDir\flow_worker.log"
& $NssmExe set $FlowServiceName AppStderr "$LogDir\flow_worker_error.log"
& $NssmExe set $FlowServiceName AppStdoutCreationDisposition 4
& $NssmExe set $FlowServiceName AppStderrCreationDisposition 4
& $NssmExe set $FlowServiceName AppRotateFiles 1
& $NssmExe set $FlowServiceName AppRotateSeconds 86400
& $NssmExe set $FlowServiceName AppRotateBytes 10485760

$HeadedTaskArguments = "`"$CodeDir\app\flow_worker.py`" --server http://127.0.0.1:$Port --worker-id bi-desktop-headed --name BI-desktop-headed --profile-dir `"$HeadedFlowProfile`" --headed --idle-exit-seconds 60"
$HeadedTaskAction = New-ScheduledTaskAction -Execute $PyExe -Argument $HeadedTaskArguments
$HeadedTaskPrincipal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$HeadedTaskSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 3) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $HeadedFlowTaskName -Action $HeadedTaskAction `
    -Principal $HeadedTaskPrincipal -Settings $HeadedTaskSettings -Force | Out-Null
Write-Host "  Headed Flows worker registered for on-demand interactive runs." -ForegroundColor Green

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
$FlowCredentialPath = Join-Path $FlowProfile ".asap_credentials"
if ((Test-Path $DbPath) -and (Test-Path $FlowCredentialPath)) {
    $AuthUrlScript = "$CodeDir\tools\get_flow_auth_url.py"
    if (Test-Path $AuthUrlScript) {
        $FlowAuthUrl = (& $PyExe $AuthUrlScript $DbPath).Trim()
        if ($FlowAuthUrl) {
            Write-Host "Authenticating the Flows automation browser..." -ForegroundColor Yellow
            Write-Host "  Complete ASAP sign-in in the Edge window if prompted." -ForegroundColor DarkGray
            try {
                # The embedded Python runtime runs in isolated mode and can
                # ignore PYTHONPATH for ``-m app...``. The worker script
                # bootstraps its package root before importing app modules.
                & $PyExe "$CodeDir\app\flow_worker.py" `
                    --profile-dir $FlowProfile `
                    --authenticate-url $FlowAuthUrl `
                    --authentication-timeout-minutes 10
                if ($LASTEXITCODE -ne 0) {
                    throw "authentication helper exited with code $LASTEXITCODE"
                }
                Write-Host "  Flows automation browser authenticated." -ForegroundColor Green
            } catch {
                Write-Host "  WARNING: ASAP automation authentication was not completed: $_" -ForegroundColor Yellow
                Write-Host "  Discovery and downloads will wait until setup is rerun and sign-in completes." -ForegroundColor Yellow
            }
        }
    }
} elseif (Test-Path $DbPath) {
    Write-Host "ASAP automatic sign-in is not configured yet." -ForegroundColor DarkGray
    Write-Host "  Open Metronome > Flows > Catalog > ASAP and store the encrypted BI-desktop credential once." -ForegroundColor DarkGray
}

& $NssmExe start $ServiceName
Start-Sleep -Seconds 3

Write-Host "Starting headless Flows worker service..." -ForegroundColor Yellow
$WorkerStartedAt = Get-Date
& $NssmExe start $FlowServiceName

    # Poll until the service registers with Metronome.
    $WorkerOnline = $false
    try {
        $ExistingWorkers = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/flows/workers" -TimeoutSec 5)
        $WorkerOnline = @($ExistingWorkers | Where-Object {
            $_.worker_id -eq "bi-desktop-headless" -and $_.status -ne "offline" -and
            $_.last_seen_at -and ([datetime]$_.last_seen_at) -ge $WorkerStartedAt.AddSeconds(-5)
        }).Count -gt 0
    } catch {}
    if (-not $WorkerOnline) {
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Seconds 2
            try {
                $RegisteredWorkers = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/flows/workers" -TimeoutSec 5)
                if (@($RegisteredWorkers | Where-Object {
                    $_.worker_id -eq "bi-desktop-headless" -and $_.status -ne "offline" -and
                    $_.last_seen_at -and ([datetime]$_.last_seen_at) -ge $WorkerStartedAt.AddSeconds(-5)
                }).Count -gt 0) {
                    $WorkerOnline = $true
                    break
                }
            } catch {}
        }
    }
    if ($WorkerOnline) {
        Write-Host "  Flows worker registered with Metronome." -ForegroundColor Green
    } else {
        Write-Host "  WARNING: Flows worker did not register. Check $LogDir\flow_worker.log" -ForegroundColor Yellow
    }

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host ""
    Write-Host "Done. MX Analytics running at http://localhost:$Port" -ForegroundColor Green
    Start-Process "http://localhost:$Port"
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "WARNING: Service not running. Check $LogDir\" -ForegroundColor Red
    Write-Host ""
}
pause
