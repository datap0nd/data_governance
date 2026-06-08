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


# Folders where Python scripts live (semicolon-separated for multiple paths)
_default_script_paths = [
    p.strip()
    for p in os.environ.get("DG_DEFAULT_SCRIPT_PATHS", "").split(";")
    if p.strip()
]
_env_scripts = os.environ.get("DG_SCRIPTS_PATH", "")
if _env_scripts:
    SCRIPTS_PATHS = [p.strip() for p in _env_scripts.split(";") if p.strip()]
else:
    SCRIPTS_PATHS = _default_script_paths
# Keep single-path compat
SCRIPTS_PATH = SCRIPTS_PATHS[0] if SCRIPTS_PATHS else ""

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


ADMIN_EVERYONE = _bool_env("DG_ADMIN_EVERYONE", True)
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
