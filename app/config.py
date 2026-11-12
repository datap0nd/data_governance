import os
import sys
import threading
from pathlib import Path

from app import env_file
from app.flow_clock import TIMEZONE

# Base directory of the app
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file() -> tuple[Path | None, dict]:
    """Load local settings before module constants capture the environment.

    Diagnostics intentionally contain names and line numbers, never values.
    An explicit empty DG_ENV_FILE keeps tests and isolated tools independent of
    an installed Metronome settings file.
    """
    override = os.environ.get("DG_ENV_FILE")
    path = None if override == "" else Path(override) if override is not None else BASE_DIR.parent / ".env"
    values, status = env_file.read(path)
    os.environ.update(values)
    return path, status


# What the process was started with, before the settings file was applied. A
# name later removed from the file falls back to this, not to nothing.
_STARTING_ENVIRONMENT = dict(os.environ)
ENV_FILE, ENV_FILE_STATUS = _load_env_file()
_names_from_file = set(ENV_FILE_STATUS["loaded_names"])


def current_setting(name: str) -> str | None:
    """One setting as the settings file holds it now; the environment without a file.

    For values the host PC changes while Metronome runs (SQL passwords), so a
    save or clear applies without restarting the service or its workers.
    """
    if ENV_FILE is None:
        return os.environ.get(name) or None
    return env_file.read(ENV_FILE)[0].get(name) or None

# SQLite database file (the app's own database, not production)
DB_PATH = os.environ.get("DG_DB_PATH", str(BASE_DIR / "governance.db"))
PIPELINE_INSIGHTS_DB_PATH = os.environ.get(
    "DG_PIPELINE_INSIGHTS_DB_PATH",
    str(Path(DB_PATH).with_name("pipeline_insights.db")),
)

# Flow schedules are wall-clock values in one named timezone.  Persisted
# monitoring timestamps are UTC, so changing this setting does not reinterpret
# new evidence; it only changes future schedule occurrences.
FLOW_TIMEZONE = TIMEZONE

