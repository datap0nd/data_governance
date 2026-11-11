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

function Get-MetronomeFileSizeText {
    param([string]$Path)
    try {
        $length = (Get-Item -LiteralPath $Path -ErrorAction Stop).Length
        return ('{0:N0} KB' -f [math]::Ceiling($length / 1024))
    } catch {
        return 'size unknown'
    }
}

function Get-MetronomeInstallCheck {
    # What setup.ps1 should do when run from $CodeDir with its database at
    # $DbPath, given where the installed service keeps its own:
    #   continue - nothing to say;
    #   notice   - the services move to this folder's database, which exists
    #              (or a new one was asked for by name);
    #   stop     - this folder has no database and the installed one is
    #              somewhere else, so going on would start a second, empty
    #              Metronome. The caller exits before changing anything.
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

    $installedSize = Get-MetronomeFileSizeText -Path $installedDb
    if (Test-Path -LiteralPath $DbPath -PathType Leaf) {
        $check.Action = 'notice'
        $check.Lines = @(
            'NOTE: Metronome was using a different database until now:',
            "    $installedDb ($installedSize)",
            '  From now on it uses the one beside this folder:',
            "    $DbPath ($(Get-MetronomeFileSizeText -Path $DbPath))"
        )
        return $check
    }
    if ($AllowNewDatabase) {
        $check.Action = 'notice'
        $check.Lines = @(
            'NOTE: Starting a new, empty database here because DG_SETUP_NEW_DATABASE is 1.',
            "  The database Metronome used until now is left where it is: $installedDb ($installedSize)"
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
        "  Metronome's database now:  $installedDb ($installedSize)",
        "  Its code folder:           $installedCode",
        "  This folder:               $CodeDir",
        "  Database beside this one:  $DbPath (none)",
        '',
        'Running setup here would start a second, empty Metronome and leave your',
        'data unused. Nothing was changed.',
        '',
        'Run setup.ps1 from where Metronome is installed:',
        "  $runFrom",
        '',
        'To install here on purpose, with a new empty database, run:',
        "  `$env:DG_SETUP_NEW_DATABASE = '1'; .\setup.ps1"
    )
    return $check
}
