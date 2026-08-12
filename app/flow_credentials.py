"""Local Windows-protected credentials for authenticated Flow workers."""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import os
import platform
from datetime import datetime
from pathlib import Path


CREDENTIAL_FILENAME = ".asap_credentials"


def flow_profile_dir() -> Path:
    return Path(os.environ.get("METRONOME_FLOW_PROFILE", str(Path.home() / ".metronome-flow-browser")))


def credential_path(profile_dir: Path | None = None) -> Path:
    return (profile_dir or flow_profile_dir()) / CREDENTIAL_FILENAME


def _dpapi(data: bytes, protect: bool) -> bytes:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    function = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not function(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)):
        raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def save_asap_credentials(username: str, password: str, profile_dir: Path | None = None) -> dict:
    """Encrypt and persist ASAP credentials for the current Windows account."""
    if platform.system() != "Windows":
        raise RuntimeError("ASAP credential storage is only available on the Windows BI desktop.")
    record = {
        "username": username.strip(),
        "password": password,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if not record["username"] or not record["password"]:
        raise ValueError("ASAP username and password are required.")
    encrypted = _dpapi(json.dumps(record).encode("utf-8"), True)
    wrapper = json.dumps({"format": "dpapi", "data": base64.b64encode(encrypted).decode("ascii")})
    path = credential_path(profile_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(wrapper, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return {"configured": True, "updated_at": record["updated_at"]}


def load_asap_credentials(profile_dir: Path | None = None) -> dict | None:
    """Load the credential from the requested or account-default location."""
    path = credential_path(profile_dir)
    if not path.exists():
        return None
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    if wrapper.get("format") != "dpapi":
        raise RuntimeError("ASAP credential file is not Windows DPAPI protected.")
    decrypted = _dpapi(base64.b64decode(wrapper["data"]), False)
    record = json.loads(decrypted.decode("utf-8"))
    if not record.get("username") or not record.get("password"):
        raise RuntimeError("ASAP credential file is incomplete.")
    return record


def asap_credential_status(profile_dir: Path | None = None) -> dict:
    """Report shared credential status without returning decrypted values."""
    path = credential_path(profile_dir)
    if not path.exists():
        return {"configured": False, "updated_at": None}
    try:
        record = load_asap_credentials(profile_dir)
        return {"configured": True, "updated_at": record.get("updated_at")}
    except Exception:
        return {"configured": False, "updated_at": None}
