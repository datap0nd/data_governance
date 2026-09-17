"""Check the deployed direct-file entry point without launching a worker."""
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from app import database, flow_worker
from test_flows import flow_db  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def test_isolated_direct_worker_entrypoint_from_another_directory(tmp_path):
    worker = ROOT / 'app' / 'flow_worker.py'
    # Embedded Python ignores the working directory/PYTHONPATH. Importing the
    # module inside pytest hides import-order failures in this deployed path.
    result = subprocess.run(
        [sys.executable, '-I', str(worker), '--help'], cwd=tmp_path,
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert '--headed' in result.stdout
    assert '--profile-dir' in result.stdout
    assert not list(tmp_path.iterdir()), '--help must not start a worker or create a profile'


def test_authenticate_helper_reads_the_browser_channel_from_the_database_when_the_api_is_down(
    flow_db, tmp_path, monkeypatch, capsys,
):
    calls = []
    monkeypatch.setattr(flow_worker, "authenticate_site", lambda *args: calls.append(args))
    probes = []

    def refuse(client, method, path, body=None, attempts=5):
        probes.append((method, path, attempts, str(client.base_url)))
        # setup.ps1 runs the helper while the MXAnalytics service is stopped.
        raise httpx.ConnectError("[WinError 10061] No connection could be made")

    monkeypatch.setattr(flow_worker, "_api", refuse)
    monkeypatch.setattr(sys, "argv", [
        "flow_worker.py", "--profile-dir", str(tmp_path / "profile"), "--server", "http://127.0.0.1:8000",
        "--authenticate-url", "https://asap.example.test/", "--authenticate-adapter", "asap_portal",
    ])
    flow_worker.main()
    # The flow_db fixture saves Microsoft Edge as the Flows browser.
    assert calls == [(tmp_path / "profile", "https://asap.example.test/", 10, "asap_portal", "msedge")]
    assert probes == [("GET", "/api/system/flows", 1, "http://127.0.0.1:8000")]
    captured = capsys.readouterr()
    assert "Browser channel 'msedge' read from the local database" in captured.err
    assert "http://127.0.0.1:8000 is unreachable" in captured.err and "10061" in captured.err

    # A reachable API still wins, silently.
    calls.clear()
    monkeypatch.setattr(flow_worker, "_api", lambda client, method, path, body=None, attempts=5: {"browser_channel": "chrome"})
    flow_worker.main()
    assert calls == [(tmp_path / "profile", "https://asap.example.test/", 10, "asap_portal", "chrome")]
    assert capsys.readouterr().err == ""

    # Without the database the helper fails closed instead of creating an empty one.
    calls.clear()
    monkeypatch.setattr(flow_worker, "_api", refuse)
    missing = tmp_path / "elsewhere" / "governance.db"
    monkeypatch.setattr(database, "DB_PATH", str(missing))
    with pytest.raises(RuntimeError, match="no database exists at .*governance.db. Start the MXAnalytics service or set DG_DB_PATH"):
        flow_worker.main()
    assert calls == [] and not missing.exists() and not missing.parent.exists()


def test_setup_elevation_window_is_visible_when_interactive_and_the_helper_sees_the_database():
    # No PowerShell on Linux runners: check the exact lines setup.ps1 must carry.
    source = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    elevation = source[source.index("# --- Self-elevate to Admin if needed ---"):source.index('$ErrorActionPreference = "Stop"')]
    assert "$ElevationWindowStyle = if ($Unattended) { 'Hidden' } else { 'Normal' }" in elevation
    assert "Start-Process powershell.exe $ElevationArguments -Verb RunAs -WindowStyle $ElevationWindowStyle" in elevation
    assert "-WindowStyle Hidden" not in elevation
    # The SSO helper runs while the service is stopped and falls back to the
    # database; setup keeps governance.db beside the code folder, not inside
    # it, so the helper needs the same DG_DB_PATH the service gets.
    assert '$DbPath      = "$ProjectDir\\governance.db"' in source
    assert source.index("$env:DG_DB_PATH = $DbPath") < source.index("--authenticate-url $FlowAuthUrl")
