# Where Metronome is installed on this PC, and whether setup.ps1 is being run
# from somewhere else. Dot-sourcing this file changes nothing on the machine.
#
# setup.ps1 installs into the folder it is run from and keeps the database
# beside that folder. Run from a second copy of the code (a fresh download, an
# archive extracted one folder too deep), it used to build a second, empty
# Metronome and point the services at it: the real database stayed on disk,
# unused, and the app opened blank. These functions let setup.ps1 see that
# before it changes anything.

function Get-MetronomeInstalledLocation {
    # The code folder and the database the installed service uses now, as nssm
    # recorded them. Empty strings when the service is not installed.
    param(
        [string]$ServiceName = 'MXAnalytics',
        [string]$RegistryRoot = 'HKLM:\SYSTEM\CurrentControlSet\Services'
    )
    $location = @{ CodeDir = ''; Database = '' }
    try {
        $key = Join-Path (Join-Path $RegistryRoot $ServiceName) 'Parameters'
        $parameters = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
        $directory = $parameters.PSObject.Properties['AppDirectory']
        if ($directory -and $directory.Value) { $location.CodeDir = "$($directory.Value)".Trim() }
        $environment = $parameters.PSObject.Properties['AppEnvironmentExtra']
        if ($environment) {
            foreach ($entry in @($environment.Value)) {
                foreach ($line in ("$entry" -split "`r?`n")) {
                    if ($line -match '^\s*DG_DB_PATH=(.+)$') { $location.Database = $Matches[1].Trim() }
                }
            }
        }
    } catch {}
    return $location
}

