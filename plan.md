# Data Governance Platform — Plan

## Problem

Reports go out with stale data, broken sources, or numbers that don't add up — and nobody notices until someone asks "why does this look wrong?" With ~60 Power BI reports, there's no way to manually check that every source is fresh and every number makes sense.

## Goal

A **web-based panel** (hosted on your local server) where you and your team can see, at a glance:
- Which data sources exist, when they last refreshed, and who owns them
- Which reports depend on which sources — **auto-detected from TMDL files**
- Whether the latest data passes quality and consistency checks
- Alerts when something breaks or looks off

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Browser (Panel)                       │
│  Dashboard  │  Sources  │  Reports  │  Checks  │  Alerts│
└──────────────────────┬──────────────────────────────────┘
                       │ REST API
┌──────────────────────┴──────────────────────────────────┐
│                 FastAPI Backend                          │
│  /sources  /reports  /checks  /lineage  /alerts         │
└──────┬───────────┬──────────────┬───────────────────────┘
       │           │              │
┌──────┴──────┐  ┌─┴────────┐  ┌─┴───────┐  ┌───────────┐
│ PostgreSQL  │  │Scheduler │  │ Checks  │  │   TMDL    │
│ (metadata)  │  │(APSched) │  │ Engine  │  │  Scanner  │
└─────────────┘  └──────────┘  └─────────┘  └───────────┘
```

**Stack:**
- **Backend:** Python + FastAPI
- **App database:** PostgreSQL (you already run it — no reason to add SQLite)
- **Scheduler:** APScheduler (runs checks and scans on a cron)
- **Frontend:** Lightweight — HTML + vanilla JS + a CSS framework (Pico CSS or similar), no build step
- **Deployment:** Runs on your local server; single `docker compose up` to start
- **All reports are Power BI** — the system is built around this assumption

---

## Core Modules

### 1. TMDL Scanner (the big one)

You have ~60 Power BI reports. Manually registering sources for each one is not realistic. Instead, the system **scans TMDL files** to auto-detect everything.

**How it works:**

Power BI projects (when saved as TMDL or extracted via pbi-tools/Tabular Editor) produce files like:
```
MyReport.Dataset/
├── model.tmdl
├── tables/
│   ├── Main.tmdl
│   ├── SKU Master.tmdl
│   └── MP Plan.tmdl
└── relationships.tmdl
```

Each table's `.tmdl` file contains its source — a SQL query, an Excel path, a SharePoint URL, etc. The scanner:

1. **Walks a root folder** where all your TMDL exports live (one subfolder per report)
2. **Parses each table file** to extract:
   - Table name
   - Source type (SQL Server, Excel, CSV, SharePoint, etc.)
   - Source connection (server + database, file path, URL)
   - Source query (the M/Power Query expression or SQL)
3. **Builds the lineage automatically:** Report X uses tables A, B, C → tables A, B, C come from sources S1, S2
4. **Detects changes:** on each scan, compares against what's already registered — flags new sources, removed sources, changed queries

**What you need to do once:** Export/save each of your 60 reports' datasets as TMDL into a shared folder on the server. This can be scripted with Tabular Editor CLI or pbi-tools.

**The scanner runs on a schedule** (e.g., daily) and also has a manual trigger button in the panel.

**Scanner output example:**
```
Scan complete: 60 reports processed
  → 47 unique data sources found
  → 3 new sources detected (not previously registered)
  → 1 source query changed (SKU_Master in "Weekly Sales")
  → 2 reports have broken source references
