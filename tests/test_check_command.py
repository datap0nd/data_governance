from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tools" / "check.ps1"
pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="the supported local PowerShell command uses a Windows checkout-owned .venv"
)


def run_check(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(CHECK), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def result_from(output: str) -> dict:
    result_line = next(line for line in output.splitlines() if line.startswith("Result: "))
    return json.loads(Path(result_line.removeprefix("Result: ")).read_text(encoding="utf-8-sig"))


def test_verify_requires_an_explicit_focused_selection():
    completed = run_check("-Mode", "Verify")
    assert completed.returncode != 0
    assert "Verify requires explicit -TestPath selectors" in completed.stderr
    result = result_from(completed.stdout)
    assert result["status"] == "failed"
    assert result["exit_code"] == 2


def test_full_suite_requires_a_recorded_diagnostic_reason():
    completed = run_check("-Mode", "Verify", "-Full")
    assert completed.returncode != 0
    assert "A local full suite is diagnostic-only" in completed.stderr
    result = result_from(completed.stdout)
    assert result["selection"]["full_suite"] is True
    assert result["selection"]["diagnostic_reason"] == ""


def test_command_declares_isolated_paths_before_pytest_launch():
    source = CHECK.read_text(encoding="utf-8")
    assignments = [
        "$env:TEMP = $tempRoot",
        "$env:TMP = $tempRoot",
        "$env:DG_DB_PATH = Join-Path $runRoot 'governance-test.db'",
        "$env:DG_TEST_RUN_ROOT = $runRoot",
        "$env:DG_BROWSER_PROFILE_ROOT = $profileRoot",
        "$env:DG_FLOWS_ROOT = $externalFlowRoot",
        "$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $repoRoot '.playwright-browsers'",
    ]
    launch = source.index("$pytestArguments =")
    assert all(source.index(assignment) < launch for assignment in assignments)
    assert "-ExecutionPolicy Bypass" in source[launch:]
