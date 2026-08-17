# AGENTS.md

## Cursor Cloud specific instructions

Metronome (a.k.a. the "Data Governance Panel") is a single FastAPI + SQLite web
app. It parses Power BI report exports (`.pbix` or TMDL), tracks data sources and
lineage, and layers on optional automation (Power BI sync, scheduled emails,
report-download flows, PostgreSQL probing). The whole product is one Python
service; there is no separate frontend build — `app/static/` is served directly.

### Environment / dependencies

- Python 3.12 with a virtualenv at `.venv` (created by the startup update
  script). System package `python3.12-venv` is required for `python3 -m venv`
  and is already provisioned in the base VM; it is not reinstalled on each boot.
- Install target is `requirements-local.txt` (it includes `requirements.txt`
  plus native pbix-parsing extras). `pbixray` resolves to a Linux-compatible
  build here even though `vendor/` only ships Windows `xpress9` wheels.
- `pytest` is a dev-only tool and is intentionally not in the requirements
  files; the update script installs it separately.

### Run the app (dev)

Standard command, run from the repo root:

```
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Useful env vars for local dev (all optional):
- `DG_TMDL_ROOT` — folder the scanner reads reports from. Point it at the
  committed sample data `/workspace/test_reports` to get 5 reports / 13 sources
  without any real Power BI export.
- `DG_DB_PATH` — SQLite file path (defaults to `governance.db` in repo root;
  gitignored).
- `DG_AI_MOCK=true` — use the mock AI provider so no external LLM endpoint is
  needed.

The app is served at `http://localhost:8000`. First load prompts for a name
(identity is keyed to client IP), then Scanner > "Run Scan Now" populates the
Dashboard, Reports, Sources, and Pipelines pages.

### Non-obvious runtime notes

- Most integrations are Windows-only or need external credentials and fail
  **soft**, not hard: on Linux, Power BI sync reports "only available on
  Windows" and PostgreSQL probing reports "no credentials", while the core
  TMDL/pbix scan still completes. This is expected — the app is designed to run
  degraded without those services.
- Scan access (`/api/scanner/run`, refresh schedule, etc.) is gated to the
  server machine via `require_app_access`; requests from `localhost` count as
  local, so scanning works in-VM.
- A background `apscheduler` starts on app startup (daily backup, per-minute
  email/recurrence/flow dispatch). These no-op without configured integrations
  but will emit scheduler log lines.

### Tests / lint

- Python tests: `.venv/bin/python -m pytest -q` (currently 280 passing). There
  is no pytest config file; tests are plain files under `tests/` and create
  their own temp SQLite DBs.
- Frontend static checks: `node --check app/static/app.js` and the two Node
  suites `node tests/test_lineage_display.mjs` and
  `node tests/test_lineage_layers.mjs`.
- `.venv/bin/python -m compileall -q app` is a quick byte-compile sanity check.
