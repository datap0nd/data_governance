# MX Analytics - Setup Guide (Windows)

## 1. Install Python (one time only)

- Go to https://www.python.org/downloads/
- Download Python 3.12 or 3.13
- **Check the box "Add Python to PATH"** during install
- Click "Install Now"

Verify in PowerShell:
```
python --version
```

## 2. Run setup.ps1

This one script does everything: downloads code, installs dependencies, creates the service.

1. Download the ZIP from https://github.com/datap0nd/data_governance (Code > Download ZIP)
2. Extract it anywhere (Desktop, Documents, wherever)
3. Right-click PowerShell > **Run as Administrator**
4. `cd` into the extracted folder and run:

```powershell
cd path\to\data_governance-main
.\setup.ps1
```

That's it. The app runs at **http://localhost:8000** automatically on boot.

## 3. Run the first scan

- Open http://localhost:8000
- Go to **Scanner** > **Run Scan Now**
- The scanner reads `.pbix` files from `\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals`

## Updating

When there is a new version, run `setup.ps1` again (as Administrator). It downloads the latest code, reinstalls everything, and restarts the service. The database is not affected.

## Network access

Others on the same network can open the panel at:
```
http://YOUR_PC_IP:8000
```

Run `ipconfig` to find your IP.

## Managing the service

From inside the code folder:
```powershell
.\tools\nssm.exe stop MXAnalytics
.\tools\nssm.exe start MXAnalytics
.\tools\nssm.exe restart MXAnalytics
.\tools\nssm.exe status MXAnalytics
.\tools\nssm.exe remove MXAnalytics confirm   # uninstall
```

## Optional CSV files

Place in the project root (next to `governance.db`, one level above the code folder). None are required.

| File | Purpose | Format |
|------|---------|--------|
| `owners.csv` | Report and source owner names | `report_owner,business_owner` (no header) |
| `powerbi_links.csv` | Links to Power BI workspace | `report_name,powerbi_url` (no header) |
| `endpoint_url.txt` | AI chat endpoint URL | Single line, e.g. `https://your-llm/v1` |

## Data source freshness

- **File sources** (CSV, Excel): probed by checking the file's last modified time at the path extracted from the Power BI report. Paths must be accessible from this machine.
- **Database sources** (PostgreSQL, SQL Server): simulated. No database connection is attempted.

## Running manually (without the service)

Stop the service first, then run directly:
```powershell
.\tools\nssm.exe stop MXAnalytics
cd path\to\data_governance-main
$env:DG_REPORTS_PATH = "\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals"
$env:DG_DB_PATH = "..\governance.db"
$env:DG_SIMULATE_FRESHNESS = "false"
$env:DG_AI_MOCK = "true"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000, go to Scanner, click Run Scan Now.

Press `Ctrl+C` to stop. Start the service again with `.\tools\nssm.exe start MXAnalytics`.
