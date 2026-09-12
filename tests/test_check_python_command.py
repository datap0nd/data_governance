"""The cross-platform verifier mirrors tools/check.ps1 on every operating system."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import check

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tools" / "check.py"


def run_check(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def result_from(output: str) -> dict:
    result_line = next(line for line in output.splitlines() if line.startswith("Result: "))
    return json.loads(Path(result_line.removeprefix("Result: ")).read_text(encoding="utf-8-sig"))


def test_verify_requires_an_explicit_focused_selection():
    completed = run_check("verify")
    assert completed.returncode == 2
    assert "Verify requires explicit --test selectors" in completed.stderr
    result = result_from(completed.stdout)
    assert result["status"] == "failed"
    assert result["exit_code"] == 2
    assert result["mode"] == "verify"
    assert result["schema_version"] == 1


def test_full_suite_requires_a_recorded_diagnostic_reason():
    completed = run_check("verify", "--full")
    assert completed.returncode == 2
    assert "A local full suite is diagnostic-only" in completed.stderr
    result = result_from(completed.stdout)
    assert result["selection"]["full_suite"] is True
    assert result["selection"]["diagnostic_reason"] == ""


def test_selection_validation_matches_the_powershell_rules():
    assert check.validate_selection(["tests/test_x.py::t"], False, None) == ["tests/test_x.py::t"]
    assert check.validate_selection([], True, "equivalence question") == ["tests"]
    with pytest.raises(check.CheckError):
        check.validate_selection([], False, None)
    with pytest.raises(check.CheckError):
        check.validate_selection(["tests/test_x.py"], True, "  ")


def test_isolated_environment_keeps_every_path_disposable(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    run_root = tmp_path / ".test-runs" / "run-1"
    env = check.isolated_environment(tmp_path, run_root, "run-1")
    for key in ("TEMP", "TMP", "TMPDIR", "DG_DB_PATH", "DG_TEST_RUN_ROOT", "DG_BROWSER_PROFILE_ROOT"):
        assert Path(env[key]).is_relative_to(run_root)
    assert Path(env["DG_FLOWS_ROOT"]).is_relative_to(check.isolation_base() / "run-1")
    assert not Path(env["DG_FLOWS_ROOT"]).is_relative_to(tmp_path)
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / ".playwright-browsers")


def test_isolated_environment_respects_a_preinstalled_browser_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    env = check.isolated_environment(tmp_path, tmp_path / "run", "run")
    assert "PLAYWRIGHT_BROWSERS_PATH" not in env


def test_cleanup_refuses_roots_outside_the_isolation_base(tmp_path):
    base = tmp_path / "MetronomeTestRuns"
    inside = base / "run-1"
    inside.mkdir(parents=True)
    check.cleanup_isolation_root(inside, base)
    assert not inside.exists()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(check.CheckError, match="Refusing to clean unexpected Flow test root"):
        check.cleanup_isolation_root(outside, base)
    assert outside.exists()


def test_reuse_requires_a_passed_result_with_the_same_fingerprint_and_selection(tmp_path):
    runs = tmp_path / ".test-runs"
    selection = {"full_suite": False, "diagnostic_reason": "", "tests": ["tests/test_a.py"], "syntax": []}

    def write(name: str, **overrides):
        record = {"status": "passed", "source_fingerprint": "abc", "selection": selection, **overrides}
        target = runs / name / "result.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(record), encoding="utf-8")
        return target

    write("failed", status="failed")
    write("other-source", source_fingerprint="zzz")
    write("other-selection", selection={**selection, "tests": ["tests/test_b.py"]})
    current = write("current")
    match = write("match")
    assert check.find_reusable_result(tmp_path, "abc", selection, current) == match
    assert check.find_reusable_result(tmp_path, "nope", selection, current) is None


def test_assert_python313_rejects_missing_wrong_and_bundled_interpreters(tmp_path):
    with pytest.raises(check.CheckError, match="was not found"):
        check.assert_python313(tmp_path / "missing" / "python", "Verification")
    bundled = tmp_path / "codex-runtimes" / "python"
    bundled.parent.mkdir()
    bundled.write_text("", encoding="utf-8")
    with pytest.raises(check.CheckError, match="bundled coding-agent Python runtime"):
        check.assert_python313(bundled, "Setup")
    if sys.version_info[:2] == (3, 13):
        assert check.assert_python313(Path(sys.executable), "Setup").startswith("3.13.")
    else:
        with pytest.raises(check.CheckError, match="requires Python 3.13"):
            check.assert_python313(Path(sys.executable), "Setup")


def test_junit_summary_adds_every_suite(tmp_path):
    report = tmp_path / "pytest.xml"
    report.write_text(
        '<testsuites><testsuite tests="3" failures="1" errors="0" skipped="1"/>'
        '<testsuite tests="2" failures="0" errors="1" skipped="0"/></testsuites>',
        encoding="utf-8",
    )
    assert check.junit_summary(report) == {"tests": 5, "failures": 1, "errors": 1, "skipped": 1}


def test_source_fingerprint_changes_with_source_and_selection(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    app = tmp_path / "app"
    app.mkdir()
    (app / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "requirements-ci.lock").write_text("pytest==9.1.1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "app"], check=True)
    first = check.source_fingerprint(tmp_path, ["tests/test_a.py::case"], [])
    assert first == check.source_fingerprint(tmp_path, ["tests/test_a.py::case"], [])
    assert first != check.source_fingerprint(tmp_path, ["tests/test_b.py::case"], [])
    (app / "module.py").write_text("x = 2\n", encoding="utf-8")
    assert first != check.source_fingerprint(tmp_path, ["tests/test_a.py::case"], [])


@pytest.mark.skipif(os.name == "nt", reason="the POSIX .venv layout is what Linux and macOS sessions use")
def test_venv_python_uses_the_posix_layout_off_windows(tmp_path):
    assert check.venv_python(tmp_path) == tmp_path / ".venv" / "bin" / "python"
