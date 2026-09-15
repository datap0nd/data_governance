param([string]$ExtensionRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $ExtensionRoot 'setup-functions.ps1')
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('gemini-setup-' + [guid]::NewGuid())
function Assert-That($Value, $Message) { if (-not $Value) { throw $Message } }
function Run-Case([string]$Name, [string]$Type = '', [switch]$SkipSql, [switch]$Offline, [switch]$FailNpm, [switch]$Interrupt, [switch]$BlankUser, [switch]$QuotedBlank,
    [switch]$Fresh, [switch]$FailInstall, [switch]$Unknown, [switch]$Foreign, [switch]$CopiedWithoutMetadata, [switch]$TrailingSlash, [switch]$Malformed) {
    $userDir = Join-Path $scratch $Name
    $registration = Join-Path $userDir '.gemini/extensions/metronome-gemini'
    $fixtureSettingsFile = Join-Path $registration '.env'
    if (-not $Fresh) {
        New-Item -ItemType Directory -Force -Path $registration | Out-Null
        [IO.File]::WriteAllText($fixtureSettingsFile, "METRONOME_FLOW_IDS=42`nMETRONOME_SITE_IDS=1`n")
    }
    if ($Type) {
        $source = if ($Foreign) { Join-Path $scratch 'other-source' } elseif ($TrailingSlash) { $ExtensionRoot.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar } else { $ExtensionRoot }
        @{ type = $Type; source = $source } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $registration '.gemini-extension-install.json')
    }
    if ($Unknown) { Set-Content -LiteralPath (Join-Path $registration 'owner-file.txt') -Value 'fictional preserved data' }
    if ($CopiedWithoutMetadata) { @{ name = 'metronome-gemini'; version = '0.2.0' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $registration 'gemini-extension.json') }
    if ($Malformed) { Set-Content -LiteralPath (Join-Path $registration '.gemini-extension-install.json') -Value '{bad' }
    $calls = [Collections.Generic.List[string]]::new()
    $gemini = {
        param([string[]]$Arguments)
        $calls.Add(($Arguments -join '|'))
        if ($Arguments[1] -eq 'install') {
            # Model Gemini's actual destination guard, previously absent here.
            if (Test-Path -LiteralPath $registration) { throw 'Cannot install extension because a directory with that name already exists.' }
            New-Item -ItemType Directory -Force -Path $registration | Out-Null
            @{ type = 'local'; source = $ExtensionRoot } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $registration '.gemini-extension-install.json')
            if ($FailInstall) { return 1 }
        }
        if ($Arguments[-1] -eq 'METRONOME_SQL_HOST' -and -not $SkipSql) { [IO.File]::AppendAllText($fixtureSettingsFile, "METRONOME_SQL_HOST=fixture-server`n") }
        if ($Arguments[-1] -eq 'METRONOME_SQL_HOST' -and $QuotedBlank) { [IO.File]::AppendAllText($fixtureSettingsFile, "METRONOME_SQL_HOST=`"  `"`n") }
        if ($Arguments[-1] -eq 'METRONOME_SQL_USER' -and $Interrupt) { return 1 }
        if ($Arguments[-1] -eq 'METRONOME_SQL_USER' -and -not $BlankUser) { [IO.File]::AppendAllText($fixtureSettingsFile, "METRONOME_SQL_USER=fixture-reader`n") }
        return 0
    }.GetNewClosure()
    $npm = { if ($FailNpm) { return 1 }; return 0 }.GetNewClosure()
    $probe = { param($Address); if ($Offline) { throw 'synthetic offline' }; if ($Address -ne 'http://127.0.0.1:8000/api/version') { throw 'wrong address' }; return @{ version = 'fictional' } }.GetNewClosure()
    $failed = $false
    try { Invoke-MetronomeGeminiSetup -ExtensionRoot $ExtensionRoot -UserRoot $userDir -RunGemini $gemini -RunNpm $npm -CheckService $probe }
    catch { $failed = $true; if (-not ($FailNpm -or $Interrupt -or $BlankUser -or $FailInstall -or $Unknown -or $Foreign -or $Malformed)) { throw } }
    Assert-That ($failed -eq [bool]($FailNpm -or $Interrupt -or $BlankUser -or $FailInstall -or $Unknown -or $Foreign -or $Malformed)) 'Unexpected failure state'
    if ($Unknown -or $Foreign -or $Malformed) { Assert-That ($calls.Count -eq 0) 'Unsafe registration invoked Gemini'; Assert-That (Test-Path -LiteralPath $fixtureSettingsFile) 'Existing settings lost'; return }
    if ($FailNpm) { Assert-That ($calls.Count -eq 0) 'Dependency failure invoked Gemini'; return }
    if ($FailInstall) {
        Assert-That ([IO.File]::ReadAllText($fixtureSettingsFile) -eq "METRONOME_FLOW_IDS=42`nMETRONOME_SITE_IDS=1`n") 'Recovery did not restore original settings'
        Assert-That ($calls.Count -eq 1) 'Failed install continued to SQL'
        Assert-That (@(Get-ChildItem -LiteralPath (Join-Path $userDir '.gemini/metronome/install-backups')).Count -eq 1) 'Interrupted registration not retained'
        return
    }
    $access = Get-Content -LiteralPath (Join-Path $userDir '.gemini/metronome/access.json') -Raw | ConvertFrom-Json
    if ($Fresh) { Assert-That ($access.METRONOME_FLOW_IDS -eq '*' -and $access.METRONOME_SITE_IDS -eq '*') 'Fresh defaults changed' }
    else { Assert-That ($access.METRONOME_FLOW_IDS -eq '42' -and $access.METRONOME_SITE_IDS -eq '1') 'Existing scopes changed' }
    $questions = @($calls | Where-Object { $_ -like 'extensions|config|*' })
    $expected = if ($SkipSql) { 1 } elseif ($Interrupt -or $BlankUser) { 2 } else { 3 }
    Assert-That ($questions.Count -eq $expected) 'Unexpected question count'
    Assert-That ($questions[0] -like '*|METRONOME_SQL_HOST') 'First field was not server'
    if ($questions.Count -gt 1) { Assert-That ($questions[1] -like '*|METRONOME_SQL_USER') 'Second field was not username' }
    if ($questions.Count -gt 2) { Assert-That ($questions[2] -like '*|METRONOME_SQL_PASSWORD') 'Third field was not password' }
    Assert-That (-not ($questions -match 'BASE_URL|SQL_RELATIONS|FLOW_IDS|SITE_IDS|READONLY_DSN')) 'Unexpected setup question'
    if ($Type -eq 'link') { Assert-That ($calls.Count -eq $expected) 'Existing link was installed again' }
    elseif ($Type -eq 'local') { Assert-That ($calls[0] -eq 'extensions|update|metronome-gemini') 'Copied extension was not updated' }
    else { Assert-That ($calls[0] -like 'extensions|install|*|--skip-settings') 'Fresh install did not skip generic settings wizard' }
    if (-not $Type -and -not $Fresh) {
        $backups = @(Get-ChildItem -LiteralPath (Join-Path $userDir '.gemini/metronome/install-backups'))
        Assert-That ($backups.Count -eq 1) 'Original registration backup missing'
        Assert-That ([IO.File]::ReadAllText((Join-Path $backups[0].FullName '.env')) -eq "METRONOME_FLOW_IDS=42`nMETRONOME_SITE_IDS=1`n") 'Backup settings changed'
        # Rerun against real metadata written by the mock CLI: update, not install.
        $calls.Clear()
        Invoke-MetronomeGeminiSetup -ExtensionRoot $ExtensionRoot -UserRoot $userDir -RunGemini $gemini -RunNpm $npm -CheckService $probe
        Assert-That ($calls[0] -eq 'extensions|update|metronome-gemini') 'Recovery rerun tried a fresh install'
    }
}
try {
    Run-Case 'fresh' -Fresh
    Run-Case 'incomplete-env'
    Run-Case 'incomplete-copy' -CopiedWithoutMetadata
    Run-Case 'failed-recovery' -FailInstall
    Run-Case 'unknown' -Unknown
    Run-Case 'foreign' -Type local -Foreign
    Run-Case 'malformed' -Malformed
    Run-Case 'trailing-slash' -Type link -TrailingSlash
    Run-Case 'linked' -Type link
    Run-Case 'copied' -Type local
    Run-Case 'skip' -Type link -SkipSql
    Run-Case 'offline' -Type link -Offline
    Run-Case 'dependency-failure' -Type link -FailNpm
    Run-Case 'interrupted' -Type link -Interrupt
    Run-Case 'blank-user' -Type link -BlankUser
    Run-Case 'quoted-blank' -Type link -SkipSql -QuotedBlank
    Write-Output 'PASS: 16 isolated setup journeys'
} finally {
    $resolved = [IO.Path]::GetFullPath($scratch)
    $boundary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture cleanup escaped temp directory' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
