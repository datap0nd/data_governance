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
4. Run a scan — the prober runs automatically after each scan

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
