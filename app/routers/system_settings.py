"""The Settings page: everyone may read it, only the host PC may change it."""
import platform
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import connection_tests, flow_credentials, flow_sign_in_guard, settings_registry, user_identity
from app.database import get_db
from app.local_access import require_host
from app.routers.eventlog import get_actor, log_event

router = APIRouter(prefix="/api/system/settings", tags=["system"])


class SettingsWrite(BaseModel):
    # A value of null returns that setting to its default.
    values: dict[str, Any] = Field(min_length=1, max_length=200)


def _state(request: Request) -> dict:
    _person_id, is_host = user_identity.request_identity(request)
    return {"can_edit": bool(is_host), "sections": settings_registry.describe()}


@router.get("")
def get_settings(request: Request):
    return _state(request)


@router.put("")
def save_settings(body: SettingsWrite, request: Request):
    require_host(request, "Only the host PC can change settings.")
    try:
        changed = settings_registry.save(body.values)
    except settings_registry.SettingsRejected as exc:
        # Nothing was saved; each message belongs beside its own field.
        return JSONResponse(status_code=422, content={
            "detail": "Nothing was saved. Fix the highlighted settings and save again.",
            "errors": exc.errors,
        })
    except settings_registry.SaveFailed as exc:
        if not exc.saved:
            # Everything was put back; the message says nothing was saved.
            return JSONResponse(status_code=503, content={"detail": str(exc)})
        _record(exc.saved, request)
        labels = ", ".join(record["label"] for record in exc.saved)
        return JSONResponse(status_code=503, content={
            "detail": f"Saving stopped part-way. These were saved: {labels}. The rest were not. "
                      "Reload the page before trying again.",
        })
    except Exception as exc:
        raise HTTPException(
            503, f"Saving stopped part-way ({type(exc).__name__}). Reload the page to see what was saved, then try again."
        ) from exc
    _record(changed, request)
    return {**_state(request), "changed": [record["key"] for record in changed]}


def _record(changed: list[dict], request: Request) -> None:
    """One Event Log entry per setting that changed, with who changed it."""
    if not changed:
        return
    with get_db() as db:
        for record in changed:
            log_event(
                db, "system", None, "Settings", "setting_changed",
                f"{record['label']}: {settings_registry.display(record['old'])} to "
                f"{settings_registry.display(record['new'])}",
                get_actor(request),
            )


class SecretWrite(BaseModel):
    value: str = Field(min_length=1, max_length=2000)


def _rejected(exc: settings_registry.SettingsRejected) -> JSONResponse:
    return JSONResponse(status_code=422, content={
        "detail": "Nothing was saved. " + " ".join(exc.errors.values()), "errors": exc.errors,
    })


@router.put("/secret/{key}")
def replace_secret(key: str, body: SecretWrite, request: Request):
    """Replace a password or token. The value is written, never returned or logged."""
    require_host(request, "Only the host PC can change passwords.")
    try:
        saved = settings_registry.save_secret(key, body.value)
    except KeyError:
        raise HTTPException(404, "This setting does not exist.") from None
    except settings_registry.SettingsRejected as exc:
        return _rejected(exc)
    with get_db() as db:
        log_event(db, "system", None, "Settings", "secret_replaced", f"{saved['label']} replaced", get_actor(request))
    return _state(request)


@router.delete("/secret/{key}")
def clear_secret(key: str, request: Request):
    require_host(request, "Only the host PC can change passwords.")
    try:
        saved = settings_registry.save_secret(key, None)
    except KeyError:
        raise HTTPException(404, "This setting does not exist.") from None
    except settings_registry.SettingsRejected as exc:
        return _rejected(exc)
    with get_db() as db:
        log_event(db, "system", None, "Settings", "secret_cleared", f"{saved['label']} cleared", get_actor(request))
    return _state(request)


@router.post("/test/{name}")
def test_connection(name: str, request: Request):
    """Sign in with the saved values and say what happened."""
    require_host(request, "Only the host PC can test connections.")
    test = connection_tests.TESTS.get(name)
    if test is None:
        raise HTTPException(404, "There is no such connection test.")
    return test()


# --- Portal sign-in ---------------------------------------------------------------
#
# ASAP and GSCM share one single sign-on login. It is kept as a Windows-protected
# file beside the worker's browser profile, readable only by the Windows account
# the services run as, so it is not a settings-file value like the others.

class PortalLoginWrite(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000)


def _portal_login_state() -> dict:
    """The encrypted sign-in this block manages, which sign-in is in use, and whether it is paused."""
    # Named folder: the encrypted file's own state, whatever the settings file holds.
    encrypted = flow_credentials.asap_credential_status(flow_credentials.flow_profile_dir())
    from_settings_file = flow_credentials.settings_file_credentials() is not None
    try:
        in_use = flow_credentials.load_asap_credentials()
    except Exception:  # an unreadable encrypted file is simply not in use
        in_use = None
    return {
        **encrypted,
        # The protection is Windows' own; elsewhere the login cannot be stored.
        "available": platform.system() == "Windows",
        "source": "settings_file" if from_settings_file else "encrypted_file" if in_use else None,
        **flow_sign_in_guard.status(in_use),
    }


@router.get("/portal-login")
def get_portal_login():
    return _portal_login_state()


@router.put("/portal-login")
def save_portal_login(body: PortalLoginWrite, request: Request):
    require_host(request, "Only the host PC can change the portal sign-in.")
    try:
        flow_credentials.save_asap_credentials(body.username, body.password)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, f"The portal sign-in was not saved: {exc}") from exc
    with get_db() as db:
        log_event(db, "system", None, "Settings", "secret_replaced", "Portal sign-in replaced", get_actor(request))
    return _portal_login_state()


@router.post("/portal-login/resume")
def resume_portal_login(request: Request):
    """A person says the paused sign-in is right: allow it to be submitted again."""
    require_host(request, "Only the host PC can change the portal sign-in.")
    flow_sign_in_guard.resume()
    with get_db() as db:
        log_event(db, "system", None, "Settings", "sign_in_resumed", "Portal sign-in allowed again", get_actor(request))
    return _portal_login_state()


@router.delete("/portal-login")
def clear_portal_login(request: Request):
    require_host(request, "Only the host PC can change the portal sign-in.")
    try:
        flow_credentials.clear_asap_credentials()
    except OSError as exc:
        raise HTTPException(503, f"The portal sign-in could not be cleared: {type(exc).__name__}.") from exc
    with get_db() as db:
        log_event(db, "system", None, "Settings", "secret_cleared", "Portal sign-in cleared", get_actor(request))
    return _portal_login_state()
