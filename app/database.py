import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,
    type            TEXT NOT NULL,
    connection_info TEXT,
    source_query    TEXT,
    owner           TEXT,
    refresh_schedule TEXT,
    tags            TEXT,
    discovered_by   TEXT DEFAULT 'manual',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_probes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER REFERENCES sources(id),
    probed_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_data_at    DATETIME,
    row_count       INTEGER,
    status          TEXT,
    message         TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,
    tmdl_path       TEXT,
    owner           TEXT,
    business_owner  TEXT,
    recipients      TEXT,
    frequency       TEXT,
    last_published  DATETIME,
    powerbi_url     TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS report_tables (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER REFERENCES reports(id),
    table_name      TEXT NOT NULL,
    source_id       INTEGER REFERENCES sources(id),
    source_expression TEXT,
    last_scanned    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_id, table_name)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at     DATETIME,
    reports_scanned INTEGER,
    sources_found   INTEGER,
    new_sources     INTEGER,
    changed_queries INTEGER,
    broken_refs     INTEGER,
    status          TEXT,
    log             TEXT
);

CREATE TABLE IF NOT EXISTS probe_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at     DATETIME,
    sources_probed  INTEGER,
    fresh           INTEGER DEFAULT 0,
    stale           INTEGER DEFAULT 0,
    outdated        INTEGER DEFAULT 0,
    unknown         INTEGER DEFAULT 0,
    status          TEXT,
    log             TEXT
);

CREATE TABLE IF NOT EXISTS checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER REFERENCES sources(id),
    type            TEXT NOT NULL,
    config          TEXT NOT NULL,
    severity        TEXT DEFAULT 'critical',
    enabled         INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS check_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id        INTEGER REFERENCES checks(id),
    ran_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    status          TEXT NOT NULL,
    value           REAL,
    message         TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    check_result_id     INTEGER REFERENCES check_results(id),
    source_id           INTEGER REFERENCES sources(id),
    scan_run_id         INTEGER REFERENCES scan_runs(id),
    severity            TEXT NOT NULL,
    message             TEXT NOT NULL,
    acknowledged        INTEGER DEFAULT 0,
    acknowledged_by     TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER REFERENCES sources(id),
    report_id       INTEGER REFERENCES reports(id),
    type            TEXT NOT NULL,
    status          TEXT DEFAULT 'open',
    assigned_to     TEXT,
    notes           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    resolved_at     DATETIME
);

CREATE TABLE IF NOT EXISTS upstream_systems (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,
    code            TEXT NOT NULL,
    refresh_day     TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS report_pages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER REFERENCES reports(id),
    page_name       TEXT NOT NULL,
    page_ordinal    INTEGER DEFAULT 0,
    last_scanned    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_id, page_name)
);

CREATE TABLE IF NOT EXISTS report_visuals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id         INTEGER REFERENCES report_pages(id),
    visual_id       TEXT NOT NULL,
    visual_type     TEXT,
    title           TEXT,
    last_scanned    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(page_id, visual_id)
);

CREATE TABLE IF NOT EXISTS visual_fields (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    visual_id       INTEGER REFERENCES report_visuals(id),
    table_name      TEXT NOT NULL,
    field_name      TEXT NOT NULL,
    UNIQUE(visual_id, table_name, field_name)
);

CREATE TABLE IF NOT EXISTS report_measures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER REFERENCES reports(id),
    table_name      TEXT NOT NULL,
    measure_name    TEXT NOT NULL,
    measure_dax     TEXT,
    UNIQUE(report_id, table_name, measure_name)
);

