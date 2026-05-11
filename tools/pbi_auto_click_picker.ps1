<#
.SYNOPSIS
    Watches for the MSAL account picker and selects a cached account.
    Spawned in the background before Connect-PowerBIServiceAccount. It keeps
    watching after the account tile click so it can also press simple follow-up
    prompts such as Continue, Next, Yes, or OK.
.PARAMETER TimeoutSeconds
    How long to watch before giving up. Default: 60.
.PARAMETER PreferredAccount
    Optional account hint, usually an email address. Defaults to DG_PBI_ACCOUNT.
.PARAMETER LogPath
    Optional path to append log lines for debugging. Default: %TEMP%\dg_auto_click.log
#>
param(
    [int]$TimeoutSeconds = 60,
    [string]$PreferredAccount = $env:DG_PBI_ACCOUNT,
    [string]$LogPath = (Join-Path $env:TEMP "dg_auto_click.log")
)

function Write-Log {
    param([string]$Message)
    $line = "[$([DateTime]::Now.ToString('HH:mm:ss'))] $Message"
    Add-Content -Path $LogPath -Value $line -ErrorAction SilentlyContinue
}

function Get-LoggedInAccountHint {
    try {
        $upn = (& whoami /upn 2>$null).Trim()
        if ($upn -match "^[^@\s]+@[^@\s]+\.[^@\s]+$") {
            return $upn
        }
    } catch {}
    return ""
}

if (-not $PreferredAccount) {
    $PreferredAccount = Get-LoggedInAccountHint
}

Write-Log "Auto-click watcher started (timeout ${TimeoutSeconds}s, preferred '$PreferredAccount')"

try {
    Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
    Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop
} catch {
    Write-Log "Failed to load UIAutomation: $_"
    exit 2
}

try {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class DgMouse {
    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(int flags, int dx, int dy, int data, UIntPtr extraInfo);

    public const int LeftDown = 0x0002;
    public const int LeftUp = 0x0004;
}
"@ -ErrorAction Stop
} catch {
    Write-Log "Mouse fallback unavailable: $_"
}

$root = [System.Windows.Automation.AutomationElement]::RootElement
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$trueCondition = [System.Windows.Automation.Condition]::TrueCondition
$clickedKeys = @{}
$clickedSomething = $false

$titleFragments = @(
    "Pick an account",
    "Choose an account",
    "Sign in to your account",
    "Microsoft account",
    "Work or school account",
    "Use this account",
    "Let this app access",
    "Stay signed in"
)

$skipPatterns = @(
    "another account",
    "different account",
    "use another",
    "create account",
    "cancel",
    "close",
    "back",
    "privacy",
    "terms"
)

$continuePatterns = @(
    "continue",
    "next",
    "yes",
    "ok",
    "sign in",
    "use this account"
)

function Get-ElementName {
    param($Element)
    try { return [string]$Element.Current.Name } catch { return "" }
}

function Get-ControlTypeName {
    param($Element)
    try { return [string]$Element.Current.ControlType.ProgrammaticName } catch { return "" }
}

function Test-ContainsAny {
    param([string]$Value, [string[]]$Needles)
    if (-not $Value) { return $false }
    foreach ($needle in $Needles) {
        if ($Value.IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
}

function Test-SkipName {
    param([string]$Name)
    return (Test-ContainsAny -Value $Name -Needles $skipPatterns)
}

function Get-Elements {
    param($Window)
    $items = New-Object System.Collections.ArrayList
    [void]$items.Add($Window)
    try {
        $desc = $Window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $trueCondition)
        foreach ($el in $desc) { [void]$items.Add($el) }
    } catch {
        Write-Log "Could not enumerate descendants: $_"
    }
    return $items
}

function Test-WindowLooksLikePicker {
    param($Window)
    $name = Get-ElementName $Window
    if (Test-ContainsAny -Value $name -Needles $titleFragments) { return $true }

    $checked = 0
    foreach ($el in (Get-Elements $Window)) {
        $checked += 1
        if ($checked -gt 500) { break }
        $childName = Get-ElementName $el
        if (Test-ContainsAny -Value $childName -Needles $titleFragments) { return $true }
    }
    return $false
}

function Get-PickerWindow {
    try {
        $children = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $trueCondition)
        foreach ($w in $children) {
            if (Test-WindowLooksLikePicker -Window $w) { return $w }
        }
    } catch {
        Write-Log "Could not scan top-level windows: $_"
    }
    return $null
}

function Get-ClickableAncestor {
    param($Element)
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $current = $Element
    for ($i = 0; $i -lt 6 -and $current; $i++) {
        try {
            $null = $current.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
            return $current
        } catch {}
        try {
            $null = $current.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
            return $current
        } catch {}
        try {
            $current = $walker.GetParent($current)
        } catch {
            $current = $null
        }
    }
    return $Element
}