```

---

### 2. Source Registry

Track every data source your reports depend on. **Initially populated by the TMDL scanner**, then enriched manually (owner, refresh schedule, tags).

**What gets stored per source:**
| Field | Example |
|---|---|
| Name | `SAP_Orders_Extract` |
| Type | `SQL Server` / `Excel` / `SharePoint` / `CSV` |
| Connection | server/database or file path (encrypted at rest) |
| Source query | The M expression or SQL used to pull data |
| Owner | `rafael` (manually assigned) |
| Expected refresh | `daily by 07:00` (manually set) |
| Last known refresh | `2026-02-27 06:45` (from probe) |
| Status | `fresh` / `stale` / `error` |
| Used by (reports) | Auto-populated from TMDL scan |
| Tags | `sales`, `orders` |

**How freshness is checked (probes):**
- **PostgreSQL tables:** `SELECT MAX(updated_at) FROM table` or check pg_stat_user_tables for last modification
- **SQL Server tables:** Similar lightweight query
- **Excel/CSV files:** File modified timestamp on the server
- **SharePoint:** Last modified date via file system or mapped drive

**API:**
- `GET /sources` — list all, with current status
- `POST /sources` — manually register a source (rarely needed since scanner handles this)
- `GET /sources/{id}/history` — freshness history over time
- `POST /sources/{id}/probe` — trigger a manual freshness check

---

### 3. Report Inventory

Track every Power BI report. **Auto-populated by the TMDL scanner.**

**What gets stored per report:**
| Field | Example |
|---|---|
| Name | `Weekly Sales Dashboard` |
| TMDL path | `/tmdl_exports/weekly_sales/` |
| Owner | `rafael` (manually assigned) |
| Recipients | `commercial-team` (manually set) |
| Frequency | `weekly, Monday 09:00` (manually set) |
| Sources used | Auto-detected from TMDL scan |
| Last scan | `2026-02-27 06:00` |
| Status | `current` / `stale sources` / `check failures` |

A report's status is **derived** — if any of its sources are stale or any of its checks are failing, the report is flagged.

**API:**
- `GET /reports` — list all, with derived status
- `GET /reports/{id}/lineage` — which sources feed this report (from TMDL scan)

---

### 4. Lineage Map

**Fully automated from TMDL scanning** — no manual mapping needed.

The graph is: **Data Source → Table → Report**

Example:
```
PostgreSQL (orders DB)  ──→  Main table       ──→  Weekly Sales Dashboard
                        ──→  Main table       ──→  Monthly KPI Report
Excel (SKU_Master.xlsx) ──→  SKU Master table ──→  Weekly Sales Dashboard
                        ──→  SKU Master table ──→  Product Mix Report
SharePoint (plan.xlsx)  ──→  MP Plan table    ──→  Plan vs Actual Report
```

**Panel view:** Interactive graph (Cytoscape.js) showing the full picture. Click a source to highlight all downstream reports. Click a report to highlight all upstream sources. Color-coded by status (green = fresh, yellow = warning, red = stale/error).

---

### 5. Checks Engine

Automated rules that validate data quality and consistency.

**Check types:**

| Check | What it does | Example |
|---|---|---|
| **Freshness** | Source refreshed within expected window | `orders` table refreshed < 24h ago |
| **Row count** | Row count within expected range or delta | Orders table has 50k–200k rows |
| **Null rate** | Column null % below threshold | `oe_total_sales_price` < 1% nulls |
| **Range** | Values within expected bounds | `oe_discount_amount` between 0 and 10,000 |
| **Uniqueness** | Column has no unexpected duplicates | `id` column is unique |
| **Referential** | FK values exist in parent table | All `SKU` codes exist in `SKU_Master` |
| **Period-over-period** | Metric doesn't swing wildly vs last period | Total Sales CY vs LY within ±50% |
| **Cross-report** | Same metric matches across reports | "Total Revenue" in Report A = Report B |
| **Custom SQL** | Run any query, assert result | `SELECT COUNT(*) FROM orders WHERE amount < 0` = 0 |

**How checks are defined:**

```yaml
# checks/sales_orders.yml
source: orders_db
checks:
  - type: freshness
    max_age_hours: 24

  - type: null_rate
    column: oe_total_sales_price
    max_percent: 1.0

  - type: range
    column: oe_discount_amount
    min: 0
    max: 10000

  - type: row_count
    min: 50000

  - type: period_over_period
    metric: "SUM(oe_total_sales_price)"
    compare: last_week
    max_change_percent: 50
