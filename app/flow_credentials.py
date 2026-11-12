"""Local Windows-protected credentials for authenticated Flow workers."""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import os
import platform
import time
from datetime import datetime
from pathlib import Path


CREDENTIAL_FILENAME = ".asap_credentials"

#: The portal sign-in as the settings file (.env) names it. Plain text, at the
#: owner's request and like the SQL passwords kept there: the file is readable
#: by SYSTEM, Administrators and the installing account only. When both are
#: set they are what every worker signs in with; the encrypted file below is
#: then not read.
PORTAL_USER_SETTING = "DG_PORTAL_USER"
PORTAL_PASSWORD_SETTING = "DG_PORTAL_PASSWORD"

#: Flow scripts read the sign-in from the encrypted file, so while the
#: settings file holds it Metronome keeps that file as a copy of it
#: (``sync_credential_file``). A copy carries this mark inside its encrypted
#: record, which is how it is told apart from the older stand-alone sign-in:
#: a copy goes when the settings-file sign-in is removed, the older one is
#: only ever replaced.
COPY_OF_SETTINGS_FILE = "settings_file"


def _settings_file_sign_in(username: str | None, password: str | None) -> dict | None:
    username = (username or "").strip()
    if not username or not password:
        return None
    return {"username": username, "password": password, "updated_at": None, "source": "settings_file"}


def settings_file_credentials() -> dict | None:
    """The portal sign-in from the settings file, as it reads right now."""
    from app import config

    return _settings_file_sign_in(config.current_setting(PORTAL_USER_SETTING),
                                  config.current_setting(PORTAL_PASSWORD_SETTING))


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


def save_asap_credentials(username: str, password: str, profile_dir: Path | None = None, *,
                          copy_of: str | None = None) -> dict:
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
    if copy_of:
        record["copy_of"] = copy_of
    encrypted = _dpapi(json.dumps(record).encode("utf-8"), True)
    wrapper = json.dumps({"format": "dpapi", "data": base64.b64encode(encrypted).decode("ascii")})
    path = credential_path(profile_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Named for this process: every worker writes the copy when it starts, and
    # two of them sharing one temporary file would trip over each other.
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(wrapper, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return {"configured": True, "updated_at": record["updated_at"]}


def load_asap_credentials(profile_dir: Path | None = None) -> dict | None:
    """The portal sign-in workers use: the settings file's, else the encrypted file's.

    A caller that names a profile folder asks for the encrypted file there and
    nothing else, which is what the tools that maintain that file need.
    """
    if profile_dir is None:
        from_settings = settings_file_credentials()
        if from_settings:
            return from_settings
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


def clear_asap_credentials(profile_dir: Path | None = None) -> dict:
    """Forget the stored portal login. Workers then wait for a manual sign-in."""
    path = credential_path(profile_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return {"configured": False, "updated_at": None}


def asap_credential_status(profile_dir: Path | None = None) -> dict:
    """Report shared credential status without returning decrypted values."""
    if profile_dir is None and settings_file_credentials():
        return {"configured": True, "updated_at": None}
    path = credential_path(profile_dir)
    if not path.exists():
        return {"configured": False, "updated_at": None}
    try:
        record = load_asap_credentials(profile_dir)
        return {"configured": True, "updated_at": record.get("updated_at")}
    except Exception:
        return {"configured": False, "updated_at": None}


# --- The encrypted file as a copy of the settings-file sign-in ---------------------

def _read_settings_file_sign_in() -> tuple[dict | None, bool]:
    """The settings-file sign-in from one read, and whether "there is none" can be believed.

    A settings file that is missing or cannot be read for a moment (it is
    being rewritten, or locked) says nothing about the sign-in.
    """
    from app import config, env_file

    if config.ENV_FILE is None:
        return settings_file_credentials(), True  # no file is used; the environment is the answer
    values, status = env_file.read(config.ENV_FILE)
    sign_in = _settings_file_sign_in(values.get(PORTAL_USER_SETTING), values.get(PORTAL_PASSWORD_SETTING))
    return sign_in, bool(status["exists"]) and not status["error"]


def _encrypted_record(profile_dir: Path | None) -> dict | None:
    """What the encrypted file holds; None when there is none or it cannot be read."""
    try:
        return load_asap_credentials(profile_dir if profile_dir is not None else flow_profile_dir())
    except Exception:  # another account's file, a damaged one, no Windows protection
        return None


def _is_copy_of(record: dict | None, sign_in: dict | None) -> bool:
    return bool(
        record and sign_in
        and record.get("copy_of") == COPY_OF_SETTINGS_FILE
        and record.get("username") == sign_in["username"]
        and record.get("password") == sign_in["password"]
    )


def credential_file_is_current_copy(profile_dir: Path | None = None) -> bool:
    """Whether the encrypted file holds exactly the settings-file sign-in."""
    sign_in = settings_file_credentials()
    return bool(sign_in) and _is_copy_of(_encrypted_record(profile_dir), sign_in)


def sync_credential_file(profile_dir: Path | None = None) -> str:
    """Make the encrypted file a copy of the settings-file sign-in.

    Flow scripts read the portal sign-in from the encrypted file, as every
    worker did before the sign-in moved to the settings file. So that those
    scripts keep working, and never read an older password than the workers
    use, Metronome rewrites the file from the settings file: when the web
    service starts, when a worker starts, when the sign-in is changed on the
    Settings page, and when a worker sees the settings file change.

    Says what it did:

    * ``written``: the file was missing, unreadable or held something else;
    * ``unchanged``: it already held this sign-in;
    * ``removed``: the settings-file sign-in is gone, and so is its copy;
    * ``not_set``: no sign-in in the settings file, and no copy to remove. An
      older stand-alone encrypted sign-in is left exactly as it is;
    * ``settings_unreadable``: the settings file is missing or could not be
      read at this moment, so nothing is known and nothing was touched. The
      caller asks again; it must not take this for an answer;
    * ``unsupported``: not Windows, whose protection the file relies on.
    """
    if platform.system() != "Windows":
        return "unsupported"
    sign_in, believable = _read_settings_file_sign_in()
    if sign_in is None and not believable:
        # Removing the copy on the word of a settings file that could not be
        # read would break the scripts that are running.
        return "settings_unreadable"
    held = _encrypted_record(profile_dir)
    if sign_in is None:
        if held and held.get("copy_of") == COPY_OF_SETTINGS_FILE:
            clear_asap_credentials(profile_dir)
            return "removed"
        return "not_set"
    if _is_copy_of(held, sign_in):
        return "unchanged"
    pauses = [0.2, 0.5, 1.0]
    while True:
        try:
            save_asap_credentials(sign_in["username"], sign_in["password"], profile_dir,
                                  copy_of=COPY_OF_SETTINGS_FILE)
            return "written"
        except PermissionError:
            # Windows refuses to replace a file another process is reading.
            # A worker that started at the same moment may have written it.
            if _is_copy_of(_encrypted_record(profile_dir), sign_in):
                return "unchanged"
            if not pauses:
                raise
            time.sleep(pauses.pop(0))
