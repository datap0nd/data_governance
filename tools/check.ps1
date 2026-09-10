[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Preflight', 'Verify')]
    [string]$Mode = 'Verify',
    [string[]]$TestPath = @(),
    [string[]]$SyntaxPath = @(),
    [switch]$Full,
    [string]$DiagnosticReason,
    [switch]$Reuse,
    [string]$PythonPath,
    [switch]$InstallBrowsers
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$lockPath = Join-Path $repoRoot 'requirements-ci.lock'
$runId = '{0}-{1}-{2}' -f ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')), $PID, ([guid]::NewGuid().ToString('N').Substring(0, 8))
$runRoot = Join-Path $repoRoot ".test-runs\$runId"
$started = [DateTimeOffset]::UtcNow
$resultPath = Join-Path $runRoot 'result.json'
$externalIsolationRoot = $null
$result = [ordered]@{
    schema_version = 1
    run_id = $runId
    mode = $Mode.ToLowerInvariant()
    status = 'running'
    revision = $null
    source_fingerprint = $null
    environment = [ordered]@{
        os = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription
        powershell = $PSVersionTable.PSVersion.ToString()
        python = $null
    }
    selection = [ordered]@{
        full_suite = [bool]$Full
        diagnostic_reason = $DiagnosticReason
        tests = @($TestPath)
        syntax = @($SyntaxPath)
    }
    started_utc = $started.ToString('o')
    finished_utc = $null
    duration_seconds = $null
    exit_code = $null
    reused_from = $null
    artifacts = [ordered]@{}
    test_summary = $null
    diagnostic = $null
}

function Save-Result {
    param([string]$Status, [int]$ExitCode, [string]$Diagnostic)
    New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
    $finished = [DateTimeOffset]::UtcNow
    $result.status = $Status
    $result.exit_code = $ExitCode
    $result.finished_utc = $finished.ToString('o')
    $result.duration_seconds = [math]::Round(($finished - $started).TotalSeconds, 3)
    $result.diagnostic = $Diagnostic
    if ($externalIsolationRoot -and (Test-Path -LiteralPath $externalIsolationRoot)) {
        $allowedRoot = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'MetronomeTestRuns'))
        $resolvedIsolationRoot = [IO.Path]::GetFullPath($externalIsolationRoot)
        if (-not $resolvedIsolationRoot.StartsWith($allowedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean unexpected Flow test root: $resolvedIsolationRoot"
        }
        Remove-Item -LiteralPath $resolvedIsolationRoot -Recurse -Force
    }
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $resultPath -Encoding utf8
    Write-Host "Result: $resultPath"
}

function Fail-Check {
    param([string]$Message, [int]$ExitCode = 2)
    Save-Result -Status 'failed' -ExitCode $ExitCode -Diagnostic $Message
    Write-Error $Message
}

function Get-Revision {
    $revision = (& git -C $repoRoot rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $revision) {
        Fail-Check 'This command must run from a Git checkout with a readable HEAD revision.'
    }
    return $revision.Trim()
}

function Assert-Python313 {
    param([string]$Executable, [string]$Purpose)
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        Fail-Check "$Purpose Python was not found at '$Executable'. Run tools/check.ps1 -Mode Setup after installing Python 3.13, or pass -PythonPath to Setup."
    }
    if ($Executable -match '(?i)[\\/]codex-runtimes[\\/]') {
        Fail-Check 'The bundled coding-agent Python runtime is not a supported test interpreter. Install Python 3.13 and create the checkout-owned .venv.'
    }
    $version = (& $Executable -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^3\.13\.') {
        Fail-Check "$Purpose requires Python 3.13; '$Executable' reported '$version'."
    }
    return $version.Trim()
}

function Get-BootstrapPython {
    if ($PythonPath) {
        return (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path
    }
    $userInstall = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (Test-Path -LiteralPath $userInstall -PathType Leaf) {
        return $userInstall
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $candidate = (& $launcher.Source -3.13 -c 'import sys; print(sys.executable)' 2>$null)
        if ($LASTEXITCODE -eq 0 -and $candidate) {
            return $candidate.Trim()
        }
    }
    Fail-Check "Python 3.13 is missing. Install the official Python 3.13 package, then run '.\tools\check.ps1 -Mode Setup'; no temporary or sibling environment was searched."
}

function Get-LockFingerprint {
    if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
        Fail-Check "Dependency lock is missing: $lockPath"
    }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $lockPath).Hash.ToLowerInvariant()
}

