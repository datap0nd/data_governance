import os
from pathlib import Path

# Base directory of the app
BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite database file (the app's own database, not production)
DB_PATH = os.environ.get("DG_DB_PATH", str(BASE_DIR / "governance.db"))

# Folder where .pbix reports live (or TMDL exports as fallback)
_default_reports = os.path.expanduser(
    os.path.join("~", "documents", "Home", "projects", "data_governance", "BI Report Originals")
)

# If the default path doesn't exist, fall back to test_data inside the project
if not os.path.isdir(_default_reports):
    _default_reports = str(BASE_DIR / "test_data")

REPORTS_PATH = os.environ.get("DG_REPORTS_PATH", _default_reports)

# DG_TMDL_ROOT is the documented env var — prefer it over REPORTS_PATH
TMDL_ROOT = os.environ.get("DG_TMDL_ROOT", REPORTS_PATH)


# How often to run scheduled scans and checks (in hours)
SCAN_INTERVAL_HOURS = int(os.environ.get("DG_SCAN_INTERVAL_HOURS", "24"))
CHECK_INTERVAL_HOURS = int(os.environ.get("DG_CHECK_INTERVAL_HOURS", "6"))

# AI / LLM settings
AI_MOCK = os.environ.get("DG_AI_MOCK", "true").lower() in ("true", "1", "yes")
AI_MODEL = os.environ.get("DG_AI_MODEL", "oss-120b")
AI_API_KEY = os.environ.get("DG_AI_API_KEY", "")

# LiteLLM endpoint: read from env var, or from endpoint_url.txt file, or fallback
_endpoint_file = Path(os.environ.get("DG_ENDPOINT_FILE", str(BASE_DIR.parent / "endpoint_url.txt")))
_default_url = "http://localhost:4000"
if os.environ.get("DG_AI_URL"):
    AI_BASE_URL = os.environ["DG_AI_URL"]
elif _endpoint_file.exists():
    AI_BASE_URL = _endpoint_file.read_text().strip()
else:
    AI_BASE_URL = _default_url

# Simulated freshness — generates realistic source_probes entries for demo mode
SIMULATE_FRESHNESS = os.environ.get("DG_SIMULATE_FRESHNESS", "true").lower() in ("true", "1", "yes")