function Test-MetronomeSamePath {
    # Two spellings of one location: letter case and a trailing separator do
    # not make a different folder on Windows.
    param([string]$Left, [string]$Right)
    if (-not $Left -or -not $Right) { return $false }
    try {
        $separators = [char[]]@('\', '/')
        $a = [System.IO.Path]::GetFullPath($Left).TrimEnd($separators)
        $b = [System.IO.Path]::GetFullPath($Right).TrimEnd($separators)
        return [string]::Equals($a, $b, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Test-MetronomeSameFile {
    # One file reached by two routes (a junction, a share name, a short name)
    # has one size and one last-write time. Two different databases do not.
    param([string]$Left, [string]$Right)
    try {
        $a = Get-Item -LiteralPath $Left -ErrorAction Stop
        $b = Get-Item -LiteralPath $Right -ErrorAction Stop
        return ($a.Length -eq $b.Length) -and ($a.LastWriteTimeUtc -eq $b.LastWriteTimeUtc)
    } catch {
        return $false
    }
}

function Get-MetronomeSizeText {
    # A size a person can compare at a glance: "640 KB", "1.3 MB", "1.02 GB".
    # In KB alone, a new empty database and a real one differ only in how many
    # digits the number has.
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
    return ('{0:N0} KB' -f [math]::Ceiling($Bytes / 1KB))
}

function Get-MetronomeFileText {
    # "1.3 MB, last changed 2026-11-11 14:02" for a message.
    param([string]$Path)
    try {
        $file = Get-Item -LiteralPath $Path -ErrorAction Stop
        return ('{0}, last changed {1}' -f (Get-MetronomeSizeText -Bytes $file.Length), $file.LastWriteTime.ToString('yyyy-MM-dd HH:mm'))
    } catch {
        return 'size unknown'
    }
}

function Get-MetronomeInstallCheck {
    # What setup.ps1 should do when run from $CodeDir with its database at
    # $DbPath, given where the installed service keeps its own:
    #   continue - this is the installed folder, or nothing is installed;
    #   stop     - this folder has no database and the installed one is
    #              somewhere else, so going on would start a second, empty
    #              Metronome;
    #   ask      - both folders have a database. Going on would make Metronome
    #              show the other one, so a person has to say so. This is also
    #              how a PC is pointed back after a second copy was installed
    #              by mistake;
    #   notice   - a new empty database here was asked for by name.
    param(
        [Parameter(Mandatory = $true)][string]$CodeDir,
        [Parameter(Mandatory = $true)][string]$DbPath,
        [hashtable]$Installed = @{},
        [switch]$AllowNewDatabase
    )
    $check = @{ Action = 'continue'; Lines = @() }
    $installedDb = "$($Installed['Database'])"
    $installedCode = "$($Installed['CodeDir'])"
    if (-not $installedDb) { return $check }
    if (Test-MetronomeSamePath -Left $installedDb -Right $DbPath) { return $check }
    if (-not (Test-Path -LiteralPath $installedDb -PathType Leaf)) { return $check }

    $installedText = Get-MetronomeFileText -Path $installedDb
    if (Test-Path -LiteralPath $DbPath -PathType Leaf) {
        if (Test-MetronomeSameFile -Left $installedDb -Right $DbPath) { return $check }
        $check.Action = 'ask'
        $check.Lines = @(
            'This folder is not the one Metronome uses now.',
            '',
            '  Metronome uses now:',
            "    $installedDb",
            "    $installedText",
            '  This folder would make it use:',
            "    $DbPath",
            "    $(Get-MetronomeFileText -Path $DbPath)",
            '',
            'Switching makes Metronome show what is in this folder''s database.',
            'The other database is left where it is.'
        )
        return $check
    }
    if ($AllowNewDatabase) {
        $check.Action = 'notice'
        $check.Lines = @(
            'NOTE: Starting a new, empty database here because DG_SETUP_NEW_DATABASE is 1.',
            "  The database Metronome used until now is left where it is: $installedDb"
        )
        return $check
    }

    $runFrom = 'the code folder that sits beside that database'
    if ($installedCode -and (Test-Path -LiteralPath (Join-Path $installedCode 'setup.ps1') -PathType Leaf)) {
        $runFrom = Join-Path $installedCode 'setup.ps1'
    }
    $check.Action = 'stop'
    $check.Lines = @(
        'SETUP STOPPED: Metronome is already installed somewhere else on this PC.',
        '',
        '  Metronome uses now:',
        "    $installedDb",
        "    $installedText",
        "  Its code folder:  $installedCode",
        "  This folder:      $CodeDir",
        "  It has no database: $DbPath",
        '',
        'Running setup here would start a second, empty Metronome and leave your',
        'data unused.',
        '',
        'Run setup.ps1 from where Metronome is installed:',
        "  $runFrom",
        '',
        'To install here on purpose, with a new empty database, run:',
        "  `$env:DG_SETUP_NEW_DATABASE = '1'; .\setup.ps1"
    )
    return $check
}

function Get-MetronomeParentProcessId {
    # The process that started this one; 0 when it cannot be read.
    param([int]$ProcessId = $PID)
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        return [int]$process.ParentProcessId
    } catch {
        return 0
    }
}

function Test-MetronomeRelaunchedCopy {
    # True only in the copy of setup.ps1 that another copy started to finish
    # the same run. A copy that relaunches sets the marker and records its own
    # process id, and only a process it started itself matches. The marker
    # alone proves nothing: an older copy leaves it behind in the PowerShell
    # window it was run from, where it outlives the run.
    param(
        [string]$Marker = $env:DG_SETUP_RELAUNCHED,
        [string]$RecordedParent = $env:DG_SETUP_RELAUNCH_PARENT,
        [int]$ActualParent = -1
    )
    if ($Marker -ne '1' -or -not "$RecordedParent".Trim()) { return $false }
    if ($ActualParent -lt 0) { $ActualParent = Get-MetronomeParentProcessId }
    if ($ActualParent -le 0) { return $false }
    return ("$RecordedParent".Trim() -eq "$ActualParent")
}

function Start-MetronomeInstalledServices {
    # Start again the services an earlier part of the run stopped, when setup
    # then refuses to go on. Only services Windows starts by itself; a slot
    # that is installed but switched off stays off. Returns the names started.
    param([string[]]$Names = @('MXAnalytics', 'MetronomeAuditorReader', 'MXFlowsWorker*'))
    $started = @()
    foreach ($service in @(Get-Service -Name $Names -ErrorAction SilentlyContinue)) {
        try {
            if ("$($service.StartType)" -eq 'Automatic' -and "$($service.Status)" -ne 'Running') {
                Start-Service -InputObject $service -ErrorAction Stop
                $started += $service.Name
            }
        } catch {}
    }
    return $started
}