CREATE TABLE IF NOT EXISTS report_columns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER REFERENCES reports(id),
    table_name      TEXT NOT NULL,
    column_name     TEXT NOT NULL,
    UNIQUE(report_id, table_name, column_name)
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT,
    status      TEXT DEFAULT 'backlog',
    priority    TEXT DEFAULT 'medium',
    assigned_to TEXT,
    due_date    TEXT,
    position    INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id   INTEGER,
    entity_name TEXT,
    action      TEXT NOT NULL,
    detail      TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scripts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    path            TEXT UNIQUE NOT NULL,
    display_name    TEXT NOT NULL,
    owner           TEXT,
    last_modified   DATETIME,
    last_scanned    DATETIME,
    file_size       INTEGER,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS script_tables (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id       INTEGER NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
    table_name      TEXT NOT NULL,
    direction       TEXT NOT NULL,
    source_id       INTEGER REFERENCES sources(id),
    UNIQUE(script_id, table_name, direction)
);

CREATE INDEX IF NOT EXISTS idx_script_tables_script_id ON script_tables(script_id);
CREATE INDEX IF NOT EXISTS idx_script_tables_source_id ON script_tables(source_id);
CREATE INDEX IF NOT EXISTS idx_scripts_path ON scripts(path);

CREATE VIEW IF NOT EXISTS lineage AS
    SELECT DISTINCT source_id, report_id
    FROM report_tables
    WHERE source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_source_probes_source_id ON source_probes(source_id);