def _live_values(env) -> dict:
    """Connections, credentials and folders as ``env`` defines them.

    These are the values an operator changes on the Settings page. They are
    derived in one place so that start-up and ``refresh`` cannot disagree.
    """
    # Folder where .pbix reports live (or TMDL exports as fallback). Prefer
    # bio_path for BI report originals, while keeping the older documented
    # variables as fallbacks for existing installs.
    report_root = (
        env.get("bio_path")
        or env.get("BIO_PATH")
        or env.get("DG_TMDL_ROOT")
        or env.get("DG_REPORTS_PATH")
        or env.get("DG_DEFAULT_REPORTS_PATH")
        or "reports"
    )
    # A relative reports path is taken from the code folder, wherever the
    # process was started from.
    tmdl_root = report_root if os.path.isabs(report_root) else str(BASE_DIR / report_root)
    pbi_tenant = env.get("DG_PBI_TENANT_ID", "")
    # PostgreSQL credentials for READ-ONLY freshness probing
    # WARNING: These credentials must ONLY be used for SELECT queries.
    # NEVER use them for INSERT, UPDATE, DELETE, DROP, or any write operation.
    pghost = env.get("PGHOST", "")
    pgport = env.get("PGPORT", "") or "5432"
    pgdatabase = env.get("PGDATABASE", "postgres")
    return {
        "REPORTS_PATH": report_root,
        "TMDL_ROOT": tmdl_root,
        # Power BI workspace name for refresh schedule sync
        "PBI_WORKSPACE": env.get("DG_PBI_WORKSPACE", ""),
        "PBI_TENANT_ID": pbi_tenant,
        "PBI_CLIENT_ID": env.get("DG_PBI_CLIENT_ID", ""),
        "PBI_CLIENT_SECRET": env.get("DG_PBI_CLIENT_SECRET", ""),
        "PBI_SYNC_WINDOWS_USER": env.get("DG_PBI_SYNC_WINDOWS_USER", env.get("USERNAME", "")),
        # Headless Power BI sync with a saved Microsoft account (no tenant setup
        # needed). The default client id is Microsoft's first-party Azure CLI
        # public application, which is pre-consented in nearly every tenant and
        # supports the device code flow plus long-lived refresh tokens. Override
        # only if your tenant blocks it (the Azure PowerShell client
        # 1950a258-227b-4e31-a9cf-717495945fc2 is a common alternative).
        "PBI_PUBLIC_CLIENT_ID": env.get("DG_PBI_PUBLIC_CLIENT_ID", "04b07795-8ddb-461a-bbee-02f9e1bf7b46"),
        "PBI_AUTH_TENANT": env.get("DG_PBI_AUTH_TENANT", pbi_tenant or "organizations"),
        # Outbound proxy override for login.microsoftonline.com / api.powerbi.com,
        # e.g. DG_PBI_PROXY=http://proxyhost:8080. When unset, the app uses
        # HTTPS_PROXY/HTTP_PROXY if present, then asks Windows to evaluate the
        # system proxy settings (including a configured PAC setup script), then
        # goes direct. A PAC URL itself is NOT a valid value here.
        "PBI_PROXY": env.get("DG_PBI_PROXY", ""),
        # Folder containing the exported PBI usage CSV files. The lowercase name
        # is kept because this app is often configured from Windows environment
        # variables created by hand.
        "USAGE_FILES_PATH": (
            env.get("usage_files_path") or env.get("USAGE_FILES_PATH") or env.get("DG_USAGE_FILES_PATH") or ""
        ),
        "PGHOST": pghost,
        "PGPORT": pgport,
        "PGUSER": env.get("PGUSER", ""),
        "PGPASSWORD": env.get("PGPASSWORD", ""),
        "PGDATABASE": pgdatabase,
        # PostgreSQL credentials for Flow SQL handoffs and explicit
        # materialized-view refreshes. Kept separate from the read-only probing
        # credentials above: user and password have no fallback, while
        # host/port/database may reuse the probing connection's location.
        "UPLOAD_PGHOST": env.get("DG_UPLOAD_PGHOST", "") or pghost,
        "UPLOAD_PGPORT": env.get("DG_UPLOAD_PGPORT", "") or pgport,
        "UPLOAD_PGDATABASE": env.get("DG_UPLOAD_PGDATABASE", "") or pgdatabase,
        "UPLOAD_PGUSER": env.get("DG_UPLOAD_PGUSER", ""),
        "UPLOAD_PGPASSWORD": env.get("DG_UPLOAD_PGPASSWORD", ""),
    }


_live = _live_values(os.environ)
LIVE_NAMES = tuple(_live)

REPORTS_PATH = _live["REPORTS_PATH"]
TMDL_ROOT = _live["TMDL_ROOT"]


# How often to run scheduled scans and checks (in hours)
SCAN_INTERVAL_HOURS = int(os.environ.get("DG_SCAN_INTERVAL_HOURS", "24"))
CHECK_INTERVAL_HOURS = int(os.environ.get("DG_CHECK_INTERVAL_HOURS", "6"))

# AI configuration. Local models are reached through an OpenAI-compatible
# endpoint; the application itself never loads the checkpoint into the web process.
AI_MODEL = os.environ.get("DG_AI_MODEL", "Qwen/Qwen3.8-27B")
AI_API_KEY = os.environ.get("DG_AI_API_KEY", "")

# Read endpoint URL: endpoint_url.txt takes priority, then DG_AI_API_URL env var
_endpoint_file = BASE_DIR.parent / "endpoint_url.txt"
if _endpoint_file.exists():
    _ai_url = _endpoint_file.read_text(encoding="utf-8").strip().rstrip("/")
else:
    _ai_url = os.environ.get("DG_AI_API_URL", "").strip().rstrip("/")

if _ai_url:
    # Append /chat/completions if endpoint ends with /v1
    if _ai_url.endswith("/v1"):
        _ai_url += "/chat/completions"
    AI_API_URL = _ai_url
    AI_MOCK = False
else:
    AI_API_URL = "http://localhost:11434/v1/chat/completions"
    AI_MOCK = os.environ.get("DG_AI_MOCK", "true").lower() in ("true", "1", "yes")


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(os.environ.get(name, str(default)))))
    except ValueError:
        return default


