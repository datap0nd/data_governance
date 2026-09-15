param()
$ErrorActionPreference = 'Stop'
Get-Command node -ErrorAction Stop | Out-Null
Get-Command npm.cmd -ErrorAction Stop | Out-Null
$geminiCommand = Get-Command gemini.cmd -ErrorAction SilentlyContinue
if (-not $geminiCommand) { $geminiCommand = Get-Command gemini -ErrorAction Stop }
$nodeMajor = [int]((& node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 22) { throw 'Node.js 22 or newer is required.' }
$geminiVersionOutput = & $geminiCommand.Source --version
if ($LASTEXITCODE -ne 0) { throw 'Gemini CLI could not report its version.' }
$geminiVersionMatch = [regex]::Match(($geminiVersionOutput -join "`n"), '(?m)^\s*(\d+\.\d+\.\d+)(?:[-+][^\s]+)?\s*$')
if (-not $geminiVersionMatch.Success -or [version]$geminiVersionMatch.Groups[1].Value -lt [version]'0.59.0') {
    throw 'Gemini CLI 0.59.0 or newer is required for this extension. Update Gemini CLI, then rerun the installer.'
}
Push-Location $PSScriptRoot
try {
    . (Join-Path $PSScriptRoot 'setup-functions.ps1')
    $geminiUserRoot = if ($env:GEMINI_CLI_HOME) { $env:GEMINI_CLI_HOME } else { $env:USERPROFILE }
    Invoke-MetronomeGeminiSetup -ExtensionRoot $PSScriptRoot -UserRoot $geminiUserRoot -RunNpm {
        & npm.cmd ci --ignore-scripts | Out-Host
        return $LASTEXITCODE
    } -RunGemini {
        param([string[]]$Arguments)
        & $geminiCommand.Source @Arguments | Out-Host
        return $LASTEXITCODE
    } -CheckService {
        param($Address)
        Invoke-RestMethod -Uri $Address -TimeoutSec 5 -MaximumRedirection 0
    }
} finally { Pop-Location }
