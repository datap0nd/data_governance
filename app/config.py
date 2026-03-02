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

# AI configuration
AI_MOCK = os.environ.get("DG_AI_MOCK", "true").lower() in ("true", "1", "yes")
AI_API_URL = os.environ.get("DG_AI_API_URL", "http://localhost:11434/v1/chat/completions")
AI_API_KEY = os.environ.get("DG_AI_API_KEY", "")
AI_MODEL = os.environ.get("DG_AI_MODEL", "gpt-oss-120b")

# Simulated freshness — generates realistic source_probes entries for demo mode
SIMULATE_FRESHNESS = os.environ.get("DG_SIMULATE_FRESHNESS", "true").lower() in ("true", "1", "yes")
