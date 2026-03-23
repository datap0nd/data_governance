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
- Create the project folder and extract the ZIP into it:

```
%USERPROFILE%\documents\Home\projects\data_governance\data_governance-main\
```

The folder structure should look like:
```
data_governance\
    data_governance-main\    <-- the code (replaced on updates)
    governance.db            <-- database (created on first scan, persists across updates)
    logs\                    <-- service logs (created by install_service.ps1)
    nssm\                    <-- service manager (downloaded by install_service.ps1)
```

## 3. Install dependencies

Open PowerShell and run:
```powershell
cd "$env:USERPROFILE\documents\Home\projects\data_governance\data_governance-main"
pip install -r requirements.txt --index-url "https://bart.sec.samsung.net/artifactory/api/pypi/pypi-remote/simple" --trusted-host bart.sec.samsung.net
```

## 4. Install as a Windows service

Right-click PowerShell > **Run as Administrator**, then:
```powershell
cd "$env:USERPROFILE\documents\Home\projects\data_governance\data_governance-main"
.\install_service.ps1
```

This does the following:
- Downloads NSSM (Non-Sucking Service Manager) if not present
- Creates the **MXAnalytics** Windows service
- Configures it to auto-start on boot and restart on failure
- Sets the database path outside the code folder (so it survives updates)
- Points the scanner at `Z:\METOMX\Desktop\BI Report Originals`
- Sets up log rotation in the `logs\` folder

After install, the app runs at **http://localhost:8000** automatically.

## 5. Run the first scan

- Open http://localhost:8000
- Go to **Scanner** in the nav bar
- Click **Run Scan Now**
- The scanner reads all `.pbix` files from `Z:\METOMX\Desktop\BI Report Originals`
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

When there is a new version, run `update.ps1` (double-click or right-click > Run with PowerShell).

It stops the service, downloads fresh code from GitHub, installs dependencies, and restarts the service. The database is not affected since it lives outside the code folder.

## Managing the service

```powershell
nssm stop MXAnalytics        # stop
nssm start MXAnalytics       # start
nssm restart MXAnalytics     # restart
nssm status MXAnalytics      # check status
nssm remove MXAnalytics confirm   # uninstall completely
```

Logs are at `%USERPROFILE%\documents\Home\projects\data_governance\logs\`.

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

If you prefer not to use the service, you can run the app directly:
```powershell
cd "$env:USERPROFILE\documents\Home\projects\data_governance\data_governance-main"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Press `Ctrl+C` to stop. The app will not auto-start on boot in this mode.

## Running the tests

```powershell
cd "$env:USERPROFILE\documents\Home\projects\data_governance\data_governance-main"
python tests/test_scanner.py
```

This runs against the bundled test data, not your real reports.
