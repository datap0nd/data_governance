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
