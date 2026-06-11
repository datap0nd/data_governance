# Data Governance Panel

A web-based panel that monitors your Power BI reports, tracks data sources, and validates data quality.

## Quick Start (Windows)

### 1. Install Python

Download Python 3.11+ from [python.org](https://www.python.org/downloads/). During install, check "Add Python to PATH".

### 2. Set up the project

Open a terminal (PowerShell or Command Prompt) in this folder:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure TMDL path

Set where your TMDL report exports live. By default it looks at:
```
%USERPROFILE%\documents\projects\data_governance\reports\
```

To change it, set the environment variable before running:
```bash
set DG_TMDL_ROOT=C:\Users\YourName\documents\projects\data_governance
```

### 4. Run the app

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open your browser to `http://localhost:8000`

For other people on the network to access it, they go to `http://YOUR_COMPUTER_IP:8000`

### Access

All registered users have the same application access, including remote PCs on the network. There is no app-level admin allowlist or IP toggle.

### Unattended Power BI sync

The sync picks the first available auth mode, in this order:

1. **Service principal** (`DG_PBI_TENANT_ID` + `DG_PBI_CLIENT_ID` + `DG_PBI_CLIENT_SECRET`): fully unattended, requires tenant-admin setup.
2. **Saved Microsoft account (recommended when no tenant access exists)**: open the panel, go to Scanner, click **Connect Power BI**, and enter the shown code at microsoft.com/devicelogin from any device (your own laptop or phone works). After this one-time sign-in, every sync runs headless inside the app with a silently refreshed token: no PowerShell window, no account picker, and it works while the PC is locked or the RDP session is disconnected.
3. **Interactive fallback**: the legacy scheduled-task flow that opens a PowerShell window plus the Microsoft account picker in the sync user's session. Only used when neither of the above is configured.

#### Saved Microsoft account details

- The sign-in is stored in `pbi_token.json` next to the app, encrypted with Windows DPAPI for the account that runs the service. Treat the file as a credential; it is gitignored and `Disconnect account` on the Scanner page deletes it.
- The refresh token rotates on every sync, so daily syncs keep the sign-in alive indefinitely. If the org forces a re-auth (password change, revocation, or a conditional-access sign-in frequency policy), the sync fails with a clear "reconnect" message, raises a critical alert on the dashboard, and the Scanner page shows a **Reconnect needed** badge. Click Connect Power BI again to fix it; nothing hangs.
- The default sign-in client is Microsoft's first-party Azure CLI public app, pre-consented in nearly every tenant. If your tenant blocks it or blocks the device code flow, set `DG_PBI_PUBLIC_CLIENT_ID` (the Azure PowerShell client `1950a258-227b-4e31-a9cf-717495945fc2` is a common alternative).
- Proxy handling: the app resolves the outbound proxy automatically, in this order: `DG_PBI_PROXY`, standard `HTTPS_PROXY`/`HTTP_PROXY` env vars, then the Windows system proxy settings (including a configured PAC "setup script"), then direct. Set `DG_PBI_PROXY=http://proxyhost:port` only if auto-detection picks the wrong route; a `.pac` script URL itself is not a valid proxy value. Connect failures show which proxy was used.
- Usage sync (activity events) still requires the signed-in account to hold the Power BI/Fabric administrator role, same as before.

#### Interactive fallback (legacy)

Without a saved account or service principal, the sync needs an active, unlocked interactive session so the Microsoft account picker can be selected.

For RDP-heavy machines, setup installs an automatic RDP console guard. The guard runs every five minutes, also runs after RDP disconnect events, and runs once immediately before a Power BI sync starts. It targets the configured sync Windows user and transfers that user's disconnected RDP session back to the console with `tscon`, so the sync does not depend on every RDP user remembering a manual step.

This is a workaround for delegated interactive auth, not a secure headless design. Use it only on a controlled machine where leaving the sync user's desktop available at the console is acceptable.

Set the target sync user if the service account is not the same account used during setup:

```bash
DG_PBI_SYNC_WINDOWS_USER=<windows-user-name>
```

The guard only repairs disconnected sessions by default. It does not kick an actively connected RDP user. If the sync user is actively connected through RDP, that counts as ready because the account picker is visible in that session. If someone is actively using a different account at the sync time, the sync may still have to wait or fail cleanly. That case needs an operating rule, such as reserving the sync window or running the sync under a dedicated service account that users do not RDP into.

If multiple Microsoft accounts are cached, set this environment variable for the service account so the picker chooses the expected account:

```bash
DG_PBI_ACCOUNT=<user@example.com>
```

The sync scripts also check for a locked/disconnected session before interactive sign-in and record a failed sync instead of hanging overnight. If sign-in reaches the account picker but does not finish, the process is stopped after `DG_PBI_CONNECT_TIMEOUT_SECONDS`, defaulting to `120`.

The current RDP guard diagnostics are available at:

```text
http://localhost:8000/api/scanner/pbi-sync/status
```

Scheduled emails are blocked by default if the latest completed Power BI refresh sync is older than `DG_EMAIL_MAX_PBI_SYNC_AGE_HOURS` hours, defaulting to `24`. After the configured overall refresh time plus `DG_EMAIL_PBI_SYNC_GRACE_MINUTES`, defaulting to `30`, emails require that day's sync to have completed. If PBI is stale, the email is deferred and retried after `DG_EMAIL_PBI_STALE_RETRY_MINUTES`, defaulting to `30`. Set `DG_EMAIL_REQUIRE_FRESH_PBI=false` only if stale PBI metadata is acceptable.

If service-principal access is later approved, set `DG_PBI_TENANT_ID`, `DG_PBI_CLIENT_ID`, and `DG_PBI_CLIENT_SECRET` to run the sync without an interactive account picker.

Users can change the daily overall refresh time from System > Refresh Schedule. The job runs the report scan, source probe, and Power BI sync together. The default can also be set with `DG_OVERALL_REFRESH_HOUR` and `DG_OVERALL_REFRESH_MINUTE`.

### 5. Run the scanner

Click "Run Scan Now" on the Scanner page, or hit the API:
```bash
curl -X POST http://localhost:8000/api/scanner/run
```

## PostgreSQL "Last Updated" Probing

To show when PostgreSQL tables were last updated:

1. Export the query results from pgAdmin as a CSV named `latest_upload_date.csv`
2. Place it in the `data_governance` project root (same level as `app/`)
3. CSV must have columns: `schema_name, table_name, last_activity` (with a header row)
4. Run a scan - the prober runs automatically after each scan

You can also trigger a probe independently:
```bash
curl -X POST http://localhost:8000/api/scanner/probe
```

### Debugging probe matching

If "Last Updated" shows empty, open this URL in your browser to see what the CSV contains vs what's stored:
```
http://localhost:8000/api/scanner/probe/debug
```

Or paste this into Chrome DevTools Console (F12 → Console):
```js
fetch("/api/scanner/probe/debug").then(r=>r.json()).then(d=>console.log(d))
```

This shows `csv_samples` (what the CSV has) and `postgresql_sources` (what's in the database) side-by-side so you can spot the mismatch.

## Running Tests

```bash
python tests/test_scanner.py
```

## Expected Folder Structure for TMDL Exports

```
{DG_TMDL_ROOT}/
└── reports/
    ├── Weekly_Sales/
    │   └── Weekly_Sales.SemanticModel/
    │       └── Definition/
    │           ├── Tables/
    │           │   ├── Main.tmdl
    │           │   ├── SKU Master.tmdl
    │           │   └── ...
    │           └── expressions.tmdl  (optional, for parameters)
    ├── Monthly_KPI/
    │   └── ...
    └── ...
```

## What it does

- **TMDL Scanner**: Parses your Power BI TMDL files to auto-detect all data sources (SQL Server, Excel, CSV)
- **Source Registry**: Tracks every data source, deduplicates across reports
- **Report Inventory**: Lists all reports with their upstream sources
- **Lineage Map**: Shows which sources feed which reports
- **Alerts**: Flags stale sources and broken references

See [plan.md](plan.md) for the full architecture and roadmap.
