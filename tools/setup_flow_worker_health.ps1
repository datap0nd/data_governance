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
