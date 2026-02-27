# Data Governance Platform — Plan

## Problem

Reports go out with stale data, broken sources, or numbers that don't add up — and nobody notices until someone asks "why does this look wrong?" There's no single place to see: are my sources fresh? Are my reports consistent? Do the numbers pass basic sanity checks?

## Goal

A **web-based panel** where you and your team can see, at a glance:
- Which data sources exist, when they last refreshed, and who owns them
- Which reports depend on which sources (lineage)
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
┌──────┴──┐  ┌─────┴─────┐  ┌────┴────┐
│ SQLite  │  │ Scheduler │  │ Checks  │
│ (meta)  │  │ (APSched) │  │ Engine  │
└─────────┘  └───────────┘  └─────────┘
```

**Stack:**
- **Backend:** Python + FastAPI
- **Database:** SQLite (zero setup, single file, good enough until you outgrow it)
- **Scheduler:** APScheduler (runs checks on a cron)
- **Frontend:** Lightweight — HTML + vanilla JS + a CSS framework (Pico CSS or similar), no build step needed
- **Deployment:** Runs locally or on any VM; single `docker compose up` to start

---

## Core Modules

### 1. Source Registry

Track every data source your reports depend on.

**What gets stored per source:**
| Field | Example |
|---|---|
| Name | `SAP_Orders_Extract` |
| Type | `SQL Server` / `Excel` / `API` / `CSV` / `SharePoint` |
| Connection | server/path/URL (encrypted at rest) |
| Owner | `rafael` |
| Expected refresh | `daily by 07:00` / `weekly Monday` |
| Last known refresh | `2026-02-27 06:45` |
| Status | `fresh` / `stale` / `error` |
| Tags | `sales`, `orders` |

**How freshness is checked:**
- For databases: run a lightweight query like `SELECT MAX(updated_at) FROM table`
- For files (Excel/CSV): check file modified timestamp or row count delta
- For APIs: hit a health/metadata endpoint
- Each source gets a **probe** — a small, configurable script that returns a timestamp + row count

**API:**
- `GET /sources` — list all, with current status
- `POST /sources` — register a new source
- `GET /sources/{id}/history` — freshness history over time
- `POST /sources/{id}/probe` — trigger a manual freshness check

---

### 2. Report Inventory

Track every report that goes out.

**What gets stored per report:**
| Field | Example |
|---|---|
| Name | `Weekly Sales Dashboard` |
| Type | `Power BI` / `Excel` / `PDF` / `Email` |
| Owner | `rafael` |
| Recipients | `commercial-team` |
| Frequency | `weekly, Monday 09:00` |
| Sources used | `[SAP_Orders, SKU_Master, Plan_MP]` |
| Last sent/published | `2026-02-24 09:12` |
| Status | `current` / `stale sources` / `check failures` |

A report's status is **derived** — if any of its sources are stale or any of its checks are failing, the report is flagged.

**API:**
- `GET /reports` — list all, with derived status
- `POST /reports` — register a new report
- `GET /reports/{id}/lineage` — which sources feed this report

---

### 3. Lineage Map

A simple directed graph: **Source → Report**.

This isn't about column-level lineage (that's a rabbit hole). It's practical: "this report breaks if this source is stale." You define the edges manually when registering a report.

**Panel view:** A visual graph (using a library like D3 or Cytoscape.js) showing which sources feed which reports. Click a source to see all downstream reports. Click a report to see all upstream sources.

---

### 4. Checks Engine

Automated rules that validate data quality and consistency. This is the core of "do the numbers make sense."

**Check types:**

| Check | What it does | Example |
|---|---|---|
| **Freshness** | Source refreshed within expected window | `SAP_Orders` refreshed < 24h ago |
| **Row count** | Row count within expected range or delta | Orders table has 50k–200k rows |
| **Null rate** | Column null % below threshold | `oe_total_sales_price` < 1% nulls |
| **Range** | Values within expected bounds | `oe_discount_amount` between 0 and 10,000 |
| **Uniqueness** | Column has no unexpected duplicates | `id` column is unique |
| **Referential** | FK values exist in parent table | All `SKU` codes exist in `SKU_Master` |
| **Period-over-period** | Metric doesn't swing wildly vs last period | Total Sales CY vs LY within ±50% |
| **Cross-report** | Same metric matches across reports | "Total Revenue" in Report A = Report B |
| **Custom SQL/DAX** | Run any query, assert result | `SUM(quantity) > 0` |

**How checks are defined:**

```yaml
# checks/sales_orders.yml
source: SAP_Orders
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

