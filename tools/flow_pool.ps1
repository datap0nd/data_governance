# Pure configuration: dot-sourcing this file never starts or changes a service.
function Get-MetronomeFlowSlot {
    param([ValidateRange(1,5)][int]$Slot, [string]$BaseProfile, [switch]$Headed)
    $Suffix = if ($Slot -eq 1) { '' } else { "-$Slot" }
    $ServiceSuffix = if ($Slot -eq 1) { '' } else { "$Slot" }
    $Mode = if ($Headed) { 'headed' } else { 'headless' }
    [pscustomobject]@{
        Slot = $Slot
        WorkerId = "bi-desktop-$Mode$Suffix"
        ServiceName = "MXFlowsWorker$ServiceSuffix"
        TaskName = "Metronome_Flows_Headed$ServiceSuffix"
        Profile = "$BaseProfile$Suffix"
        LogName = "flow_worker$ServiceSuffix"
    }
}
