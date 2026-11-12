# Pure configuration: dot-sourcing this file never starts or changes a service.
function Get-MetronomeFlowSlot {
    param([ValidateRange(1,32)][int]$Slot, [string]$BaseProfile, [switch]$Headed)
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

# The worker browsers setup signs in: the first $Limit background slots and
# the first $Limit visible slots, slot 1 of each first. Signing in every
# configured slot for every portal made each setup run take many minutes; the
# other workers sign in by themselves, with the saved portal sign-in, the
# first time a Flow needs a portal.
function Get-MetronomeSignInProfiles {
    param([object[]]$FlowSlots = @(), [object[]]$HeadedSlots = @(), [ValidateRange(1,32)][int]$Limit = 2)
    $Pools = @(
        @{ Slots = $FlowSlots;   Mode = 'headless'; First = 'headless service' },
        @{ Slots = $HeadedSlots; Mode = 'headed';   First = 'headed on-demand' }
    )
    foreach ($Slot in 1..$Limit) {
        foreach ($Pool in $Pools) {
            $Found = @($Pool.Slots | Where-Object { $_.Slot -eq $Slot })
            if (-not $Found.Count) { continue }
            $Label = if ($Slot -eq 1) { $Pool.First } else { "$($Pool.Mode) slot $Slot" }
            @{ Path = $Found[0].Profile; Label = $Label }
        }
    }
}
