"""Saved Microsoft account sign-in for headless Power BI sync.

Why this exists: Connect-PowerBIServiceAccount never remembers the login, so
every scheduled sync needed a visible Microsoft account-picker window plus an
auto-clicker, which fails whenever the PC is locked or the RDP session is
disconnected. This module signs the user in ONCE with the OAuth2 device code
flow (the code can be entered from any browser on any device) and keeps the
refresh token in a local cache file. Every later sync gets an access token
silently, with no window and no desktop session at all.

No tenant admin work is required: the client id is a first-party Microsoft
public application (Azure CLI by default) that is already consented in
practically every tenant. The refresh token rotates on every use, so as long
as the sync runs at least every couple of weeks the sign-in stays valid until
the org forces a re-auth (password change, revocation, or a conditional
access sign-in frequency policy). When that happens the sync fails with a
clear "reconnect" status instead of hanging at a login window.

On Windows the cache file is encrypted with DPAPI for the account that runs
the app service. On other platforms it is stored as plain JSON with
owner-only permissions (development convenience).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import platform
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

try:
    # Trust the OS certificate store (needed when corporate TLS interception
    # re-signs outbound HTTPS; PowerShell trusted it via SChannel, Python's
    # default certifi bundle does not).
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from app.config import PBI_AUTH_TENANT, PBI_PROXY, PBI_PUBLIC_CLIENT_ID, PBI_TOKEN_CACHE_PATH

logger = logging.getLogger(__name__)

_PROXY_CACHE: dict = {}


def _normalize_proxy(value: str) -> str:
    value = value.strip().rstrip("/")
    if value and "://" not in value:
        value = "http://" + value
    return value


def resolve_proxy(target_url: str) -> str | None:
    """Find the outbound proxy for a URL.

    Order: DG_PBI_PROXY, standard proxy env vars, then the Windows system
    proxy settings (which evaluate a configured PAC setup script). Returns
    None for a direct connection. Cached per host for the process lifetime.
    """
    if PBI_PROXY:
        return _normalize_proxy(PBI_PROXY)
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(name)
        if value:
            return _normalize_proxy(value)
    if platform.system() != "Windows":
        return None
    host = urlsplit(target_url).netloc
    if host in _PROXY_CACHE:
        return _PROXY_CACHE[host]
    proxy = None
    try:
        script = (
            "$ErrorActionPreference='SilentlyContinue';"
            f"$u=[Uri]'{target_url}';"
            "$p=[System.Net.WebRequest]::GetSystemWebProxy().GetProxy($u);"
            "if ($p -and $p.AbsoluteUri -ne $u.AbsoluteUri) { Write-Output $p.AbsoluteUri }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if lines:
            proxy = _normalize_proxy(lines[-1])
    except Exception:
        logger.exception("Could not resolve the Windows system proxy for %s", target_url)
    _PROXY_CACHE[host] = proxy
    logger.info("Outbound proxy for %s: %s", host, proxy or "none (direct)")
    return proxy

LOGIN_BASE = "https://login.microsoftonline.com"
PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default offline_access openid profile"
TOKEN_TIMEOUT_SECONDS = 30
REFRESH_SKEW_SECONDS = 300

_LOCK = threading.RLock()
_DEVICE_FLOW: dict = {}
_DEVICE_FLOW_GENERATION = 0

_RECONNECT_ERROR_CODES = {
    "invalid_grant",
    "interaction_required",
    "consent_required",
    "login_required",
    "unauthorized_client",
    "invalid_client",
}


class PbiAuthError(RuntimeError):
    """Raised when a Power BI access token cannot be obtained silently."""

    def __init__(self, message: str, *, reconnect_required: bool = False):
        super().__init__(message)
        self.reconnect_required = reconnect_required


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_path() -> Path:
    return Path(PBI_TOKEN_CACHE_PATH)


# --- DPAPI protection (Windows only) ---------------------------------------

def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    func = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not func(ctypes.byref(blob_in), None, None, None, None, flags, ctypes.byref(blob_out)):
        raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _encode_record(record: dict) -> str:
    raw = json.dumps(record).encode("utf-8")
    if platform.system() == "Windows":
        try:
            return json.dumps(
                {"format": "dpapi", "data": base64.b64encode(_dpapi(raw, True)).decode("ascii")}
            )
        except Exception:
            logger.exception("DPAPI protect failed; storing token cache unencrypted")
    return json.dumps({"format": "plain", "data": base64.b64encode(raw).decode("ascii")})


def _decode_record(text: str) -> dict:
    wrapper = json.loads(text)
    payload = base64.b64decode(wrapper["data"])
    if wrapper.get("format") == "dpapi":
        payload = _dpapi(payload, False)
    return json.loads(payload.decode("utf-8"))


# --- Cache file -------------------------------------------------------------

def _load_record() -> dict | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        return _decode_record(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Could not read Power BI token cache at %s", path)
        return None


def _save_record(record: dict) -> None:
    path = _cache_path()
    path.write_text(_encode_record(record), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def cached_token_available() -> bool:
    """Return True when a saved Microsoft account sign-in exists."""
    record = _load_record()
    return bool(record and record.get("refresh_token"))


def disconnect() -> dict:
    """Forget the saved Microsoft account sign-in."""
    global _DEVICE_FLOW
    with _LOCK:
        _DEVICE_FLOW = {}
        path = _cache_path()
        existed = path.exists()
        if existed:
            try:
                path.unlink()
            except OSError as exc:
                return {"status": "error", "message": f"Could not delete token cache: {exc}"}
    return {
        "status": "disconnected" if existed else "idle",
        "message": "Saved Power BI sign-in removed." if existed else "No saved Power BI sign-in to remove.",
    }


# --- Token plumbing ---------------------------------------------------------

def _decode_jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return {}


def _account_from_response(tokens: dict) -> str:
    claims = _decode_jwt_claims(tokens.get("id_token") or "")
    account = claims.get("preferred_username") or claims.get("upn") or ""
    if not account:
        claims = _decode_jwt_claims(tokens.get("access_token") or "")
        account = claims.get("upn") or claims.get("unique_name") or claims.get("preferred_username") or ""
    return account


def _tenant_from_response(tokens: dict) -> str:
    claims = _decode_jwt_claims(tokens.get("access_token") or "")
    return claims.get("tid") or ""


def _token_endpoint(tenant: str) -> str:
    return f"{LOGIN_BASE}/{tenant or 'organizations'}/oauth2/v2.0/token"


def _post_form(url: str, data: dict) -> tuple[dict, int]:
    with httpx.Client(timeout=TOKEN_TIMEOUT_SECONDS, proxy=resolve_proxy(url)) as client:
        response = client.post(url, data=data)
    try:
        body = response.json()
    except ValueError:
        body = {"error": "invalid_response", "error_description": response.text[:300]}
    return body, response.status_code


def _short_aad_error(body: dict) -> str:
    description = (body.get("error_description") or "").splitlines()
    first = description[0].strip() if description else ""
    code = body.get("error") or "unknown_error"
    return f"{code}: {first}" if first else code


def _record_from_tokens(tokens: dict, previous: dict | None = None) -> dict:
    previous = previous or {}
    now = time.time()
    record = {
        "client_id": PBI_PUBLIC_CLIENT_ID,
        "scope": PBI_SCOPE,
        "refresh_token": tokens.get("refresh_token") or previous.get("refresh_token"),
        "access_token": tokens.get("access_token"),
        "access_token_expires_at": now + float(tokens.get("expires_in") or 0),
        "account": _account_from_response(tokens) or previous.get("account") or "",
        "tenant_id": _tenant_from_response(tokens) or previous.get("tenant_id") or "",
        "obtained_at": previous.get("obtained_at") or _now_iso(),
        "last_refresh_at": _now_iso(),
        "last_error": None,
    }
    return record


def _refresh_tokens(record: dict) -> dict:
    tenant = record.get("tenant_id") or PBI_AUTH_TENANT
    body, status = _post_form(
        _token_endpoint(tenant),
        {
            "grant_type": "refresh_token",
            "client_id": record.get("client_id") or PBI_PUBLIC_CLIENT_ID,
            "refresh_token": record["refresh_token"],
            "scope": record.get("scope") or PBI_SCOPE,
        },
    )
    if "access_token" in body:
        updated = _record_from_tokens(body, record)
        _save_record(updated)
        return updated

    error_code = body.get("error") or f"http_{status}"
    message = _short_aad_error(body)
    reconnect = error_code in _RECONNECT_ERROR_CODES
    record["last_error"] = {"at": _now_iso(), "code": error_code, "message": message, "reconnect_required": reconnect}
    try:
        _save_record(record)
    except Exception:
        logger.exception("Could not persist Power BI token refresh error")
    raise PbiAuthError(f"Power BI token refresh failed ({message}).", reconnect_required=reconnect)


def get_access_token() -> dict:
    """Return a valid access token silently, refreshing the cache if needed.

    Returns {"access_token": str, "account": str}. Raises PbiAuthError when
    no saved sign-in exists or the refresh token is no longer accepted.
    """
    with _LOCK:
        record = _load_record()
        if not record or not record.get("refresh_token"):
            raise PbiAuthError(
                "No saved Microsoft account sign-in. Use Connect Power BI on the Scanner page.",
                reconnect_required=True,
            )
        expires_at = float(record.get("access_token_expires_at") or 0)
        if record.get("access_token") and expires_at - time.time() > REFRESH_SKEW_SECONDS:
            return {"access_token": record["access_token"], "account": record.get("account") or ""}
        try:
            updated = _refresh_tokens(record)
        except PbiAuthError:
            raise
        except Exception as exc:
            raise PbiAuthError(f"Could not reach the Microsoft sign-in service: {exc}") from exc
        return {"access_token": updated["access_token"], "account": updated.get("account") or ""}


# --- Device code flow --------------------------------------------------------

def start_device_flow() -> dict:
    """Begin a device code sign-in and poll for completion in the background."""
    global _DEVICE_FLOW, _DEVICE_FLOW_GENERATION
    with _LOCK:
        _DEVICE_FLOW_GENERATION += 1
        generation = _DEVICE_FLOW_GENERATION

    try:
        body, status = _post_form(
            f"{LOGIN_BASE}/{PBI_AUTH_TENANT}/oauth2/v2.0/devicecode",
            {"client_id": PBI_PUBLIC_CLIENT_ID, "scope": PBI_SCOPE},
        )
    except Exception as exc:
        logger.exception("Could not reach the Microsoft sign-in service")
        try:
            proxy_used = resolve_proxy(LOGIN_BASE) or "none (direct connection)"
        except Exception:
            proxy_used = "unknown"
        with _LOCK:
            _DEVICE_FLOW = {
                "status": "failed",
                "message": (
                    f"The server could not reach login.microsoftonline.com: {exc}. "
                    f"Proxy used: {proxy_used}. If that proxy is wrong, set "
                    "DG_PBI_PROXY=http://proxyhost:port in the service environment "
                    "and restart it (a .pac script URL is not a valid proxy value)."
                ),
                "updated_at": _now_iso(),
            }
            return dict(_DEVICE_FLOW)

    if "device_code" not in body:
        message = _short_aad_error(body)
        with _LOCK:
            _DEVICE_FLOW = {
                "status": "failed",
                "message": f"Could not start the Microsoft sign-in ({message}). "
                "The tenant may block the device code flow or this client id; "
                "try setting DG_PBI_PUBLIC_CLIENT_ID.",
                "updated_at": _now_iso(),
            }
            return dict(_DEVICE_FLOW)

    interval = int(body.get("interval") or 5)
    expires_at = time.time() + float(body.get("expires_in") or 900)
    flow = {
        "status": "pending",
        "user_code": body.get("user_code"),
        "verification_uri": body.get("verification_uri") or "https://microsoft.com/devicelogin",
        "message": body.get("message")
        or f"Go to https://microsoft.com/devicelogin and enter the code {body.get('user_code')}.",
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
        "updated_at": _now_iso(),
    }
    with _LOCK:
        _DEVICE_FLOW = dict(flow)

    worker = threading.Thread(
        target=_poll_device_flow,
        args=(generation, body["device_code"], interval, expires_at),
        name="pbi-device-flow",
        daemon=True,
    )
    worker.start()
    return dict(flow)


def _set_flow(generation: int, **updates) -> None:
    global _DEVICE_FLOW
    with _LOCK:
        if generation != _DEVICE_FLOW_GENERATION:
            return
        _DEVICE_FLOW.update(updates)
        _DEVICE_FLOW["updated_at"] = _now_iso()


def _poll_device_flow(generation: int, device_code: str, interval: int, expires_at: float) -> None:
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": PBI_PUBLIC_CLIENT_ID,
        "device_code": device_code,
    }
    while time.time() < expires_at:
        with _LOCK:
            if generation != _DEVICE_FLOW_GENERATION:
                return
        time.sleep(max(1, interval))
        try:
            body, _ = _post_form(_token_endpoint(PBI_AUTH_TENANT), data)
        except Exception as exc:
            _set_flow(generation, message=f"Sign-in check failed, retrying: {exc}")
            continue

        if "access_token" in body:
            with _LOCK:
                record = _record_from_tokens(body)
                try:
                    _save_record(record)
                except Exception as exc:
                    logger.exception("Could not save Power BI token cache")
                    _set_flow(generation, status="failed", message=f"Sign-in succeeded but the token cache could not be saved: {exc}")
                    return
            _set_flow(
                generation,
                status="completed",
                account=record.get("account") or "",
                message=f"Connected as {record.get('account') or 'unknown account'}.",
            )
            logger.info("Power BI device-code sign-in completed for %s", record.get("account"))
            return

        error_code = body.get("error") or ""
        if error_code == "authorization_pending":
            continue
        if error_code == "slow_down":
            interval += 5
            continue
        if error_code == "expired_token":
            break
        _set_flow(generation, status="failed", message=f"Sign-in failed ({_short_aad_error(body)}).")
        return

    _set_flow(
        generation,
        status="expired",
        message="The sign-in code expired before it was used. Start Connect Power BI again.",
    )


def device_flow_status() -> dict | None:
    with _LOCK:
        return dict(_DEVICE_FLOW) if _DEVICE_FLOW else None


def auth_status() -> dict:
    """Summarize the saved sign-in for the UI without touching the network."""
    record = _load_record()
    flow = device_flow_status()
    if not record or not record.get("refresh_token"):
        return {
            "connected": False,
            "reconnect_required": False,
            "account": "",
            "message": "No saved Microsoft account. Use Connect Power BI to sign in once; "
            "after that, syncs run headless even when the PC is locked.",
            "client_id": PBI_PUBLIC_CLIENT_ID,
            "device_flow": flow,
        }
    last_error = record.get("last_error") or None
    reconnect = bool(last_error and last_error.get("reconnect_required"))
    return {
        "connected": True,
        "reconnect_required": reconnect,
        "account": record.get("account") or "",
        "tenant_id": record.get("tenant_id") or "",
        "obtained_at": record.get("obtained_at"),
        "last_refresh_at": record.get("last_refresh_at"),
        "last_error": last_error,
        "message": (
            f"Sign-in for {record.get('account') or 'saved account'} needs to be renewed - use Connect Power BI."
            if reconnect
            else f"Connected as {record.get('account') or 'saved account'}. Syncs run headless."
        ),
        "client_id": record.get("client_id") or PBI_PUBLIC_CLIENT_ID,
        "device_flow": flow,
    }