CREATE INDEX IF NOT EXISTS idx_report_tables_source_id ON report_tables(source_id);
CREATE INDEX IF NOT EXISTS idx_report_tables_report_id ON report_tables(report_id);
CREATE INDEX IF NOT EXISTS idx_alerts_source_id ON alerts(source_id);
CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON alerts(acknowledged);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
CREATE INDEX IF NOT EXISTS idx_event_log_created_at ON event_log(created_at);
CREATE INDEX IF NOT EXISTS idx_actions_source_id ON actions(source_id);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);
CREATE INDEX IF NOT EXISTS idx_report_pages_report_id ON report_pages(report_id);
CREATE INDEX IF NOT EXISTS idx_report_visuals_page_id ON report_visuals(page_id);
CREATE INDEX IF NOT EXISTS idx_visual_fields_visual_id ON visual_fields(visual_id);
CREATE INDEX IF NOT EXISTS idx_report_measures_report_id ON report_measures(report_id);
CREATE INDEX IF NOT EXISTS idx_report_columns_report_id ON report_columns(report_id);
"""


MIGRATIONS = [
    "ALTER TABLE reports ADD COLUMN business_owner TEXT",
    "ALTER TABLE reports ADD COLUMN powerbi_url TEXT",
    # Alert resolution workflow
    "ALTER TABLE alerts ADD COLUMN resolution_status TEXT",
    "ALTER TABLE alerts ADD COLUMN resolution_reason TEXT",
    "ALTER TABLE alerts ADD COLUMN resolved_at DATETIME",
    # Per-source custom freshness thresholds
    "ALTER TABLE sources ADD COLUMN custom_fresh_days INTEGER",
    "ALTER TABLE sources ADD COLUMN custom_stale_days INTEGER",
    # Upstream system linkage
    "ALTER TABLE sources ADD COLUMN upstream_id INTEGER REFERENCES upstream_systems(id)",
    # Alert owner assignment
    "ALTER TABLE alerts ADD COLUMN assigned_to TEXT",
    # Manual entry tracking
    "ALTER TABLE reports ADD COLUMN discovered_by TEXT DEFAULT 'scanned'",
    "ALTER TABLE upstream_systems ADD COLUMN discovered_by TEXT DEFAULT 'scanned'",
    # Task email-owner flag
    "ALTER TABLE tasks ADD COLUMN email_owner INTEGER DEFAULT 0",
    # People table
    "CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, role TEXT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
    # Scheduled tasks (Windows Task Scheduler)
    """CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        task_name       TEXT UNIQUE NOT NULL,
        task_path       TEXT NOT NULL,
        status          TEXT,
        last_run_time   DATETIME,
        last_result     TEXT,
        next_run_time   DATETIME,
        author          TEXT,
        run_as_user     TEXT,
        action_command  TEXT,
        action_args     TEXT,
        schedule_type   TEXT,
        enabled         INTEGER DEFAULT 1,
        script_id       INTEGER REFERENCES scripts(id),
        last_scanned    DATETIME,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_script_id ON scheduled_tasks(script_id)",
    # Power BI refresh sync
    "ALTER TABLE reports ADD COLUMN pbi_dataset_id TEXT",
    "ALTER TABLE reports ADD COLUMN pbi_refresh_schedule TEXT",
    "ALTER TABLE reports ADD COLUMN pbi_last_refresh_at TEXT",
    "ALTER TABLE reports ADD COLUMN pbi_refresh_status TEXT",
    "ALTER TABLE reports ADD COLUMN pbi_refresh_error TEXT",
    # Archive support
    "ALTER TABLE sources ADD COLUMN archived INTEGER DEFAULT 0",
    "ALTER TABLE reports ADD COLUMN archived INTEGER DEFAULT 0",
    "ALTER TABLE scripts ADD COLUMN archived INTEGER DEFAULT 0",
    "ALTER TABLE upstream_systems ADD COLUMN archived INTEGER DEFAULT 0",
    "ALTER TABLE scheduled_tasks ADD COLUMN archived INTEGER DEFAULT 0",
    # Normalize source types: csv/folder -> excel
    "UPDATE sources SET type = 'excel' WHERE type IN ('csv', 'folder')",
    # Clear upstream_id (reset for manual population)
    "UPDATE sources SET upstream_id = NULL WHERE upstream_id IS NOT NULL",
    # Machine tracking for scheduled tasks - add columns and rebuild table to drop UNIQUE on task_name
    "ALTER TABLE scheduled_tasks ADD COLUMN hostname TEXT",
    "ALTER TABLE scheduled_tasks ADD COLUMN machine_alias TEXT",
    # Rebuild scheduled_tasks to replace UNIQUE(task_name) with UNIQUE(task_name, hostname)
    """CREATE TABLE IF NOT EXISTS scheduled_tasks_new (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        task_name       TEXT NOT NULL,
        task_path       TEXT NOT NULL,
        status          TEXT,
        last_run_time   DATETIME,
        last_result     TEXT,
        next_run_time   DATETIME,
        author          TEXT,
        run_as_user     TEXT,
        action_command  TEXT,
        action_args     TEXT,
        schedule_type   TEXT,
        enabled         INTEGER DEFAULT 1,
        script_id       INTEGER REFERENCES scripts(id),
        hostname        TEXT,
        machine_alias   TEXT,
        archived        INTEGER DEFAULT 0,
        last_scanned    DATETIME,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(task_name, hostname)
    )""",
    """INSERT OR IGNORE INTO scheduled_tasks_new
        (id, task_name, task_path, status, last_run_time, last_result, next_run_time,
         author, run_as_user, action_command, action_args, schedule_type, enabled,
         script_id, hostname, machine_alias, archived, last_scanned, created_at, updated_at)
        SELECT id, task_name, task_path, status, last_run_time, last_result, next_run_time,
               author, run_as_user, action_command, action_args, schedule_type, enabled,
               script_id, hostname, machine_alias, archived, last_scanned, created_at, updated_at
        FROM scheduled_tasks""",
    "DROP TABLE scheduled_tasks",
    "ALTER TABLE scheduled_tasks_new RENAME TO scheduled_tasks",
    "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_script_id ON scheduled_tasks(script_id)",
    # Machine tracking for scripts
    "ALTER TABLE scripts ADD COLUMN hostname TEXT",
    "ALTER TABLE scripts ADD COLUMN machine_alias TEXT",
    # Task-entity linking
    """CREATE TABLE IF NOT EXISTS task_links (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        entity_type TEXT NOT NULL,
        entity_id   INTEGER NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(task_id, entity_type, entity_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_task_links_task_id ON task_links(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_task_links_entity ON task_links(entity_type, entity_id)",
    # Source-to-source dependencies (MV -> upstream tables)
    """CREATE TABLE IF NOT EXISTS source_dependencies (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id       INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        depends_on_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        discovered_by   TEXT DEFAULT 'pg_matviews',
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source_id, depends_on_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_source_deps_source ON source_dependencies(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_source_deps_depends ON source_dependencies(depends_on_id)",
    # Multi-user: IP-to-user mapping
    """CREATE TABLE IF NOT EXISTS user_ips (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ip_address  TEXT NOT NULL UNIQUE,
        person_name TEXT NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Multi-user: actor tracking in event log
    "ALTER TABLE event_log ADD COLUMN actor TEXT",
    # Power Automate flows (manual entry)
    """CREATE TABLE IF NOT EXISTS power_automate_flows (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        name                TEXT UNIQUE NOT NULL,
        description         TEXT,
        owner               TEXT,
        schedule            TEXT,
        source_url          TEXT,
        output_source_id    INTEGER REFERENCES sources(id),
        output_description  TEXT,
        status              TEXT DEFAULT 'active',
        account             TEXT,
        last_run_time       DATETIME,
        notes               TEXT,
        archived            INTEGER DEFAULT 0,
        created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pa_flows_status ON power_automate_flows(status)",
    # Custom Reports (recurring tasks with stakeholders/documentation)
    """CREATE TABLE IF NOT EXISTS custom_reports (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        name                TEXT UNIQUE NOT NULL,
        description         TEXT,
        frequency           TEXT,
        owner               TEXT,
        stakeholders        TEXT,
        steps               TEXT,
        data_sources        TEXT,
        output_description  TEXT,
        estimated_hours     REAL,
        status              TEXT DEFAULT 'active',
        last_completed      DATETIME,
        tags                TEXT,
        archived            INTEGER DEFAULT 0,
        created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Documentation pages (pipeline docs with entity linking)
    """CREATE TABLE IF NOT EXISTS documentation (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id       INTEGER REFERENCES reports(id),
        title           TEXT UNIQUE NOT NULL,
        business_purpose TEXT,
        business_audience TEXT,
        business_cadence TEXT,
        technical_lineage_mermaid TEXT,
        technical_sources TEXT,
        technical_transformations TEXT,
        technical_known_issues TEXT,
        information_tab TEXT,
        status          TEXT DEFAULT 'draft',
        created_by      TEXT,
        archived        INTEGER DEFAULT 0,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS doc_entity_links (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id      INTEGER NOT NULL REFERENCES documentation(id) ON DELETE CASCADE,
        entity_type TEXT NOT NULL,
        entity_id   INTEGER NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(doc_id, entity_type, entity_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_doc_entity_links_doc_id ON doc_entity_links(doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_doc_entity_links_entity ON doc_entity_links(entity_type, entity_id)",
    # PBI usage / view count tracking
    """CREATE TABLE IF NOT EXISTS pbi_usage_days (
    date TEXT PRIMARY KEY,
    synced_at DATETIME DEFAULT CURRENT_TIMESTAMP
)""",
    """CREATE TABLE IF NOT EXISTS pbi_report_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_name TEXT NOT NULL,
    report_id INTEGER REFERENCES reports(id),
    view_date TEXT NOT NULL,
    view_count INTEGER DEFAULT 0,
    unique_users INTEGER DEFAULT 0,
    UNIQUE(report_name, view_date)
)""",
    """CREATE INDEX IF NOT EXISTS idx_pbi_report_views_report_id ON pbi_report_views(report_id)""",
    """CREATE INDEX IF NOT EXISTS idx_pbi_report_views_date ON pbi_report_views(view_date)""",
]


def init_db():
    """Create all tables if they don't exist, then run migrations."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    for migration in MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                pass  # column already exists
            else:
                raise
    conn.commit()
    conn.close()


@contextmanager
def get_db():
    """Yield a database connection with row_factory set for dict-like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
