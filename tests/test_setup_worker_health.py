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