Checks are YAML files — easy to version control, review in PRs, and extend.

**API:**
- `GET /checks` — all checks with last result (pass/fail/warn)
- `GET /checks/{id}/history` — result history
- `POST /checks/run` — trigger a full check run
- `POST /checks/run/{source_id}` — run checks for one source

---

### 5. Alerts

When a check fails or a source goes stale, notify.

**Channels (phased):**
1. **Phase 1:** Panel shows red/yellow/green badges — you check the panel
2. **Phase 2:** Email alerts (via SMTP) for critical failures
3. **Phase 3:** Slack/Teams webhook integration

**Alert rules:**
- Freshness overdue by >2h → warning (yellow)
- Freshness overdue by >6h → critical (red)
- Any check failure → critical
- Period-over-period anomaly → warning

---

### 6. Web Panel (Frontend)

Single-page app, no framework, no build step. Just HTML + JS served by FastAPI.

**Pages:**

#### Dashboard (home)
```
┌─────────────────────────────────────────────────┐
│  DATA GOVERNANCE PANEL            Last run: 5m  │
├───────────┬───────────┬───────────┬─────────────┤
│  Sources  │  Reports  │  Checks   │   Alerts    │
│   12 ✓    │    8 ✓    │  47/52 ✓  │   3 active  │
│    1 ⚠    │    1 ⚠    │   3 ⚠     │             │
│    0 ✗    │    0 ✗    │   2 ✗     │             │
├───────────┴───────────┴───────────┴─────────────┤
│  RECENT ALERTS                                  │
│  ⚠ SAP_Orders stale (last refresh 8h ago)       │
│  ✗ Weekly Sales: null rate 3.2% > threshold 1%  │
│  ⚠ Discount avg swung +62% vs last week         │
└─────────────────────────────────────────────────┘
```

#### Sources page
Table of all sources with status badges, last refresh time, owner. Click to expand → see history chart + downstream reports.

#### Reports page
Table of all reports with derived status. Click to expand → see upstream sources, checks, and lineage graph.

#### Checks page
Filterable table: all checks, grouped by source. Pass/fail/warn with timestamps. Click to see history.

#### Lineage page
Interactive graph of sources → reports.

---

## Data Model (SQLite)

```sql
-- Sources
CREATE TABLE sources (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    type        TEXT NOT NULL,           -- sql_server, excel, csv, api, sharepoint
    connection  TEXT,                    -- encrypted connection string / path
    owner       TEXT,
    refresh_schedule TEXT,               -- cron expression or human-readable
    tags        TEXT,                    -- comma-separated
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Source freshness history
CREATE TABLE source_probes (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER REFERENCES sources(id),
    probed_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_data_at DATETIME,              -- when the data was last updated
    row_count   INTEGER,
    status      TEXT,                   -- fresh, stale, error
    message     TEXT
);

-- Reports
CREATE TABLE reports (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    type        TEXT NOT NULL,           -- powerbi, excel, pdf, email
    owner       TEXT,
    recipients  TEXT,
    frequency   TEXT,
    last_published DATETIME,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Lineage (source → report edges)
CREATE TABLE lineage (
    source_id   INTEGER REFERENCES sources(id),
    report_id   INTEGER REFERENCES reports(id),
    PRIMARY KEY (source_id, report_id)
);

-- Check definitions
CREATE TABLE checks (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER REFERENCES sources(id),
    type        TEXT NOT NULL,
    config      TEXT NOT NULL,           -- JSON blob with check parameters
    severity    TEXT DEFAULT 'critical', -- critical, warning, info
    enabled     BOOLEAN DEFAULT 1
);

-- Check results
CREATE TABLE check_results (
    id          INTEGER PRIMARY KEY,
    check_id    INTEGER REFERENCES checks(id),
    ran_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    status      TEXT NOT NULL,           -- pass, fail, warn, error
    value       REAL,                   -- the measured value
    message     TEXT
);

-- Alerts
CREATE TABLE alerts (
    id          INTEGER PRIMARY KEY,
    check_result_id INTEGER REFERENCES check_results(id),
    source_id   INTEGER REFERENCES sources(id),
    severity    TEXT NOT NULL,
    message     TEXT NOT NULL,
    acknowledged BOOLEAN DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## Project Structure

```
data_governance/
├── plan.md
├── README.md
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── app/
│   ├── main.py              # FastAPI app, startup, scheduler
│   ├── config.py             # Settings, DB path, secrets
│   ├── database.py           # SQLite connection, migrations
│   ├── models.py             # Pydantic models (request/response)
│   ├── routers/
│   │   ├── sources.py        # /sources endpoints
│   │   ├── reports.py        # /reports endpoints
│   │   ├── checks.py         # /checks endpoints
│   │   ├── lineage.py        # /lineage endpoints
│   │   └── alerts.py         # /alerts endpoints
│   ├── checks/
│   │   ├── engine.py         # Runs checks, records results
│   │   ├── probes.py         # Source freshness probes
│   │   └── builtin.py        # Built-in check implementations
│   ├── scheduler.py          # APScheduler setup
│   └── static/
│       ├── index.html        # SPA shell
│       ├── app.js            # Frontend logic
│       └── style.css         # Styles (Pico CSS + overrides)
├── checks/                   # YAML check definitions
│   └── example.yml
└── tests/
    ├── test_sources.py
    ├── test_checks.py
    └── test_api.py
