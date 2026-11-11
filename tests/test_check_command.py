from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check import reusable_result, summarize_junit


ROOT = Path(__file__).resolve().parents[1]
CHECK_PS1 = ROOT / "tools" / "check.ps1"
CHECK_PY = ROOT / "tools" / "check.py"
windows_only = pytest.mark.skipif(
    os.name != "nt", reason="the PowerShell command uses a Windows checkout-owned .venv"
)


def run_powershell_check(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(CHECK_PS1), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def run_python_check(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK_PY), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def result_from(output: str) -> dict:
    result_line = next(line for line in output.splitlines() if line.startswith("Result: "))
    return json.loads(Path(result_line.removeprefix("Result: ")).read_text(encoding="utf-8-sig"))


@windows_only
def test_full_suite_requires_a_recorded_diagnostic_reason():
    completed = run_powershell_check("-Mode", "Verify", "-Full")
    assert completed.returncode != 0
    assert "A local full suite is diagnostic-only" in completed.stderr
    result = result_from(completed.stdout)
    assert result["selection"]["full_suite"] is True
    assert result["selection"]["diagnostic_reason"] == ""


@windows_only
@pytest.mark.parametrize("arguments, expected", [
    (("-Mode", "Verify"), "Verify requires explicit -TestPath selectors"),
    (("-Mode", "Verify", "-Full"), "A local full suite is diagnostic-only"),
    (("-Mode", "Verify", "-TestPath", "tests/test_example.py"), "Verification Python was not found"),
    (("-Mode", "Preflight"), "Verification Python was not found"),
])
def test_powershell_validation_without_a_checkout_environment(tmp_path, arguments, expected):
    # Disposable script fixture only: never creates or borrows a Python environment.
    checkout = tmp_path / "checkout"
    (checkout / "tools").mkdir(parents=True)
    shutil.copy2(CHECK_PS1, checkout / "tools/check.ps1")
    subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "fixture"],
                   check=True, capture_output=True)
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(checkout / "tools/check.ps1"), *arguments],
        cwd=checkout, text=True, capture_output=True, timeout=30, check=False,
    )
    assert completed.returncode != 0
    assert expected in completed.stderr
    result = result_from(completed.stdout)
    assert result["status"] == "failed"
    assert result["exit_code"] == 2
    assert not (checkout / ".venv").exists()


def test_python_verify_requires_an_explicit_focused_selection():
    completed = run_python_check("verify")
    assert completed.returncode == 2
    assert "Verify requires explicit --test selectors" in completed.stderr
    result = result_from(completed.stdout)
    assert result["status"] == "failed"
    assert result["exit_code"] == 2
    assert result["schema_version"] == 1


def test_python_full_suite_requires_a_recorded_diagnostic_reason():
    completed = run_python_check("verify", "--full")
    assert completed.returncode == 2
    assert "A local full suite is diagnostic-only" in completed.stderr
    result = result_from(completed.stdout)
    assert result["selection"]["full_suite"] is True
    assert result["selection"]["diagnostic_reason"] is None


def test_python_result_records_the_shared_evidence_schema():
    completed = run_python_check("verify")
    result = result_from(completed.stdout)
    assert set(result) == {
        "schema_version",
        "run_id",
        "mode",
        "status",
        "revision",
        "source_fingerprint",
        "environment",
        "selection",
        "started_utc",
        "finished_utc",
        "duration_seconds",
        "exit_code",
        "reused_from",
        "artifacts",
        "test_summary",
        "diagnostic",
    }
    assert set(result["selection"]) == {"full_suite", "diagnostic_reason", "tests", "syntax"}
    assert result["revision"]


def test_python_keeps_the_scratch_root_after_a_failing_run(tmp_path, monkeypatch, capsys):
    import tools.check as check

    scratch = tmp_path / "MetronomeTestRuns" / "run-1"
    scratch.mkdir(parents=True)
    monkeypatch.setattr(check, "RUNS_ROOT", tmp_path / ".test-runs")
    monkeypatch.setattr(check, "isolation_base", lambda: tmp_path / "MetronomeTestRuns")
    run = check.Run("verify", {"full_suite": False, "diagnostic_reason": None, "tests": [], "syntax": []})
    run.external_root = scratch

    run.save("failed", 1, "something failed")
    assert scratch.exists()
    assert "Kept scratch for diagnosis" in capsys.readouterr().out

    run.save("passed", 0)
    assert not scratch.exists()


def test_python_reuse_requires_a_matching_fingerprint_and_selection(tmp_path, monkeypatch):
    prior = tmp_path / "20260101T000000000Z-1-aaaaaaaa" / "result.json"
    prior.parent.mkdir(parents=True)
    selection = {"full_suite": False, "diagnostic_reason": None, "tests": ["tests/test_a.py"], "syntax": []}
    prior.write_text(
        json.dumps(
            {"status": "passed", "source_fingerprint": "abc", "selection": selection}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.check.RUNS_ROOT", tmp_path)

    class FakeRun:
        result_path = tmp_path / "current" / "result.json"
        result = {"source_fingerprint": "abc", "selection": dict(selection)}

    assert reusable_result(FakeRun()) == str(prior)

    FakeRun.result = {"source_fingerprint": "changed", "selection": dict(selection)}
    assert reusable_result(FakeRun()) is None

    FakeRun.result = {
        "source_fingerprint": "abc",
        "selection": dict(selection, tests=["tests/test_b.py"]),
    }
    assert reusable_result(FakeRun()) is None


def test_python_summarizes_junit_counts(tmp_path):
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        '<testsuites><testsuite tests="4" failures="1" errors="0" skipped="2"/></testsuites>',
        encoding="utf-8",
    )
    assert summarize_junit(junit) == {"tests": 4, "failures": 1, "errors": 0, "skipped": 2}
    assert summarize_junit(tmp_path / "missing.xml") is None