function Get-RectKey {
    param($Element)
    try {
        $r = $Element.Current.BoundingRectangle
        return "$([int]$r.Left),$([int]$r.Top),$([int]$r.Width),$([int]$r.Height)"
    } catch {
        return "no-rect"
    }
}

function Invoke-Element {
    param($Element)
    $target = Get-ClickableAncestor -Element $Element
    try { $target.SetFocus() } catch {}

    try {
        $pat = $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $pat.Invoke()
        return $true
    } catch {}

    try {
        $sel = $target.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
        $sel.Select()
        Start-Sleep -Milliseconds 150
        try {
            $pat = $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
            $pat.Invoke()
        } catch {}
        return $true
    } catch {}

    if ("DgMouse" -as [type]) {
        try {
            $r = $target.Current.BoundingRectangle
            if ($r.Width -gt 1 -and $r.Height -gt 1) {
                $x = [int]($r.Left + ($r.Width / 2))
                $y = [int]($r.Top + ($r.Height / 2))
                [DgMouse]::SetCursorPos($x, $y) | Out-Null
                Start-Sleep -Milliseconds 80
                [DgMouse]::mouse_event([DgMouse]::LeftDown, 0, 0, 0, [UIntPtr]::Zero)
                Start-Sleep -Milliseconds 80
                [DgMouse]::mouse_event([DgMouse]::LeftUp, 0, 0, 0, [UIntPtr]::Zero)
                return $true
            }
        } catch {
            Write-Log "Mouse fallback failed: $_"
        }
    }
    return $false
}

function Find-PreferredAccount {
    param($Window)
    if (-not $PreferredAccount) { return $null }
    foreach ($el in (Get-Elements $Window)) {
        $name = Get-ElementName $el
        if ($name -and -not (Test-SkipName $name) -and
            $name.IndexOf($PreferredAccount, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $el
        }
    }
    return $null
}

function Find-EmailAccount {
    param($Window)
    foreach ($el in (Get-Elements $Window)) {
        $name = Get-ElementName $el
        if (-not $name -or (Test-SkipName $name)) { continue }
        if ($name -match "[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}") {
            return $el
        }
    }
    return $null
}

function Find-ListAccount {
    param($Window)
    foreach ($el in (Get-Elements $Window)) {
        $name = Get-ElementName $el
        if (-not $name -or (Test-SkipName $name)) { continue }
        $type = Get-ControlTypeName $el
        if ($type -in @("ControlType.ListItem", "ControlType.DataItem", "ControlType.Custom") -and $name.Length -gt 3) {
            return $el
        }
    }
    return $null
}

function Find-ContinueButton {
    param($Window)
    foreach ($el in (Get-Elements $Window)) {
        $name = Get-ElementName $el
        if (-not $name -or (Test-SkipName $name)) { continue }
        $type = Get-ControlTypeName $el
        if ($type -eq "ControlType.Button" -and (Test-ContainsAny -Value $name -Needles $continuePatterns)) {
            return $el
        }
    }
    return $null
}

function Find-ClickTarget {
    param($Window)
    $candidate = Find-PreferredAccount -Window $Window
    if ($candidate) { return $candidate }
    $candidate = Find-EmailAccount -Window $Window
    if ($candidate) { return $candidate }
    $candidate = Find-ListAccount -Window $Window
    if ($candidate) { return $candidate }
    return Find-ContinueButton -Window $Window
}

while ((Get-Date) -lt $deadline) {
    try {
        $picker = Get-PickerWindow
        if ($picker) {
            Write-Log "Picker found: $(Get-ElementName $picker)"
            $target = Find-ClickTarget -Window $picker
            if ($target) {
                $name = Get-ElementName $target
                $key = "$(Get-ControlTypeName $target)|$name|$(Get-RectKey $target)"
                $lastClick = $clickedKeys[$key]
                if (-not $lastClick -or ((Get-Date) - $lastClick).TotalSeconds -gt 2) {
                    Write-Log "Clicking: $name"
                    if (Invoke-Element -Element $target) {
                        $clickedSomething = $true
                        $clickedKeys[$key] = Get-Date
                        Start-Sleep -Milliseconds 900
                    } else {
                        Write-Log "Could not invoke or click target: $name"
                    }
                }
            } else {
                Write-Log "Picker present but no eligible account/button found."
            }
        } elseif ($clickedSomething) {
            Write-Log "Picker gone after click."
            exit 0
        }
    } catch {
        Write-Log "Loop error: $_"
    }
    Start-Sleep -Milliseconds 500
}

if ($clickedSomething) {
    Write-Log "Timed out after clicking at least once."
    exit 0
}

Write-Log "Timed out after ${TimeoutSeconds}s without clicking."
exit 1
