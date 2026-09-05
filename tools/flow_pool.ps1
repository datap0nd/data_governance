# Pure configuration: dot-sourcing this file never starts or changes a service.
function Get-MetronomeFlowSlot {
    param([ValidateRange(1,5)][int]$Slot, [string]$BaseProfile)
    $Suffix = if ($Slot -eq 1) { '' } else { "-$Slot" }
    $ServiceSuffix = if ($Slot -eq 1) { '' } else { "$Slot" }
    [pscustomobject]@{
        Slot = $Slot
        WorkerId = "bi-desktop-headless$Suffix"
        ServiceName = "MXFlowsWorker$ServiceSuffix"
        Profile = "$BaseProfile$Suffix"
        LogName = "flow_worker$ServiceSuffix"
    }
}
