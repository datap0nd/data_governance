"""Windows PowerShell regression coverage for setup worker health checks."""

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "tools" / "setup_flow_worker_health.ps1"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 response semantics")
def test_worker_health_flattens_rest_array_and_rejects_bad_timestamps():
    helper_path = str(HELPERS).replace("'", "''")
    command = rf"""
. '{helper_path}'
function Invoke-RestMethod {{
    param($Uri, $TimeoutSec)
    $Rows = @(
        [pscustomobject]@{{ worker_id = 'worker-1'; status = 'idle'; last_seen_at = '2026-09-19T16:00:01' }},
        [pscustomobject]@{{ worker_id = 'worker-2'; status = 'idle'; last_seen_at = '2026-09-19T16:00:02' }}
    )
    Write-Output -NoEnumerate $Rows
}}
$Workers = @(Get-MetronomeSetupFlowWorkers -Port 8765)
if ($Workers.Count -ne 2) {{ throw "Expected two flattened workers; got $($Workers.Count)." }}
$StartedAt = [datetime]'2026-09-19T16:00:00'
if (-not (Test-MetronomeSetupWorkerFresh -Worker $Workers[0] -WorkerStartedAt $StartedAt)) {{
    throw 'A valid recent worker was rejected.'
}}
$Malformed = [pscustomobject]@{{ status = 'idle'; last_seen_at = @('bad', 'timestamp') }}
if (Test-MetronomeSetupWorkerFresh -Worker $Malformed -WorkerStartedAt $StartedAt) {{
    throw 'An array timestamp was accepted.'
}}
Write-Output 'PASS'
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


SETUP = ROOT / "setup.ps1"


def _fake_nssm(folder: Path, name: str, stream: str, text: str, code: int) -> Path:
    """A stand-in for nssm.exe that prints one line and exits with ``code``."""
    path = folder / f"{name}.cmd"
    redirect = " 1>&2" if stream == "stderr" else ""
    path.write_text(f"@echo off\r\necho {text}{redirect}\r\nexit /b {code}\r\n", encoding="ascii")
    return path


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 native stderr semantics")
def test_a_slow_service_start_no_longer_stops_setup(tmp_path):
    # Reported from a work PC: setup stopped with "SETUP FAILED: MXFlowsWorker3:
    # Unexpected status SERVICE_START_PENDING in response to START control." at
    # the slot-start line, before Metronome itself was started.
    pending = _fake_nssm(tmp_path, "pending", "stderr",
                         "MXFlowsWorker3: Unexpected status SERVICE_START_PENDING in response to START control.", 1)
    running = _fake_nssm(tmp_path, "running", "stderr", "MXFlowsWorker: An instance of the service is already running.", 1)
    started = _fake_nssm(tmp_path, "started", "stdout", "MXFlowsWorker2: START: The operation completed successfully.", 0)
    broken = _fake_nssm(tmp_path, "broken", "stderr", "MXFlowsWorker4: Unexpected status SERVICE_PAUSED in response to START control.", 1)
    helper_path = str(HELPERS).replace("'", "''")
    command = rf"""
$ErrorActionPreference = 'Stop'
. '{helper_path}'
# The line setup used: under 'Stop' the redirected stderr line ends the script.
$OldLineStopped = $false
try {{ & '{pending}' start MXFlowsWorker3 2>&1 | Out-Null }} catch {{ $OldLineStopped = "$_" -match 'SERVICE_START_PENDING' }}
if (-not $OldLineStopped) {{ throw 'The old start line did not stop on NSSM stderr; the regression is not reproduced.' }}
$Cases = @(
    @('{pending}', 'MXFlowsWorker3', 'Starting'),
    @('{running}', 'MXFlowsWorker', 'Running'),
    @('{started}', 'MXFlowsWorker2', 'Started'),
    @('{broken}', 'MXFlowsWorker4', 'Failed')
)
foreach ($Case in $Cases) {{
    $Result = Start-MetronomeSetupService -NssmExe $Case[0] -Name $Case[1]
    if ($Result.State -ne $Case[2]) {{ throw "$($Case[1]): expected $($Case[2]), got $($Result.State) ($($Result.Message))" }}
    Write-MetronomeSetupServiceStart $Result
}}
if ($ErrorActionPreference -ne 'Stop') {{ throw 'The helper changed the caller''s error preference.' }}
Write-Output 'PASS'
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "PASS" in result.stdout
    assert "MXFlowsWorker3 is still starting; setup carries on" in result.stdout
    assert "MXFlowsWorker was already running - OK." in result.stdout
    assert "WARNING: MXFlowsWorker4 did not start: MXFlowsWorker4: Unexpected status SERVICE_PAUSED" in result.stdout
    assert "MXFlowsWorker2" not in result.stdout  # a clean start prints nothing


def test_setup_starts_every_service_through_the_tolerant_helper():
    script = SETUP.read_text(encoding="utf-8")
    assert "& $NssmExe start" not in script, "start services with Start-MetronomeSetupService"
    first_start = script.index("Start-MetronomeSetupService -NssmExe")
    assert script.index(". $FlowWorkerHealthHelpers") < first_start
    for name in ("$FlowServiceName", "$FlowSlot.ServiceName", "$AuditorServiceName", "$ServiceName)"):
        assert f"Start-MetronomeSetupService -NssmExe $NssmExe -Name {name}" in script
    helpers = HELPERS.read_text(encoding="utf-8")
    assert "function Start-MetronomeSetupService" in helpers
    assert "$ErrorActionPreference = 'Continue'" in helpers.split("function Start-MetronomeSetupService", 1)[1]