```

---

## Implementation Phases

### Phase 1 — Foundation (MVP)
**Goal:** Panel shows sources, reports, and basic freshness checks.

1. Set up FastAPI app with SQLite
2. Build Source and Report CRUD endpoints
3. Build lineage mapping endpoint
4. Implement freshness probes (file timestamp + DB query)
5. Build the web panel: dashboard, sources list, reports list
6. Add manual "run checks" trigger via API
7. Dockerize

**Deliverable:** You can open a browser, see all your sources and reports, and check if anything is stale.

### Phase 2 — Checks Engine
**Goal:** Automated data quality checks with pass/fail history.

1. Implement all check types (null rate, range, row count, uniqueness, referential, period-over-period)
2. YAML check definition loader
3. Check results history + trend charts in the panel
4. Scheduler: run checks every N hours automatically
5. Cross-report consistency checks

**Deliverable:** Automated checks run on schedule. Panel shows which checks pass/fail with history.

### Phase 3 — Alerts & Collaboration
**Goal:** Proactive notifications + multi-user features.

1. Email alerts (SMTP) for critical failures
2. Slack/Teams webhook integration
3. Alert acknowledgment in the panel
4. User accounts (simple auth — email + password or SSO)
5. Audit log (who changed what, when)

**Deliverable:** Team gets notified when something breaks. Multiple people can use the panel.

### Phase 4 — Advanced
**Goal:** Deeper insights and integrations.

1. Power BI REST API integration (auto-detect refresh status, dataset metadata)
2. Column-level lineage (optional — only if needed)
3. Anomaly detection with statistical methods (z-score, IQR) instead of just % thresholds
4. Scheduled report generation (PDF summary emailed weekly)
5. Lineage auto-discovery from SQL queries

---

## Key Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Database | SQLite | Zero config, single file, easy backup, sufficient for this scale |
| Frontend | No framework | No build step, fast to iterate, low maintenance |
| Check definitions | YAML files | Version-controllable, reviewable in PRs, easy to read |
| Lineage granularity | Source-to-report only | Column-level is complex and rarely needed at this stage |
| Scheduler | In-process (APScheduler) | No need for Celery/Redis complexity at this scale |
| Auth (Phase 3) | Simple token-based | Don't need OAuth/SSO until team grows |

---

## What This Does NOT Do

Being explicit about scope to avoid feature creep:

- **Not an ETL tool** — it doesn't move or transform data, it monitors it
- **Not a BI tool** — it doesn't create reports, it tracks them
- **Not a data catalog** — it's focused on operational health, not discovery
- **Not column-level lineage** — that's a Phase 4 nice-to-have at best
