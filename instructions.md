# MX Analytics - Setup Guide (Windows)

## 1. Install Python

- Go to https://www.python.org/downloads/
- Download Python 3.12 or 3.13
- Run the installer
- **Check the box "Add Python to PATH"** at the bottom of the first screen
- Click "Install Now"

Verify in PowerShell:
```
python --version
```

## 2. Download the project

- Go to https://github.com/datap0nd/data_governance
- Click the green **"<> Code"** button > **"Download ZIP"**
- Extract the ZIP wherever you want, e.g. your Desktop, Documents, or a projects folder

You should end up with a folder like:
```
some_folder\
    data_governance-main\    <-- the code
```

That's it. The scripts figure out all paths automatically from wherever they are.

## 3. Install dependencies

Open PowerShell, `cd` into the code folder, and run:
```powershell
cd path\to\data_governance-main
pip install -r requirements.txt --index-url "https://bart.sec.samsung.net/artifactory/api/pypi/pypi-remote/simple" --trusted-host bart.sec.samsung.net
```

## 4. Install as a Windows service

Right-click PowerShell > **Run as Administrator**, then:
```powershell
cd path\to\data_governance-main
.\install_service.ps1
```

This:
- Creates the **MXAnalytics** Windows service using the bundled NSSM
- Configures it to auto-start on boot and restart on failure
- Stores the database one level up from the code folder (survives updates)
- Points the scanner at `\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals`
- Sets up log rotation

After install, the app runs at **http://localhost:8000** automatically.

The folder structure after install:
```
some_folder\
    data_governance-main\    <-- the code (replaced on updates)
    governance.db            <-- database (persists across updates)
    logs\                    <-- service logs
```

## 5. Run the first scan

- Open http://localhost:8000
- Go to **Scanner** in the nav bar
- Click **Run Scan Now**
- The scanner reads all `.pbix` files from `\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals`
- It extracts sources, tables, measures, visuals, and lineage from each report

After the scan, explore the pages:
- **Dashboard** - overview stats, health summary, stale source impact
- **Sources** - all data sources extracted from your reports
- **Reports** - all Power BI reports found
- **Lineage** - which sources feed which reports, down to the visual level
- **Best Practices** - TMDL checker findings (measure bloat, unused columns, etc.)
- **Actions** - stale/broken source tracking with assignment
- **Tasks** - kanban board for team task management

## 6. Network access

Other people on the same network can open the panel at:
```
http://YOUR_PC_IP:8000
```

To find your IP, run `ipconfig` in PowerShell and look for your IPv4 address.

## Updating

When there is a new version, run `update.ps1` from inside the code folder (double-click or right-click > Run with PowerShell).

It stops the service, downloads fresh code from GitHub, installs dependencies, and restarts the service. The database is not affected since it lives outside the code folder.

## Managing the service

From inside the code folder:
```powershell
.\tools\nssm.exe stop MXAnalytics
.\tools\nssm.exe start MXAnalytics
.\tools\nssm.exe restart MXAnalytics
.\tools\nssm.exe status MXAnalytics
.\tools\nssm.exe remove MXAnalytics confirm   # uninstall completely
```

## Optional CSV files

These files go in the project root (next to `governance.db`, one level above `data_governance-main`). None are required - the app works without them.

| File | Purpose | Format |
|------|---------|--------|
| `owners.csv` | Report and source owner names | `report_owner,business_owner` (no header) |
| `powerbi_links.csv` | Links to Power BI workspace | `report_name,powerbi_url` (no header) |
| `endpoint_url.txt` | AI chat endpoint URL | Single line, e.g. `https://your-llm/v1` |
| `latest_upload_date.csv` | PostgreSQL freshness dates (placeholder, not active) | `schema_name,table_name,last_activity` (with header) |

## Data source freshness

- **File sources** (CSV, Excel): probed for real by checking the file's last modified time at the path extracted from the Power BI report's M expression. These paths must be accessible from the machine running the app.
- **Database sources** (PostgreSQL, SQL Server): simulated with weighted random data (70% fresh, 20% stale, 10% outdated). No actual database connection is attempted. This is a placeholder until read-only database access is available.

## Running manually (without the service)

If you prefer not to use the service, run the app directly from the code folder:
```powershell
cd path\to\data_governance-main
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Press `Ctrl+C` to stop. The app will not auto-start on boot in this mode.

## Running the tests

```powershell
cd path\to\data_governance-main
python tests/test_scanner.py
```

This runs against the bundled test data, not your real reports.
