import os
from pathlib import Path

# Base directory of the app
BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite database file (the app's own database, not production)
DB_PATH = os.environ.get("DG_DB_PATH", str(BASE_DIR / "governance.db"))

# Folder where .pbix reports live (or TMDL exports as fallback)
REPORTS_PATH = os.environ.get(
    "DG_REPORTS_PATH",
    os.path.expanduser(
        os.path.join("~", "documents", "Home", "projects", "data_governance", "BI Report Originals")
    ),
)

# Legacy alias
TMDL_ROOT = os.environ.get("DG_TMDL_ROOT", REPORTS_PATH)

# Directory where credential/config files live (project root)
CREDENTIALS_DIR = BASE_DIR

# How often to run scheduled scans and checks (in hours)
SCAN_INTERVAL_HOURS = int(os.environ.get("DG_SCAN_INTERVAL_HOURS", "24"))
CHECK_INTERVAL_HOURS = int(os.environ.get("DG_CHECK_INTERVAL_HOURS", "6"))
