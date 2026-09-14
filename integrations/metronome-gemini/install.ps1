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
    & npm.cmd ci --ignore-scripts
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; no Gemini settings were changed.' }
    # Link only this extension. Preserve the user's models, other MCPs and skills.
    & $geminiCommand.Source extensions link $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw 'Gemini could not link the extension. Check its displayed error.' }
    Write-Host 'Installed. Restart Gemini in your reporting folder with: gemini --model gemini-3.5-flash'
    Write-Host 'Use /metronome or /html_replicate "C:\Reports\Your Folder".'
    Write-Host 'Optional reader settings: gemini extensions config metronome-gemini'
} finally { Pop-Location }