function Invoke-Preflight {
    $version = Assert-Python313 -Executable $venvPython -Purpose 'Verification'
    $result.environment.python = $version
    $expectedLock = Get-LockFingerprint
    $installedLockPath = Join-Path (Split-Path -Parent $venvPython) '..\.metronome-ci-lock.sha256'
    $installedLockPath = [IO.Path]::GetFullPath($installedLockPath)
    $installedLock = if (Test-Path -LiteralPath $installedLockPath) { (Get-Content -Raw -LiteralPath $installedLockPath).Trim() } else { '' }
    if ($installedLock -ne $expectedLock) {
        Fail-Check "The checkout-owned .venv does not match requirements-ci.lock. Run '.\tools\check.ps1 -Mode Setup'."
    }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) {
        Fail-Check "The checkout-owned .venv has incompatible dependencies. Run '.\tools\check.ps1 -Mode Setup'."
    }
    & $venvPython -c 'import importlib.util,sys; missing=[name for name in ("pytest","fastapi","playwright","sqlalchemy","psycopg2") if importlib.util.find_spec(name) is None]; print("Missing modules: "+", ".join(missing) if missing else "Dependency imports: ready"); sys.exit(bool(missing))'
    if ($LASTEXITCODE -ne 0) {
        Fail-Check "Required test modules are missing. Run '.\tools\check.ps1 -Mode Setup'."
    }
    Write-Host "Preflight ready: Python $version, locked dependencies, isolated run root $runRoot"
}

