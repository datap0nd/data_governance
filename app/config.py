import os
from pathlib import Path

# Base directory of the app
BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite database file (the app's own database, not production)
DB_PATH = os.environ.get("DG_DB_PATH", str(BASE_DIR / "governance.db"))

# Folder where .pbix reports live (or TMDL exports as fallback)
_report_root_env = (
    os.environ.get("bio_path")
    or os.environ.get("BIO_PATH")
    or os.environ.get("DG_TMDL_ROOT")
    or os.environ.get("DG_REPORTS_PATH")
    or os.environ.get("DG_DEFAULT_REPORTS_PATH")
    or "reports"
)

REPORTS_PATH = _report_root_env

# Prefer bio_path for BI report originals, while keeping the older documented
# variables as fallbacks for existing installs.
_tmdl_root_raw = _report_root_env
# Resolve relative paths against BASE_DIR (needed for Vercel/serverless)
if not os.path.isabs(_tmdl_root_raw):
    _tmdl_root_raw = str(BASE_DIR / _tmdl_root_raw)
TMDL_ROOT = _tmdl_root_raw


# How often to run scheduled scans and checks (in hours)
SCAN_INTERVAL_HOURS = int(os.environ.get("DG_SCAN_INTERVAL_HOURS", "24"))
CHECK_INTERVAL_HOURS = int(os.environ.get("DG_CHECK_INTERVAL_HOURS", "6"))

# AI configuration
AI_MODEL = os.environ.get("DG_AI_MODEL", "gpt-oss-120b")
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

# Power BI workspace name for refresh schedule sync
PBI_WORKSPACE = os.environ.get("DG_PBI_WORKSPACE", "")
PBI_SYNC_HOUR = int(os.environ.get("DG_PBI_SYNC_HOUR", "8"))
PBI_SYNC_MINUTE = int(os.environ.get("DG_PBI_SYNC_MINUTE", "15"))
PBI_TENANT_ID = os.environ.get("DG_PBI_TENANT_ID", "")
PBI_CLIENT_ID = os.environ.get("DG_PBI_CLIENT_ID", "")
PBI_CLIENT_SECRET = os.environ.get("DG_PBI_CLIENT_SECRET", "")
PBI_SYNC_WINDOWS_USER = os.environ.get("DG_PBI_SYNC_WINDOWS_USER", os.environ.get("USERNAME", ""))

# Headless Power BI sync with a saved Microsoft account (no tenant setup needed).
# The default client id is Microsoft's first-party Azure CLI public application,
# which is pre-consented in nearly every tenant and supports the device code
# flow plus long-lived refresh tokens. Override only if your tenant blocks it
# (the Azure PowerShell client 1950a258-227b-4e31-a9cf-717495945fc2 is a common
# alternative).
PBI_PUBLIC_CLIENT_ID = os.environ.get("DG_PBI_PUBLIC_CLIENT_ID", "04b07795-8ddb-461a-bbee-02f9e1bf7b46")
PBI_AUTH_TENANT = os.environ.get("DG_PBI_AUTH_TENANT", PBI_TENANT_ID or "organizations")
PBI_TOKEN_CACHE_PATH = os.environ.get("DG_PBI_TOKEN_CACHE", str(BASE_DIR / "pbi_token.json"))
PBI_USAGE_DAYS_BACK = int(os.environ.get("DG_PBI_USAGE_DAYS_BACK", "30"))

# Outbound proxy override for login.microsoftonline.com / api.powerbi.com,
# e.g. DG_PBI_PROXY=http://proxyhost:8080. When unset, the app uses
# HTTPS_PROXY/HTTP_PROXY if present, then asks Windows to evaluate the
# system proxy settings (including a configured PAC setup script), then
# goes direct. A PAC URL itself is NOT a valid value here.
PBI_PROXY = os.environ.get("DG_PBI_PROXY", "")
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

# Folder containing the exported PBI usage CSV files.
# The lowercase name is kept because this app is often configured from
# Windows environment variables created by hand.
USAGE_FILES_PATH = (
    os.environ.get("usage_files_path")
    or os.environ.get("USAGE_FILES_PATH")
    or os.environ.get("DG_USAGE_FILES_PATH")
    or ""
)

# PostgreSQL credentials for READ-ONLY freshness probing
# WARNING: These credentials must ONLY be used for SELECT queries.
# NEVER use them for INSERT, UPDATE, DELETE, DROP, or any write operation.
PGHOST = os.environ.get("PGHOST", "")
PGUSER = os.environ.get("PGUSER", "")
PGPASSWORD = os.environ.get("PGPASSWORD", "")
PGDATABASE = os.environ.get("PGDATABASE", "postgres")

# PostgreSQL credentials for the Import Data section, which WRITES
# (CREATE TABLE / TRUNCATE / INSERT). Kept separate from the read-only
# probing credentials above on purpose: user and password have no fallback,
# so imports stay disabled until DG_UPLOAD_PGUSER/DG_UPLOAD_PGPASSWORD are
# set to an account that is allowed to write. Host/port/database fall back
# to the probing connection's values.
UPLOAD_PGHOST = os.environ.get("DG_UPLOAD_PGHOST", "") or PGHOST
UPLOAD_PGPORT = os.environ.get("DG_UPLOAD_PGPORT", "") or os.environ.get("PGPORT", "") or "5432"
UPLOAD_PGDATABASE = os.environ.get("DG_UPLOAD_PGDATABASE", "") or PGDATABASE
UPLOAD_PGUSER = os.environ.get("DG_UPLOAD_PGUSER", "")
UPLOAD_PGPASSWORD = os.environ.get("DG_UPLOAD_PGPASSWORD", "")
UPLOAD_SCHEMA = os.environ.get("DG_UPLOAD_SCHEMA", "bi_reporting")
IMPORT_SCRIPT_DIR = os.environ.get("DG_IMPORT_SCRIPT_DIR", str(BASE_DIR / "generated_imports"))
