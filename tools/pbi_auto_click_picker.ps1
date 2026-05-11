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
    $win32Type = @"
using System;
using System.Runtime.InteropServices;

public static class DgWin32 {
    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(int flags, int dx, int dy, int data, UIntPtr extraInfo);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    public const int LeftDown = 0x0002;
    public const int LeftUp = 0x0004;
    public const int Restore = 9;
}
"@
    Add-Type -TypeDefinition $win32Type -ErrorAction Stop
} catch {
    Write-Log "Win32 fallback unavailable: $_"
}

try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
} catch {
    Write-Log "Keyboard fallback unavailable: $_"
}

$root = [System.Windows.Automation.AutomationElement]::RootElement
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$trueCondition = [System.Windows.Automation.Condition]::TrueCondition
$clickedKeys = @{}
$clickedSomething = $false
$fallbackAttempts = 0
$lastFallbackAt = Get-Date "2000-01-01"

$titleFragments = @(
    "Pick an account",
    "Choose an account",
    "Select an account",
    "Sign in to your account",
    "Sign in",
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
    if ($name -eq "Microsoft") { return $true }

    $checked = 0
    foreach ($el in (Get-Elements $Window)) {
        $checked += 1
        if ($checked -gt 500) { break }
        $childName = Get-ElementName $el
        if (Test-ContainsAny -Value $childName -Needles $titleFragments) { return $true }
    }
    return $false
}

function Get-PickerWindowFromProcessTitles {
    try {
        $processes = Get-Process | Where-Object { $_.MainWindowHandle -and $_.MainWindowHandle -ne 0 }
        foreach ($p in $processes) {
            $title = [string]$p.MainWindowTitle
            if (-not $title) { continue }
            if ((Test-ContainsAny -Value $title -Needles $titleFragments) -or $title -eq "Microsoft") {
                try {
                    $window = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
                    if ($window) {
                        Write-Log "Picker found by process title: $title ($($p.ProcessName))"
                        return $window
                    }
                } catch {
                    Write-Log "Could not bind process title window '$title': $_"
                }
            }
        }
    } catch {
        Write-Log "Could not scan process window titles: $_"
    }
    return $null
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
    return Get-PickerWindowFromProcessTitles
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

function Focus-PickerWindow {
    param($Window)
    try { $Window.SetFocus() } catch {}
    try {
        $handle = [IntPtr]$Window.Current.NativeWindowHandle
        if ($handle -ne [IntPtr]::Zero -and ("DgWin32" -as [type])) {
            [DgWin32]::ShowWindow($handle, [DgWin32]::Restore) | Out-Null
            [DgWin32]::SetForegroundWindow($handle) | Out-Null
        }
    } catch {
        Write-Log "Could not focus picker: $_"
    }
    Start-Sleep -Milliseconds 200
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

    if ("DgWin32" -as [type]) {
        try {
            $r = $target.Current.BoundingRectangle
            if ($r.Width -gt 1 -and $r.Height -gt 1) {
                $x = [int]($r.Left + ($r.Width / 2))
                $y = [int]($r.Top + ($r.Height / 2))
                [DgWin32]::SetCursorPos($x, $y) | Out-Null
                Start-Sleep -Milliseconds 80
                [DgWin32]::mouse_event([DgWin32]::LeftDown, 0, 0, 0, [UIntPtr]::Zero)
                Start-Sleep -Milliseconds 80
                [DgWin32]::mouse_event([DgWin32]::LeftUp, 0, 0, 0, [UIntPtr]::Zero)
                return $true
            }
        } catch {
            Write-Log "Mouse fallback failed: $_"
        }
    }
    return $false
}

function Invoke-KeyboardFallback {
    param($Window, [int]$Attempt)
    if (-not ("System.Windows.Forms.SendKeys" -as [type])) { return $false }
    Focus-PickerWindow -Window $Window
    try {
        if ($Attempt -eq 1) {
            Write-Log "Fallback: sending Enter to focused picker."
            [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
        } elseif ($Attempt -eq 2) {
            Write-Log "Fallback: sending Tab then Enter to picker."
            [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
            Start-Sleep -Milliseconds 120
            [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
        } else {
            Write-Log "Fallback: sending Down then Enter to picker."
            [System.Windows.Forms.SendKeys]::SendWait("{DOWN}")
            Start-Sleep -Milliseconds 120
            [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
        }
        return $true
    } catch {
        Write-Log "Keyboard fallback failed: $_"
        return $false
    }
}

function Invoke-WindowClickFallback {
    param($Window, [int]$Attempt)
    if (-not ("DgWin32" -as [type])) { return $false }
    Focus-PickerWindow -Window $Window
    try {
        $r = $Window.Current.BoundingRectangle
        if ($r.Width -le 1 -or $r.Height -le 1) { return $false }

        $x = [int]($r.Left + ($r.Width * 0.50))
        if ($Attempt -le 3) {
            $y = [int]($r.Top + ($r.Height * 0.38))
        } elseif ($Attempt -eq 4) {
            $y = [int]($r.Top + ($r.Height * 0.48))
        } else {
            $y = [int]($r.Top + ($r.Height * 0.58))
        }

        Write-Log "Fallback: clicking picker at $x,$y."
        [DgWin32]::SetCursorPos($x, $y) | Out-Null
        Start-Sleep -Milliseconds 80
        [DgWin32]::mouse_event([DgWin32]::LeftDown, 0, 0, 0, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 80
        [DgWin32]::mouse_event([DgWin32]::LeftUp, 0, 0, 0, [UIntPtr]::Zero)
        return $true
    } catch {
        Write-Log "Window click fallback failed: $_"
        return $false
    }
}

function Invoke-FallbackClick {
    param($Window)
    $script:fallbackAttempts += 1
    if ($script:fallbackAttempts -le 2) {
        return (Invoke-KeyboardFallback -Window $Window -Attempt $script:fallbackAttempts)
    }
    if (Invoke-WindowClickFallback -Window $Window -Attempt $script:fallbackAttempts) {
        return $true
    }
    return (Invoke-KeyboardFallback -Window $Window -Attempt $script:fallbackAttempts)
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
            Focus-PickerWindow -Window $picker
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
                        if (((Get-Date) - $lastFallbackAt).TotalSeconds -gt 1.5) {
                            $lastFallbackAt = Get-Date
                            if (Invoke-FallbackClick -Window $picker) {
                                $clickedSomething = $true
                                Start-Sleep -Milliseconds 900
                            }
                        }
                    }
                }
            } else {
                Write-Log "Picker present but no eligible account/button found."
                if (((Get-Date) - $lastFallbackAt).TotalSeconds -gt 1.5) {
                    $lastFallbackAt = Get-Date
                    if (Invoke-FallbackClick -Window $picker) {
                        $clickedSomething = $true
                        Start-Sleep -Milliseconds 900
                    }
                }
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
