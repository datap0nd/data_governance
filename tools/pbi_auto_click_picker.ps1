<#
.SYNOPSIS
    Watches for the MSAL "Pick an account" dialog and clicks the first account tile.
    Spawned in the background by pbi_refresh_sync.ps1 / pbi_usage_sync.ps1 before
    Connect-PowerBIServiceAccount, so the picker is dismissed automatically when only
    one cached account is expected. Exits as soon as it clicks something or times out.
.PARAMETER TimeoutSeconds
    How long to wait for the dialog before giving up. Default: 60.
.PARAMETER LogPath
    Optional path to append log lines for debugging. Default: %TEMP%\dg_auto_click.log
#>
param(
    [int]$TimeoutSeconds = 60,
    [string]$LogPath = (Join-Path $env:TEMP "dg_auto_click.log")
)

function Write-Log {
    param([string]$Message)
    $line = "[$([DateTime]::Now.ToString('HH:mm:ss'))] $Message"
    Add-Content -Path $LogPath -Value $line -ErrorAction SilentlyContinue
}

Write-Log "Auto-click watcher started (timeout ${TimeoutSeconds}s)"

try {
    Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
    Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop
} catch {
    Write-Log "Failed to load UIAutomation: $_"
    exit 2
}

$root = [System.Windows.Automation.AutomationElement]::RootElement
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)

# Title fragments that identify the picker (case-insensitive contains)
$titleFragments = @(
    "Pick an account",
    "Sign in to your account",
    "Microsoft account"
)

# Names to skip when picking a tile (other-account / cancel buttons)
$skipPatterns = @(
    "another account",
    "different account",
    "use another",
    "cancel",
    "close",
    "back"
)

function Get-PickerWindow {
    param($rootElement, $fragments)
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Window
    )
    $windows = $rootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
    foreach ($w in $windows) {
        $name = $w.Current.Name
        if (-not $name) { continue }
        foreach ($f in $fragments) {
            if ($name -like "*$f*") { return $w }
        }
        # Also check descendants for a matching label (some pickers have generic window titles)
        foreach ($f in $fragments) {
            $textCond = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::NameProperty, $f
            )
            $hit = $w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $textCond)
            if ($hit) { return $w }
        }
    }
    return $null
}

function Find-FirstAccountTile {
    param($window, $skip)
    # Prefer ListItem (modern WAM picker uses these)
    $listItemCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::ListItem
    )
    $items = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $listItemCond)
    foreach ($it in $items) {
        $n = $it.Current.Name
        if (-not $n) { continue }
        $skipMatch = $false
        foreach ($s in $skip) { if ($n -match $s) { $skipMatch = $true; break } }
        if (-not $skipMatch) { return $it }
    }

    # Fall back to Buttons (older dialogs)
    $btnCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button
    )
    $btns = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
    foreach ($b in $btns) {
        $n = $b.Current.Name
        if (-not $n) { continue }
        $skipMatch = $false
        foreach ($s in $skip) { if ($n -match $s) { $skipMatch = $true; break } }
        if (-not $skipMatch -and $n.Length -gt 3) { return $b }
    }
    return $null
}

function Invoke-Tile {
    param($element)
    try {
        $pat = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pat.Invoke()
        return $true
    } catch {}
    try {
        $sel = $element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
        $sel.Select()
        Start-Sleep -Milliseconds 150
        $pat = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pat.Invoke()
        return $true
    } catch {}
    return $false
}

while ((Get-Date) -lt $deadline) {
    try {
        $picker = Get-PickerWindow -rootElement $root -fragments $titleFragments
        if ($picker) {
            Write-Log "Picker found: $($picker.Current.Name)"
            $tile = Find-FirstAccountTile -window $picker -skip $skipPatterns
            if ($tile) {
                $tileName = $tile.Current.Name
                Write-Log "Clicking tile: $tileName"
                if (Invoke-Tile -element $tile) {
                    Write-Log "Click invoked successfully."
                    exit 0
                } else {
                    Write-Log "Could not invoke tile via UIAutomation patterns."
                }
            } else {
                Write-Log "Picker present but no eligible tile found."
            }
        }
    } catch {
        Write-Log "Loop error: $_"
    }
    Start-Sleep -Milliseconds 500
}

Write-Log "Timed out after ${TimeoutSeconds}s without clicking."
exit 1