function ConvertTo-ShellLiteral {
    param([string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function Get-SourceFingerprint {
    $trackedSource = @(& git -C $repoRoot ls-files app tools)
    $paths = @('requirements-ci.lock') + $trackedSource + @($TestPath) + @($SyntaxPath)
    $rows = foreach ($item in ($paths | Sort-Object -Unique)) {
        $filePart = ($item -split '::', 2)[0]
        $candidate = Join-Path $repoRoot $filePart
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            '{0}:{1}' -f $filePart, (Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash
        } else {
            "selector:$item"
        }
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($rows -join "`n"))
    $stream = [IO.MemoryStream]::new($bytes)
    try {
        return (Get-FileHash -Algorithm SHA256 -InputStream $stream).Hash.ToLowerInvariant()
    } finally {
        $stream.Dispose()
    }
}

function Find-ReusableResult {
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.test-runs'))) { return $null }
    $selectionJson = ($result.selection | ConvertTo-Json -Compress -Depth 5)
    foreach ($file in (Get-ChildItem -LiteralPath (Join-Path $repoRoot '.test-runs') -Filter result.json -Recurse -File | Sort-Object LastWriteTimeUtc -Descending)) {
        if ($file.FullName -eq $resultPath) { continue }
        try {
            $prior = Get-Content -Raw -LiteralPath $file.FullName | ConvertFrom-Json
            if ($prior.status -eq 'passed' -and $prior.source_fingerprint -eq $result.source_fingerprint -and (($prior.selection | ConvertTo-Json -Compress -Depth 5) -eq $selectionJson)) {
                return $file.FullName
            }
        } catch {
            continue
        }
    }
    return $null
}

try {
    Set-Location -LiteralPath $repoRoot
    New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
    $result.revision = Get-Revision

    if ($Mode -eq 'Setup') {
        $bootstrap = Get-BootstrapPython
        $bootstrapVersion = Assert-Python313 -Executable $bootstrap -Purpose 'Setup'
        if (-not (Test-Path -LiteralPath $venvPython)) {
            & $bootstrap -m venv (Join-Path $repoRoot '.venv')
            if ($LASTEXITCODE -ne 0) { Fail-Check 'Python failed to create the checkout-owned .venv.' }
        }
        Assert-Python313 -Executable $venvPython -Purpose 'Checkout environment' | Out-Null
        & $venvPython -m pip install --disable-pip-version-check --requirement $lockPath
        if ($LASTEXITCODE -ne 0) { Fail-Check 'Locked dependency installation failed.' }
        $lockFingerprint = Get-LockFingerprint
        Set-Content -LiteralPath (Join-Path $repoRoot '.venv\.metronome-ci-lock.sha256') -Value $lockFingerprint -Encoding ascii
        if ($InstallBrowsers) {
            & $venvPython -m playwright install chromium chrome msedge
            if ($LASTEXITCODE -ne 0) { Fail-Check 'Playwright browser setup failed.' }
        }
        $result.environment.python = $bootstrapVersion
        Save-Result -Status 'passed' -ExitCode 0 -Diagnostic $null
        exit 0
    }

    if ($Mode -eq 'Verify' -and $Full -and [string]::IsNullOrWhiteSpace($DiagnosticReason)) {
        Fail-Check 'A local full suite is diagnostic-only. Supply -Full -DiagnosticReason with the failure or equivalence question being investigated.'
    }
    if ($Mode -eq 'Verify' -and -not $Full -and $TestPath.Count -eq 0) {
        Fail-Check "Verify requires explicit -TestPath selectors. Example: .\tools\check.ps1 -Mode Verify -TestPath tests/test_flows.py::test_name"
    }

    Invoke-Preflight
    if ($Mode -eq 'Preflight') {
        Save-Result -Status 'passed' -ExitCode 0 -Diagnostic $null
        exit 0
    }
    if ($Full) { $TestPath = @('tests') }
    $result.selection.tests = @($TestPath)
    $result.source_fingerprint = Get-SourceFingerprint

    if ($Reuse) {
        $prior = Find-ReusableResult
        if ($prior) {
            $result.reused_from = $prior
            Save-Result -Status 'passed' -ExitCode 0 -Diagnostic 'Reused unchanged successful local evidence.'
            exit 0
        }
    }

    $tempRoot = Join-Path $runRoot 'tmp'
    $profileRoot = Join-Path $runRoot 'browser-profiles'
    New-Item -ItemType Directory -Force -Path $tempRoot, $profileRoot | Out-Null
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot
    $env:DG_DB_PATH = Join-Path $runRoot 'governance-test.db'
    $env:DG_TEST_RUN_ROOT = $runRoot
    $env:DG_BROWSER_PROFILE_ROOT = $profileRoot
    $externalIsolationRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) "MetronomeTestRuns\$runId"
    $externalFlowRoot = Join-Path $externalIsolationRoot 'flows'
    New-Item -ItemType Directory -Force -Path $externalFlowRoot | Out-Null
    $env:DG_FLOWS_ROOT = $externalFlowRoot
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $repoRoot '.playwright-browsers'
    $junitPath = Join-Path $runRoot 'pytest.xml'
    $result.artifacts.junit = $junitPath

    foreach ($syntaxItem in $SyntaxPath) {
        $syntaxFile = Join-Path $repoRoot $syntaxItem
        if (-not (Test-Path -LiteralPath $syntaxFile -PathType Leaf)) { Fail-Check "Syntax target not found: $syntaxItem" }
        switch -Regex ([IO.Path]::GetExtension($syntaxFile)) {
            '^\.py$' { & $venvPython -m py_compile $syntaxFile }
            '^\.(js|mjs)$' { & node --check $syntaxFile }
            '^\.ps1$' {
                $tokens = $null; $errors = $null
                [Management.Automation.Language.Parser]::ParseFile($syntaxFile, [ref]$tokens, [ref]$errors) | Out-Null
                if ($errors.Count) { throw ($errors | ForEach-Object Message) -join '; ' }
            }
            default { Fail-Check "No syntax checker is configured for: $syntaxItem" }
        }
        if ($LASTEXITCODE -ne 0) { Fail-Check "Syntax check failed: $syntaxItem" -ExitCode $LASTEXITCODE }
    }

    $pytestArguments = @('-m', 'pytest') + @($TestPath) + @('-q', '-ra', '--durations=20', "--basetemp=$tempRoot", "--junitxml=$junitPath")
    $commandText = '& ' + (((@($venvPython) + $pytestArguments) | ForEach-Object { ConvertTo-ShellLiteral $_ }) -join ' ')
    & (Get-Process -Id $PID).Path -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $commandText
    $testExit = $LASTEXITCODE
    if ($testExit -ne 0) {
        Fail-Check "Focused verification failed with exit code $testExit. Rerun the failed case and only its necessary integration companions." -ExitCode $testExit
    }
    if (Test-Path -LiteralPath $junitPath) {
        [xml]$junit = Get-Content -Raw -LiteralPath $junitPath
        $suite = if ($junit.testsuites) { $junit.testsuites.testsuite } else { $junit.testsuite }
        $result.test_summary = [ordered]@{
            tests = [int]$suite.tests
            failures = [int]$suite.failures
            errors = [int]$suite.errors
            skipped = [int]$suite.skipped
        }
    }
    Save-Result -Status 'passed' -ExitCode 0 -Diagnostic $null
    exit 0
} catch {
    if ($result.status -eq 'running') {
        Save-Result -Status 'failed' -ExitCode 2 -Diagnostic $_.Exception.Message
    }
    throw
}
