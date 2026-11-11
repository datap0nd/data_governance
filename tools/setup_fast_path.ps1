# Fast-path helpers for setup.ps1. Dot-sourcing this file changes nothing on
# the machine: every function is pure or touches only the paths it is given.
#
# setup.ps1 records a fingerprint for each slow step it completes (dependency
# install, service configuration, scheduled tasks, folder permissions). An
# unattended update whose fingerprint still matches skips that step; a manual
# run of setup.ps1 always does the full work, so it stays the repair path.
# Every helper fails soft: any doubt means "not current, do the work".

function Get-MetronomeFingerprint {
    param([string[]]$Parts = @())
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $text = (@($Parts) | ForEach-Object { "$_" }) -join "`n"
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-MetronomeFileFingerprint {
    # Names and contents of the given files. A missing file is part of the
    # fingerprint, so adding or removing one changes it.
    param([string[]]$Paths = @())
    $parts = @()
    foreach ($path in @($Paths | Sort-Object)) {
        $name = [System.IO.Path]::GetFileName($path)
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $parts += "$name=$((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash)"
        } else {
            $parts += "$name=missing"
        }
    }
    return (Get-MetronomeFingerprint -Parts $parts)
}

function Get-MetronomeSetupState {
    # The recorded fingerprints as a hashtable; empty when the file is absent
    # or unreadable.
    param([Parameter(Mandatory = $true)][string]$Path)
    $state = @{}
    try {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $parsed = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            if ($parsed) {
                foreach ($property in $parsed.PSObject.Properties) {
                    $state[$property.Name] = "$($property.Value)"
                }
            }
        }
    } catch {
        $state = @{}
    }
    return $state
}

function Test-MetronomeStepCurrent {
    param($State, [Parameter(Mandatory = $true)][string]$Step, [string]$Fingerprint)
    if (-not $Fingerprint) { return $false }
    if (-not ($State -is [hashtable])) { return $false }
    return ($State.ContainsKey($Step) -and ($State[$Step] -ceq $Fingerprint))
}

function Set-MetronomeStepCurrent {
    # Record (or, with an empty fingerprint, forget) one step. Returns $false
    # instead of throwing: a state file that cannot be written only means the
    # next update repeats the step.
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Step,
        [string]$Fingerprint
    )
    try {
        $state = Get-MetronomeSetupState -Path $Path
        if ($Fingerprint) { $state[$Step] = $Fingerprint } else { [void]$state.Remove($Step) }
        $parent = Split-Path -Parent $Path
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        $ordered = [ordered]@{}
        foreach ($key in @($state.Keys | Sort-Object)) { $ordered[$key] = $state[$key] }
        $temp = "$Path.tmp.$PID"
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($temp, ($ordered | ConvertTo-Json), $utf8)
        Move-Item -LiteralPath $temp -Destination $Path -Force
        return $true
    } catch {
        return $false
    }
}

function Expand-MetronomeArchive {
    # Extract into a fresh folder with the framework's own extractor. Windows
    # PowerShell 5.1's Expand-Archive handles every entry in script and is
    # many times slower; it remains the fallback.
    param(
        [Parameter(Mandatory = $true)][string]$ZipPath,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
        [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $Destination)
        return 'framework'
    } catch {
        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
        }
        Expand-Archive -Path $ZipPath -DestinationPath $Destination -Force
        return 'expand-archive'
    }
}

function Test-MetronomeTaskActionCurrent {
    # True when a registered scheduled task already runs exactly this program,
    # arguments and working folder.
    param($Task, [string]$Execute, [string]$Arguments, [string]$WorkingDirectory)
    try {
        if (-not $Task) { return $false }
        $actions = @($Task.Actions)
        if ($actions.Count -ne 1) { return $false }
        $action = $actions[0]
        return (
            ("$($action.Execute)" -eq $Execute) -and
            ("$($action.Arguments)" -eq $Arguments) -and
            ("$($action.WorkingDirectory)" -eq $WorkingDirectory)
        )
    } catch {
        return $false
    }
}