def _bounded_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        return min(maximum, max(minimum, float(os.environ.get(name, str(default)))))
    except ValueError:
        return default


AI_PROVIDER_PROFILE = os.environ.get("DG_AI_PROVIDER_PROFILE", "auto").strip() or "auto"
AI_REASONING_EFFORT = os.environ.get("DG_AI_REASONING_EFFORT", "medium").strip().casefold()
if AI_REASONING_EFFORT not in {"low", "medium", "xhigh"}:
    AI_REASONING_EFFORT = "medium"
AI_AGENT_MAX_TOOL_CALLS = _bounded_int_env("DG_AI_AGENT_MAX_TOOL_CALLS", 8, 1, 12)
AI_AGENT_MAX_MODEL_TURNS = _bounded_int_env("DG_AI_AGENT_MAX_MODEL_TURNS", 6, 1, 8)
AI_AGENT_MAX_SECONDS = _bounded_int_env("DG_AI_AGENT_MAX_SECONDS", 180, 30, 300)
AI_AGENT_HTTP_TIMEOUT_SECONDS = _bounded_float_env(
    "DG_AI_AGENT_HTTP_TIMEOUT_SECONDS", 90.0, 10.0, 180.0
)
AI_AGENT_MAX_OUTPUT_TOKENS = _bounded_int_env(
    "DG_AI_AGENT_MAX_OUTPUT_TOKENS", 4096, 512, 8192
)
AI_AGENT_TEMPERATURE = _bounded_float_env("DG_AI_AGENT_TEMPERATURE", 1.0, 0.0, 1.5)
AI_AGENT_TOP_P = _bounded_float_env("DG_AI_AGENT_TOP_P", 0.95, 0.1, 1.0)

PBI_WORKSPACE = _live["PBI_WORKSPACE"]
PBI_SYNC_HOUR = int(os.environ.get("DG_PBI_SYNC_HOUR", "8"))
PBI_SYNC_MINUTE = int(os.environ.get("DG_PBI_SYNC_MINUTE", "15"))
PBI_TENANT_ID = _live["PBI_TENANT_ID"]
PBI_CLIENT_ID = _live["PBI_CLIENT_ID"]
PBI_CLIENT_SECRET = _live["PBI_CLIENT_SECRET"]
PBI_SYNC_WINDOWS_USER = _live["PBI_SYNC_WINDOWS_USER"]
PBI_PUBLIC_CLIENT_ID = _live["PBI_PUBLIC_CLIENT_ID"]
PBI_AUTH_TENANT = _live["PBI_AUTH_TENANT"]
PBI_TOKEN_CACHE_PATH = os.environ.get("DG_PBI_TOKEN_CACHE", str(BASE_DIR / "pbi_token.json"))
PBI_USAGE_DAYS_BACK = int(os.environ.get("DG_PBI_USAGE_DAYS_BACK", "30"))
PBI_PROXY = _live["PBI_PROXY"]
PBI_VISUAL_EXPORT_TIMEOUT_SECONDS = int(os.environ.get("DG_PBI_VISUAL_EXPORT_TIMEOUT_SECONDS", "120"))
PBI_VISUAL_EXPORT_MAX_ROWS = min(30000, max(1, int(os.environ.get("DG_PBI_VISUAL_EXPORT_MAX_ROWS", "30000"))))


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


EMAIL_REQUIRE_FRESH_PBI = _bool_env("DG_EMAIL_REQUIRE_FRESH_PBI", True)
EMAIL_MAX_PBI_SYNC_AGE_HOURS = _float_env("DG_EMAIL_MAX_PBI_SYNC_AGE_HOURS", 24.0)
EMAIL_PBI_SYNC_GRACE_MINUTES = _float_env("DG_EMAIL_PBI_SYNC_GRACE_MINUTES", 30.0)
EMAIL_PBI_STALE_RETRY_MINUTES = _float_env("DG_EMAIL_PBI_STALE_RETRY_MINUTES", 30.0)

USAGE_FILES_PATH = _live["USAGE_FILES_PATH"]

