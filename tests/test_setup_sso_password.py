"""Setup password rotation with fictional credentials and isolated profiles."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import flow_credentials
from tools import sync_flow_sso_password as sync


ROOT = Path(__file__).resolve().parents[1]
NEW_PASSWORD = "fictional-new-'\"$`;& password-\u00e9"


@pytest.fixture
def encrypted_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(flow_credentials.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        flow_credentials, "_dpapi",
        lambda data, protect: bytes(value ^ 0xA5 for value in data),
    )
    flow_credentials.save_asap_credentials("portal-user", "fictional-old", tmp_path)
    return tmp_path


def test_rotation_preserves_portal_identity_and_updates_encrypted_password(monkeypatch, encrypted_profile):
    monkeypatch.setenv("DG_SVC_PASSWORD", NEW_PASSWORD)
    monkeypatch.setenv("USERNAME", "different-windows-account")
    message = sync.sync_password(encrypted_profile)
    credential = flow_credentials.load_asap_credentials(encrypted_profile)
    assert credential["username"] == "portal-user"
    assert credential["password"] == NEW_PASSWORD
    assert NEW_PASSWORD not in message
    raw = flow_credentials.credential_path(encrypted_profile).read_text()
    assert NEW_PASSWORD not in raw
    assert "portal-user" not in raw


@pytest.mark.parametrize("password", [None, ""])
def test_absent_password_preserves_file_byte_for_byte(monkeypatch, encrypted_profile, password):
    if password is None:
        monkeypatch.delenv("DG_SVC_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("DG_SVC_PASSWORD", password)
    path = flow_credentials.credential_path(encrypted_profile)
    before = path.read_bytes()
    sync.sync_password(encrypted_profile)
    assert path.read_bytes() == before


def test_missing_enrollment_does_not_guess_a_user(monkeypatch, tmp_path):
    monkeypatch.setenv("DG_SVC_PASSWORD", NEW_PASSWORD)
    monkeypatch.setenv("USERNAME", "different-windows-account")
    assert "user ID is not enrolled" in sync.sync_password(tmp_path)
    assert not flow_credentials.credential_path(tmp_path).exists()


@pytest.mark.parametrize("failure", ["read", "encrypt"])
def test_failure_is_sanitized_and_preserves_existing_file(monkeypatch, encrypted_profile, capsys, failure):
    monkeypatch.setenv("DG_SVC_PASSWORD", NEW_PASSWORD)
    monkeypatch.setattr(sys, "argv", ["sync_flow_sso_password.py", str(encrypted_profile)])
    path = flow_credentials.credential_path(encrypted_profile)
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError(NEW_PASSWORD)

    if failure == "read":
        monkeypatch.setattr(sync, "load_asap_credentials", fail)
    else:
        monkeypatch.setattr(flow_credentials, "_dpapi", fail)
    assert sync.main() == 1
    captured = capsys.readouterr()
    assert NEW_PASSWORD not in captured.out + captured.err
    assert "Portal sign-in was not started" in captured.err
    assert path.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell and real DPAPI")
@pytest.mark.parametrize("corrupt", [False, True])
def test_setup_block_uses_inherited_password_and_stops_on_failure(tmp_path, corrupt):
    # Execute only the real sync block, never the installer/services/portals.
    source = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    block = source.split("# Sync the shared portal password", 1)[1].split(
        "# Bootstrap the dedicated automation browser profile", 1
    )[0]
    block = "# Sync the shared portal password" + block
    path = flow_credentials.credential_path(tmp_path)
    if corrupt:
        path.write_text("broken credential", encoding="utf-8")
    else:
        flow_credentials.save_asap_credentials("portal-user", "fictional-old", tmp_path)
    before = path.read_bytes()
    env = dict(os.environ, DG_SVC_PASSWORD=NEW_PASSWORD)
    env["SSO_TEST_PYTHON"] = sys.executable
    env["SSO_TEST_CODE"] = str(ROOT)
    env["SSO_TEST_PROFILE"] = str(tmp_path)
    script = tmp_path / "sync-only.ps1"
    script.write_text(
        '$ErrorActionPreference = "Stop"\n'
        '$PyExe = $env:SSO_TEST_PYTHON\n'
        '$CodeDir = $env:SSO_TEST_CODE\n'
        '$FlowProfile = $env:SSO_TEST_PROFILE\n'
        + block + '\nWrite-Output "AUTHENTICATION_REACHED"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert NEW_PASSWORD not in result.stdout + result.stderr
    if corrupt:
        assert result.returncode != 0
        assert "AUTHENTICATION_REACHED" not in result.stdout
        assert path.read_bytes() == before
    else:
        assert result.returncode == 0, result.stderr
        assert "AUTHENTICATION_REACHED" in result.stdout
        credential = flow_credentials.load_asap_credentials(tmp_path)
        assert credential["username"] == "portal-user"
        assert credential["password"] == NEW_PASSWORD
        assert NEW_PASSWORD.encode("utf-8") not in path.read_bytes()


def test_setup_sync_precedes_interactive_authentication_and_is_not_interactive_only():
    source = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    sync_at = source.index('# Sync the shared portal password')
    bootstrap_at = source.index('# Bootstrap the dedicated automation browser profile')
    guard_at = source.index('if (-not $Unattended)', bootstrap_at)
    authenticate_at = source.index('--authenticate-url', bootstrap_at)
    assert sync_at < bootstrap_at < guard_at < authenticate_at