```

Checks are YAML files — you don't need to write code to add new checks. Just add a YAML entry.

**Checks that run against your PostgreSQL directly** — the engine connects to your actual data sources to validate the data, not just metadata.

---

### 6. Alerts

When a check fails or a source goes stale, notify.

**Channels (phased):**
1. **Phase 1:** Panel shows red/yellow/green badges — you check the panel
2. **Phase 2:** Email alerts (via SMTP) for critical failures
3. **Phase 3:** Teams webhook integration (since you likely use Teams with Power BI)

**Alert rules:**
- Freshness overdue by >2h → warning (yellow)
- Freshness overdue by >6h → critical (red)
- Any check failure → critical
- Period-over-period anomaly → warning
- TMDL scan detects broken source reference → critical

---

### 7. Web Panel (Frontend)

Single-page app, no framework, no build step. Served by FastAPI from the same server.

**Pages:**

#### Dashboard (home)
```
┌─────────────────────────────────────────────────────────┐
│  DATA GOVERNANCE PANEL                  Last scan: 5m   │
├───────────┬───────────┬───────────┬─────────────────────┤
│  Sources  │  Reports  │  Checks   │   Alerts            │
│   44 ✓    │   58 ✓    │  120/130✓ │   3 active          │
│    2 ⚠    │    1 ⚠    │    6 ⚠    │                     │
│    1 ✗    │    1 ✗    │    4 ✗    │                     │
├───────────┴───────────┴───────────┴─────────────────────┤
│  RECENT ALERTS                                          │
│  ⚠ orders_db stale (last refresh 8h ago)                │
│  ✗ Weekly Sales: null rate 3.2% > threshold 1%          │
│  ⚠ Discount avg swung +62% vs last week                 │
│  ✗ Product Mix: broken source ref (SKU_Master_v2.xlsx)  │
└─────────────────────────────────────────────────────────┘
```

#### Sources page
Table of all sources with status badges, last refresh, owner, and how many reports depend on them. Click to expand → history chart + downstream reports list.

#### Reports page
Table of all 60 reports with derived status. Click to expand → upstream sources, checks, last TMDL scan result.

#### Checks page
Filterable table: all checks grouped by source. Pass/fail/warn with timestamps. Click to see history.

#### Lineage page
Full interactive graph of sources → tables → reports.

#### Scanner page
TMDL scan status: last run, results summary, changelog (what changed since last scan). Button to trigger manual re-scan.

---

## Data Model (PostgreSQL)

```sql
-- Data sources (auto-populated by TMDL scanner, enriched manually)
CREATE TABLE sources (
    id              SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    type            TEXT NOT NULL,               -- postgresql, sql_server, excel, csv, sharepoint
    connection_info TEXT,                         -- encrypted connection string / path
    source_query    TEXT,                         -- the M/SQL expression (from TMDL)
    owner           TEXT,
    refresh_schedule TEXT,                        -- cron or human-readable
    tags            TEXT,                         -- comma-separated
    discovered_by   TEXT DEFAULT 'manual',        -- 'tmdl_scan' or 'manual'
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Source freshness probes
CREATE TABLE source_probes (
    id              SERIAL PRIMARY KEY,
    source_id       INTEGER REFERENCES sources(id),
    probed_at       TIMESTAMPTZ DEFAULT NOW(),
    last_data_at    TIMESTAMPTZ,                 -- when the data was last updated
    row_count       BIGINT,
    status          TEXT,                         -- fresh, stale, error
    message         TEXT
);

-- Power BI reports (auto-populated by TMDL scanner)
CREATE TABLE reports (
    id              SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    tmdl_path       TEXT,                         -- path to TMDL export folder
    owner           TEXT,
    recipients      TEXT,
    frequency       TEXT,
    last_published  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Tables within reports (from TMDL scan)
CREATE TABLE report_tables (
    id              SERIAL PRIMARY KEY,
    report_id       INTEGER REFERENCES reports(id),
    table_name      TEXT NOT NULL,
    source_id       INTEGER REFERENCES sources(id),     -- which source feeds this table
    source_expression TEXT,                              -- the M/Power Query expression
    last_scanned    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(report_id, table_name)
);

-- Lineage edges (source → report, derived from report_tables)
-- This is a view, not a table:
-- CREATE VIEW lineage AS
--   SELECT DISTINCT source_id, report_id FROM report_tables WHERE source_id IS NOT NULL;

-- TMDL scan history
CREATE TABLE scan_runs (
    id              SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    reports_scanned INTEGER,
    sources_found   INTEGER,
    new_sources     INTEGER,
    changed_queries INTEGER,
    broken_refs     INTEGER,
    status          TEXT,                         -- completed, failed, running
    log             TEXT
);

-- Check definitions
CREATE TABLE checks (
    id              SERIAL PRIMARY KEY,
    source_id       INTEGER REFERENCES sources(id),
    type            TEXT NOT NULL,
    config          JSONB NOT NULL,               -- check parameters
    severity        TEXT DEFAULT 'critical',       -- critical, warning, info
    enabled         BOOLEAN DEFAULT TRUE
);

-- Check results
CREATE TABLE check_results (
    id              SERIAL PRIMARY KEY,
    check_id        INTEGER REFERENCES checks(id),
    ran_at          TIMESTAMPTZ DEFAULT NOW(),
    status          TEXT NOT NULL,                 -- pass, fail, warn, error
    value           DOUBLE PRECISION,
    message         TEXT
);

-- Alerts
CREATE TABLE alerts (
    id                  SERIAL PRIMARY KEY,
    check_result_id     INTEGER REFERENCES check_results(id),
    source_id           INTEGER REFERENCES sources(id),
    scan_run_id         INTEGER REFERENCES scan_runs(id),
    severity            TEXT NOT NULL,
    message             TEXT NOT NULL,
    acknowledged        BOOLEAN DEFAULT FALSE,
    acknowledged_by     TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Project Structure

```
data_governance/
├── plan.md
├── README.md
├── docker-compose.yml          # FastAPI app + PostgreSQL
├── Dockerfile
├── requirements.txt
├── app/
│   ├── main.py                 # FastAPI app, startup, scheduler
│   ├── config.py               # Settings (DB connection, TMDL root path, etc.)
│   ├── database.py             # PostgreSQL connection (asyncpg or psycopg)
│   ├── models.py               # Pydantic models (request/response)
│   ├── routers/
│   │   ├── sources.py          # /sources endpoints
│   │   ├── reports.py          # /reports endpoints
│   │   ├── checks.py           # /checks endpoints
│   │   ├── lineage.py          # /lineage endpoints
│   │   ├── alerts.py           # /alerts endpoints
│   │   └── scanner.py          # /scanner endpoints (trigger scan, view history)
│   ├── scanner/
│   │   ├── tmdl_parser.py      # Parse individual .tmdl files
│   │   ├── walker.py           # Walk the TMDL root folder, find all reports
│   │   ├── source_matcher.py   # Match parsed sources to registered sources
│   │   └── runner.py           # Orchestrate a full scan run
│   ├── checks/
│   │   ├── engine.py           # Run checks, record results
│   │   ├── probes.py           # Source freshness probes
│   │   └── builtin.py          # Built-in check implementations
│   ├── scheduler.py            # APScheduler setup (scans + checks)
│   └── static/
│       ├── index.html          # SPA shell
│       ├── app.js              # Frontend logic
│       └── style.css           # Styles
├── checks/                     # YAML check definitions
│   └── example.yml
└── tests/
    ├── test_scanner.py
    ├── test_sources.py
    ├── test_checks.py
    └── test_api.py
```

---

## Implementation Phases

### Phase 1 — TMDL Scanner + Foundation
**Goal:** Scan all 60 reports, auto-build source registry and lineage, show it in the panel.

1. Set up FastAPI app with PostgreSQL (docker-compose with both services)
2. Build the TMDL parser — parse table definitions, extract source expressions
3. Build the folder walker — find all report TMDL exports under a root folder
4. Build the source matcher — deduplicate sources across reports (same DB table used by 10 reports = 1 source)
5. Store everything in PostgreSQL (sources, reports, report_tables, scan_runs)
6. Build the web panel: dashboard, sources list, reports list, scanner status
7. Build the lineage graph view
8. Script to bulk-export your 60 reports' datasets to TMDL (using Tabular Editor CLI)

**Deliverable:** You run the scanner, it processes all 60 reports, and you open a browser to see every source, every report, and how they connect. No manual data entry.

### Phase 2 — Freshness Probes + Checks
**Goal:** Know if your sources are up to date and if the data makes sense.

1. Implement freshness probes for PostgreSQL (query-based)
2. Implement freshness probes for Excel/CSV (file timestamp)
3. Implement all check types (null rate, range, row count, etc.)
4. YAML check definition loader
5. Scheduler: run probes + checks automatically (e.g., every morning before reports go out)
6. Check results history + trend charts in the panel
7. Report status badges derived from source freshness + check results

**Deliverable:** Every morning the system checks if sources are fresh and data is valid. You open the panel and see green/yellow/red for everything.

### Phase 3 — Alerts & Team Access
**Goal:** Proactive notifications + multi-user access.

1. Email alerts (SMTP) for critical failures
2. Teams webhook integration
3. Alert acknowledgment in the panel
4. Simple user accounts (email + password) so the team can access the panel
5. Audit log (who acknowledged what, when)

**Deliverable:** Team gets notified when something breaks. Everyone can check the panel.

### Phase 4 — Advanced (if Power BI API is available)
**Goal:** Deeper Power BI integration.

1. Power BI REST API integration (if subscription allows):
   - Auto-detect dataset refresh status
   - Pull dataset metadata without needing TMDL exports
   - Trigger refreshes from the panel
2. Anomaly detection with statistical methods (z-score, IQR)
3. Scheduled governance report (PDF summary emailed weekly)
4. TMDL diff view — show exactly what changed between scans

---

## Key Design Decisions

| Decision | Choice | Why |
|---|---|---|
| App database | PostgreSQL | You already run it, no reason to add another DB |
| Lineage detection | TMDL file scanning | 60 reports can't be mapped manually; TMDL files contain all source info |
| Frontend | No framework (HTML + JS) | No build step, you don't need to learn React/npm |
| Check definitions | YAML files | Easy to read and edit without writing code |
| Scheduler | In-process (APScheduler) | Simple, no extra services to manage |
| Deployment | Docker on local server | You have the server, docker keeps it isolated and reproducible |
| Power BI API | Phase 4 / optional | May not be available on your subscription; TMDL scanning covers the core need |

---

## Prerequisites / What You Need to Provide

1. **TMDL exports of your 60 reports** — we'll write a script to help with this, but you need Tabular Editor or pbi-tools installed on a machine with access to your Power BI datasets
2. **PostgreSQL connection details** — for the governance app's own database (a new database on your existing server)
3. **PostgreSQL connection details** — for your actual data sources (read-only access for probes and checks)
4. **A folder on the server** — to store TMDL exports (the scanner reads from here)
5. **Your local server details** — OS, Docker availability, network access (so the team can reach the panel)

---

## What This Does NOT Do

- **Not an ETL tool** — it doesn't move or transform data, it monitors it
- **Not a BI tool** — it doesn't create reports, it tracks them
- **Not a replacement for Power BI** — it's a companion that watches over your Power BI ecosystem
- **Not dependent on Power BI API** — the TMDL scanner approach works regardless of your subscription
