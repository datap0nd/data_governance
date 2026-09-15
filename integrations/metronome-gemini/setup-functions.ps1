function Invoke-MetronomeGeminiSetup {
    param(
        [string]$ExtensionRoot,
        [string]$UserRoot,
        [scriptblock]$RunGemini,
        [scriptblock]$RunNpm,
        [scriptblock]$CheckService
    )
    $ExtensionRoot = [IO.Path]::GetFullPath($ExtensionRoot).TrimEnd('\','/')
    $UserRoot = [IO.Path]::GetFullPath($UserRoot).TrimEnd('\','/')
    $registration = Join-Path $UserRoot '.gemini/extensions/metronome-gemini'
    $metadataPath = Join-Path $registration '.gemini-extension-install.json'
    $accessPath = Join-Path $UserRoot '.gemini/metronome/access.json'
    $access = @{ METRONOME_BASE_URL = 'http://127.0.0.1:8000'; METRONOME_FLOW_IDS = '*'; METRONOME_SITE_IDS = '*' }
    # Preserve existing public connection/scopes before Gemini updates its settings.
    # SQL credentials are never imported from files or from Metronome's uploader.
    if (Test-Path -LiteralPath $accessPath) {
        $saved = Get-Content -LiteralPath $accessPath -Raw | ConvertFrom-Json
        foreach ($key in @($access.Keys)) {
            if ($saved.PSObject.Properties.Name -contains $key) { $access[$key] = [string]$saved.$key }
        }
    }
    $oldEnv = Join-Path $registration '.env'
    if (Test-Path -LiteralPath $oldEnv) {
        foreach ($line in [IO.File]::ReadAllLines($oldEnv)) {
            if ($line -match '^(METRONOME_BASE_URL|METRONOME_FLOW_IDS|METRONOME_SITE_IDS)=(.*)$') {
                $key = $Matches[1]; $value = $Matches[2].Trim().Trim('"').Trim("'")
                if ($value) { $access[$key] = $value }
            }
        }
    }
    $uri = [uri]$access.METRONOME_BASE_URL
    if ($uri.Scheme -notin @('http','https') -or $uri.Host -notin @('127.0.0.1','localhost','[::1]','::1') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') {
        throw 'The saved Metronome address is not a local origin. Existing settings were preserved; this extension supports the local service.'
    }
    foreach ($key in @('METRONOME_FLOW_IDS','METRONOME_SITE_IDS')) {
        if ($access[$key] -notmatch '^(\*|[1-9][0-9]*(,[1-9][0-9]*)*)$') { throw 'Existing access restrictions are invalid. Setup will not discard them.' }
    }
    $metadata = $null
    $recoverRegistration = $false
    if (Test-Path -LiteralPath $registration) {
        $entry = Get-Item -LiteralPath $registration -Force
        if (-not $entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'The Gemini extension registration is not a normal folder. Setup preserved it; inspect its location before retrying.' }
        $fullRegistration = [IO.Path]::GetFullPath($registration).TrimEnd('\','/')
        if ($ExtensionRoot -eq $fullRegistration -or $ExtensionRoot.StartsWith($fullRegistration + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Run install.ps1 from the Metronome checkout, not from Gemini''s installed extension folder.'
        }
    }
    if (Test-Path -LiteralPath $metadataPath) {
        $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        if ($metadata.type -notin @('link','local') -or -not $metadata.source -or [IO.Path]::GetFullPath($metadata.source).TrimEnd('\','/') -ne $ExtensionRoot) {
            throw 'This extension is registered from a different source. Run setup from its existing source folder so settings and registration stay intact.'
        }
    } elseif (Test-Path -LiteralPath $registration) {
        $manifestPath = Join-Path $registration 'gemini-extension.json'
        if (Test-Path -LiteralPath $manifestPath) {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
            if ($manifest.name -ne 'metronome-gemini') { throw 'The existing extension folder belongs to something else. Setup preserved it.' }
        } elseif (@(Get-ChildItem -LiteralPath $registration -Force | Where-Object { $_.Name -ne '.env' }).Count) {
            throw 'The existing extension folder has unknown contents and no install metadata. Setup preserved it; inspect it before retrying.'
        }
        $recoverRegistration = $true
    }
    if ((& $RunNpm) -ne 0) { throw 'Dependency installation failed; existing Gemini settings were preserved.' }
    New-Item -ItemType Directory -Force -Path (Split-Path $accessPath) | Out-Null
    $access | ConvertTo-Json | Set-Content -LiteralPath $accessPath -Encoding UTF8
    try {
        $available = & $CheckService ($uri.AbsoluteUri.TrimEnd('/') + '/api/version')
        if ($available.version) { Write-Host "Metronome connected automatically: $($uri.AbsoluteUri)" }
        else { Write-Host 'Metronome is not responding as expected. Setup can finish; check the service before using /metronome.' }
    } catch { Write-Host 'Metronome is currently unavailable. Setup can finish; start the service before using /metronome.' }

    if (-not $metadata) {
        $backup = $null
        if ($recoverRegistration) {
            $backupRoot = Join-Path $UserRoot '.gemini/metronome/install-backups'
            $backup = Join-Path $backupRoot ([guid]::NewGuid().ToString())
            # Verify both absolute targets before moving this one registration.
            $boundary = [IO.Path]::GetFullPath((Join-Path $UserRoot '.gemini')) + [IO.Path]::DirectorySeparatorChar
            foreach ($target in @($registration,$backup)) {
                if (-not [IO.Path]::GetFullPath($target).StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) { throw 'Extension recovery escaped the Gemini settings directory.' }
            }
            New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
            Move-Item -LiteralPath $registration -Destination $backup
            Write-Host 'Recovering an incomplete extension registration. Existing files are backed up; account credentials remain managed by Gemini.'
        }
        try {
            if ((& $RunGemini -Arguments @('extensions','install',$ExtensionRoot,'--skip-settings')) -ne 0) { throw 'Gemini could not install the extension. Rerun setup after resolving the error above.' }
            if ($backup -and (Test-Path -LiteralPath (Join-Path $backup '.env'))) {
                Copy-Item -LiteralPath (Join-Path $backup '.env') -Destination $oldEnv
            }
        } catch {
            if ($backup) {
                # Retain an interrupted new registration too; never delete it.
                $failedBackup = $backup + '-failed'
                if (-not [IO.Path]::GetFullPath($failedBackup).StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid recovery destination.' }
                if (Test-Path -LiteralPath $registration) { Move-Item -LiteralPath $registration -Destination $failedBackup }
                Move-Item -LiteralPath $backup -Destination $registration
                Write-Host 'Installation failed. The previous extension folder was restored. Rerun setup to retry.'
            }
            throw
        }
    } elseif ($metadata.type -eq 'local') {
        # Our copied installs already have the three field definitions. Updates
        # retain their values. Linked installs read the updated source directly.
        if ((& $RunGemini -Arguments @('extensions','update','metronome-gemini')) -ne 0) { throw 'Gemini could not update the extension.' }
    }
    Write-Host 'SQL setup: server, read-only username, then password. No connection string or table list is needed.'
    foreach ($field in @('METRONOME_SQL_HOST','METRONOME_SQL_USER','METRONOME_SQL_PASSWORD')) {
        if ((& $RunGemini -Arguments @('extensions','config','metronome-gemini',$field)) -ne 0) { throw 'SQL setup was interrupted. Your completed fields are preserved; rerun setup to continue.' }
        if ($field -in @('METRONOME_SQL_HOST','METRONOME_SQL_USER')) {
            $entered = ''
            if (Test-Path -LiteralPath $oldEnv) {
                foreach ($line in [IO.File]::ReadAllLines($oldEnv)) {
                    if ($line -match ('^' + $field + '=(.*)$')) { $entered = $Matches[1].Trim().Trim('"').Trim("'").Trim() }
                }
            }
            if (-not $entered -and $field -eq 'METRONOME_SQL_HOST') { Write-Host 'SQL setup skipped. File analysis and Metronome flow tools remain available.'; break }
            if (-not $entered) { throw 'The read-only username is missing. Completed settings were preserved; rerun setup to enter it.' }
        }
    }
    Write-Host 'Generated work: %USERPROFILE%\Metronome Gemini Work'
    Write-Host 'Gemini creates scratch for scripts/temp files and reports for finished work. Close Gemini before cleaning scratch; keep reports you need.'
    Write-Host 'Ready. Restart Gemini with: gemini --model gemini-3.5-flash'
    Write-Host 'Use /metronome or /html_replicate "C:\Reports\Your Folder".'
}
