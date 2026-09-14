param([string]$ExtensionRoot)
$ErrorActionPreference = 'Stop'
. (Join-Path $ExtensionRoot 'setup-functions.ps1')
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('gemini-setup-' + [guid]::NewGuid())
function Assert-That($Value, $Message) { if (-not $Value) { throw $Message } }
function Run-Case([string]$Name, [string]$Type = '', [switch]$SkipSql, [switch]$Offline, [switch]$FailNpm, [switch]$Interrupt, [switch]$BlankUser, [switch]$QuotedBlank) {
    $userDir = Join-Path $scratch $Name
    $registration = Join-Path $userDir '.gemini/extensions/metronome-gemini'
    New-Item -ItemType Directory -Force -Path $registration | Out-Null
    $fixtureSettingsFile = Join-Path $registration '.env'
    [IO.File]::WriteAllText($fixtureSettingsFile, "METRONOME_FLOW_IDS=42`nMETRONOME_SITE_IDS=1`n")
    if ($Type) { @{ type = $Type; source = $ExtensionRoot } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $registration '.gemini-extension-install.json') }
    $calls = [Collections.Generic.List[string]]::new()
    $gemini = {
        param([string[]]$Arguments)
        $calls.Add(($Arguments -join '|'))
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
    catch { $failed = $true; if (-not ($FailNpm -or $Interrupt -or $BlankUser)) { throw } }
    Assert-That ($failed -eq [bool]($FailNpm -or $Interrupt -or $BlankUser)) 'Unexpected failure state'
    if ($FailNpm) { Assert-That ($calls.Count -eq 0) 'Dependency failure invoked Gemini'; return }
    $access = Get-Content -LiteralPath (Join-Path $userDir '.gemini/metronome/access.json') -Raw | ConvertFrom-Json
    Assert-That ($access.METRONOME_FLOW_IDS -eq '42' -and $access.METRONOME_SITE_IDS -eq '1') 'Existing scopes changed'
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
}
try {
    Run-Case 'fresh'
    Run-Case 'linked' -Type link
    Run-Case 'copied' -Type local
    Run-Case 'skip' -Type link -SkipSql
    Run-Case 'offline' -Type link -Offline
    Run-Case 'dependency-failure' -Type link -FailNpm
    Run-Case 'interrupted' -Type link -Interrupt
    Run-Case 'blank-user' -Type link -BlankUser
    Run-Case 'quoted-blank' -Type link -SkipSql -QuotedBlank
    Write-Output 'PASS: 9 isolated setup journeys'
} finally {
    $resolved = [IO.Path]::GetFullPath($scratch)
    $boundary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture cleanup escaped temp directory' }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
