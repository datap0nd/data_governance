import os
from pathlib import Path

# Base directory of the app
BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite database file (the app's own database, not production)
DB_PATH = os.environ.get("DG_DB_PATH", str(BASE_DIR / "governance.db"))

# Root folder where TMDL report exports live
# Expected structure: {TMDL_ROOT}/reports/{report_name}/{report_name}.SemanticModel/Definition/Tables/*.tmdl
TMDL_ROOT = os.environ.get(
    "DG_TMDL_ROOT",
    os.path.expanduser(os.path.join("~", "documents", "Home", "projects", "data_governance")),
)

# How often to run scheduled scans and checks (in hours)
SCAN_INTERVAL_HOURS = int(os.environ.get("DG_SCAN_INTERVAL_HOURS", "24"))
CHECK_INTERVAL_HOURS = int(os.environ.get("DG_CHECK_INTERVAL_HOURS", "6"))