# PostgreSQL credentials for READ-ONLY freshness probing
# WARNING: These credentials must ONLY be used for SELECT queries.
# NEVER use them for INSERT, UPDATE, DELETE, DROP, or any write operation.
PGHOST = _live["PGHOST"]
PGPORT = _live["PGPORT"]
PGUSER = _live["PGUSER"]
PGPASSWORD = _live["PGPASSWORD"]
PGDATABASE = _live["PGDATABASE"]
# Bound each read-only scanner statement so a broken PostgreSQL connection or
# catalog lock cannot leave lineage discovery invisible for half an hour.  The
# durable scanner heartbeat will flag a run sooner if it stops making progress.
PG_SCAN_STATEMENT_TIMEOUT_SECONDS = _bounded_int_env(
    "DG_PG_SCAN_STATEMENT_TIMEOUT_SECONDS", 180, 30, 900
)

# PostgreSQL credentials for Flow SQL handoffs and explicit materialized-view
# refreshes. Kept separate from the read-only probing credentials above:
# user and password have no fallback, while host/port/database may reuse the
# probing connection's location.
UPLOAD_PGHOST = _live["UPLOAD_PGHOST"]
UPLOAD_PGPORT = _live["UPLOAD_PGPORT"]
UPLOAD_PGDATABASE = _live["UPLOAD_PGDATABASE"]
UPLOAD_PGUSER = _live["UPLOAD_PGUSER"]
UPLOAD_PGPASSWORD = _live["UPLOAD_PGPASSWORD"]


# --- Changing these values while Metronome runs --------------------------------
#
# Most modules bind the names above at import (``from app.config import
# PGHOST``). ``refresh`` re-reads the settings file, recomputes them, and
# replaces the old value in every loaded Metronome module that still holds it,
# so a connection saved on the Settings page, or an edit to ``.env``, applies
# to the next query in the web service and in each worker without a restart.
# A module that was given a different value on purpose (a test, a script) is
# left alone, because only an attribute equal to the old value is replaced.

_refresh_lock = threading.Lock()
_env_file_signature: tuple | None = None
_APP_DIR = str(BASE_DIR / "app")


def _file_signature() -> tuple | None:
    if ENV_FILE is None:
        return None
    try:
        stat = Path(ENV_FILE).stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def settings_file_signature() -> tuple | None:
    """A value that changes whenever the settings file is written; None without one."""
    return _file_signature()


def _is_metronome_module(module) -> bool:
    name = getattr(module, "__name__", "") or ""
    if name == "app" or name.startswith("app."):
        return True
    # The worker runs app/flow_worker.py as a script, whose fallback imports
    # load sibling modules under their bare names.
    return str(getattr(module, "__file__", "") or "").startswith(_APP_DIR)


def refresh() -> list[str]:
    """Apply the settings file as it is now; return the names that changed."""
    global ENV_FILE_STATUS, _names_from_file, _env_file_signature
    with _refresh_lock:
        if ENV_FILE is not None:
            values, status = env_file.read(ENV_FILE)
            if status.get("error"):
                # Unreadable for a moment (being rewritten, locked): keep what
                # is in force rather than fall back to an empty file.
                return []
            for name in _names_from_file - set(values):
                if name in _STARTING_ENVIRONMENT:
                    os.environ[name] = _STARTING_ENVIRONMENT[name]
                else:
                    os.environ.pop(name, None)
            os.environ.update(values)
            _names_from_file = set(values)
            ENV_FILE_STATUS = status
            _env_file_signature = _file_signature()
        own = sys.modules[__name__]
        changed = []
        for name, value in _live_values(os.environ).items():
            old = getattr(own, name)
            if old == value:
                continue
            changed.append(name)
            setattr(own, name, value)
            for module in list(sys.modules.values()):
                if module is None or module is own or not _is_metronome_module(module):
                    continue
                held = module.__dict__.get(name, _refresh_lock)
                if type(held) is type(old) and held == old:
                    setattr(module, name, value)
        return changed


def refresh_if_changed() -> list[str]:
    """``refresh`` only when the settings file was written since the last look.

    Cheap enough to call on every worker loop and scheduler tick: one ``stat``.
    """
    if ENV_FILE is None or _file_signature() == _env_file_signature:
        return []
    return refresh()


_env_file_signature = _file_signature()
