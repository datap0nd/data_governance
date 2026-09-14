function Invoke-MetronomeGeminiSetup {
    param(
        [string]$ExtensionRoot,
        [string]$UserRoot,
        [scriptblock]$RunGemini,
        [scriptblock]$RunNpm,
        [scriptblock]$CheckService
    )
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
    if (Test-Path -LiteralPath $metadataPath) {
        $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        if ($metadata.type -notin @('link','local') -or [IO.Path]::GetFullPath($metadata.source) -ne [IO.Path]::GetFullPath($ExtensionRoot)) {
            throw 'This extension is registered from a different source. Run setup from its existing source folder so settings and registration stay intact.'
        }
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
        if ((& $RunGemini -Arguments @('extensions','install',$ExtensionRoot,'--skip-settings')) -ne 0) { throw 'Gemini could not install the extension. Existing account settings were preserved.' }
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
    Write-Host 'Ready. Restart Gemini with: gemini --model gemini-3.5-flash'
    Write-Host 'Use /metronome or /html_replicate "C:\Reports\Your Folder".'
}
