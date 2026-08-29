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

Run `setup.ps1` once as Administrator to install the services and unattended
updater. After that, Metronome checks GitHub `main` every five minutes and
installs a tested commit automatically when production work is idle. Use
**System > Updates** to see the exact installed/latest commits and the last
check. Running `setup.ps1` again is only the manual recovery path; the database
is preserved.

Normal setup does not open or reauthenticate website browser profiles. To
deliberately refresh the ASAP/GSCM browser sessions, run:

```powershell
.\setup.ps1 -AuthenticateFlows
```

## Network access

To allow others on the network to access the panel, open port 8000 in Windows Firewall. Run this once in an admin PowerShell:

```powershell
New-NetFirewallRule -DisplayName "MX Analytics" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

Find the server's IP:
```powershell
(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' }).IPAddress
```

Others can then open:
```
http://<server-ip>:8000
```

First-time visitors will be prompted to enter their name. Scanning and updates can only be triggered from the server machine (localhost).

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
- **PostgreSQL sources**: probed by reading `MAX(pg_xact_commit_timestamp(xmin))` per table (requires `track_commit_timestamp = on` on the server). When the server has it disabled, the probe falls back to a `COUNT(*)` row-count-only probe and records status `unknown` — maintenance timestamps like `pg_stat_user_tables.last_analyze` are deliberately not used because they don't reflect data write time. Requires `PGHOST`, `PGUSER`, `PGPASSWORD` environment variables (set in `setup.ps1`).
- **Other database sources** (SQL Server, etc.): marked as unknown. No connection attempted.

## PostgreSQL connection - READ-ONLY CONSTRAINT

**WARNING: The PostgreSQL credentials configured in this tool must ONLY be used for READ operations (SELECT queries). NEVER use them for INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE, or any other write/DDL operation. This is a strict, non-negotiable constraint.**

Environment variables:
- `PGHOST` - PostgreSQL server IP/hostname
- `PGUSER` - PostgreSQL username
- `PGPASSWORD` - PostgreSQL password
- `PGDATABASE` - Database name (default: `postgres`)

The prober enforces read-only at the connection level via `SET default_transaction_read_only = ON`. Even so, the code must never contain any write queries against PostgreSQL. All probing is done via read-only SELECTs (commit-timestamp lookups and row counts) only.

## Running manually (without the service)

Stop the service first, then run directly:
```powershell
.\tools\nssm.exe stop MXAnalytics
cd path\to\data_governance-main
$env:DG_REPORTS_PATH = "\\MX-SHARE\Users\METOMX\Desktop\BI Report Originals"
$env:DG_DB_PATH = "..\governance.db"
$env:DG_AI_MOCK = "true"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000, go to Scanner, click Run Scan Now.

Press `Ctrl+C` to stop. Start the service again with `.\tools\nssm.exe start MXAnalytics`.
