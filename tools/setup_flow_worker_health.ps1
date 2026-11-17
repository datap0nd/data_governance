function Get-MetronomeSetupFlowWorkers {
    param(
        [Parameter(Mandatory = $true)][int]$Port
    )

    # Windows PowerShell 5.1 can preserve a top-level JSON array from
    # Invoke-RestMethod as one System.Object[] pipeline item.  Pipe the saved
    # response explicitly so callers always receive individual worker rows.
    $Response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/flows/workers" -TimeoutSec 5
    @($Response | ForEach-Object { $_ })
}

function Test-MetronomeSetupWorkerFresh {
    param(
        [Parameter(Mandatory = $true)]$Worker,
        [Parameter(Mandatory = $true)][datetime]$WorkerStartedAt
    )

    if (-not $Worker -or $Worker.status -eq "offline" -or -not $Worker.last_seen_at) {
        return $false
    }
    # An unexpected array or malformed timestamp should make a worker stale;
    # it must never abort setup after the services have started successfully.
    if ($Worker.last_seen_at -is [System.Array]) {
        return $false
    }
    [datetimeoffset]$LastSeenAt = [datetimeoffset]::MinValue
    $DateStyles = [Globalization.DateTimeStyles]::AllowWhiteSpaces -bor [Globalization.DateTimeStyles]::AssumeLocal
    if (-not [datetimeoffset]::TryParse(
        [string]$Worker.last_seen_at,
        [Globalization.CultureInfo]::InvariantCulture,
        $DateStyles,
        [ref]$LastSeenAt
    )) {
        return $false
    }
    return $LastSeenAt -ge ([datetimeoffset]$WorkerStartedAt).AddSeconds(-5)
}

function Start-MetronomeSetupService {
    # Start one service through NSSM without letting NSSM's message stop setup.
    #
    # Windows PowerShell 5.1 turns each line a native program writes to stderr
    # into an error once stderr is redirected, and setup runs with
    # $ErrorActionPreference = 'Stop'. A worker that was only slow to report
    # running ("Unexpected status SERVICE_START_PENDING in response to START
    # control") therefore aborted setup before Metronome itself was started.
    # This reads NSSM's words instead and never throws. State is Started,
    # Running (it already was), Starting (Windows still reports
    # START_PENDING) or Failed; the registration and health checks that follow
    # in setup decide whether the service really came up.
    param(
        [Parameter(Mandatory = $true)][string]$NssmExe,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $ErrorActionPreference = 'Continue'
    $Lines = @(& $NssmExe start $Name 2>&1 | ForEach-Object { ("$_" -replace "`0", '').Trim() } | Where-Object { $_ })
    $ExitCode = $LASTEXITCODE
    $Message = ($Lines -join ' ').Trim()
    $State = if ($Message -match 'already running') { 'Running' }
        elseif ($ExitCode -eq 0) { 'Started' }
        elseif ($Message -match 'START_PENDING') { 'Starting' }
        else { 'Failed' }
    [pscustomobject]@{ Name = $Name; State = $State; Message = $Message; ExitCode = $ExitCode }
}

function Write-MetronomeSetupServiceStart {
    # One line for the person watching setup; nothing when the start was clean.
    param([Parameter(Mandatory = $true)]$Result)
    switch ($Result.State) {
        'Running' { Write-Host "  $($Result.Name) was already running - OK." -ForegroundColor DarkGray }
        'Starting' { Write-Host "  $($Result.Name) is still starting; setup carries on and checks below that it comes up." -ForegroundColor DarkGray }
        'Failed' { Write-Host "  WARNING: $($Result.Name) did not start: $($Result.Message) Setup carries on; the checks below say what is missing." -ForegroundColor Yellow }
    }
}
