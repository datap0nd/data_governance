param(
    [Parameter(Position=0)][string]$Command = 'check',
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:DO_NOT_TRACK = '1'
$env:HYPERFRAMES_TELEMETRY_DISABLED = '1'
$env:HYPERFRAMES_SKIP_SKILLS = '1'
$env:HYPERFRAMES_RUN_ID = 'metronome-in-sync'
$cli = Join-Path $PSScriptRoot 'node_modules\.bin\hyperframes.cmd'
if (-not (Test-Path -LiteralPath $cli)) {
    throw 'HyperFrames is not installed. Run npm ci in media/metronome-in-sync first.'
}
& $cli $Command @Arguments
exit $LASTEXITCODE
