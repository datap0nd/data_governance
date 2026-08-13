import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SQLITE_BUSY_TIMEOUT_MS = 60000

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
    name            TEXT,
    source_id       INTEGER REFERENCES sources(id),
    type            TEXT NOT NULL,
    config          TEXT NOT NULL,
    severity        TEXT DEFAULT 'critical',
    enabled         INTEGER DEFAULT 1,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
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
    fingerprint     TEXT,
    check_id        INTEGER REFERENCES checks(id),
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

CREATE TABLE IF NOT EXISTS email_schedules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_key    TEXT UNIQUE NOT NULL,
    person_id       INTEGER,
    content_types   TEXT DEFAULT 'tasks',
    enabled         INTEGER DEFAULT 0,
    recurrence      TEXT DEFAULT 'weekly',
    weekdays        TEXT DEFAULT 'monday',
    month_day       INTEGER,
    send_time       TEXT DEFAULT '09:00',
    recipients      TEXT,
    subject         TEXT,
    last_sent_at    DATETIME,
    next_run_at     DATETIME,
    last_error      TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pbi_recurrences (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    enabled             INTEGER DEFAULT 0,
    workspace_id        TEXT NOT NULL,
    workspace_name      TEXT,
    report_id           TEXT NOT NULL,
    report_name         TEXT NOT NULL,
    report_url          TEXT,
    embed_url           TEXT NOT NULL,
    dataset_id          TEXT,
    page_name           TEXT NOT NULL,
    page_display_name   TEXT,
    visual_name         TEXT NOT NULL,
    visual_title        TEXT,
    visual_type         TEXT,
    owner_name          TEXT,
    delivery_mode       TEXT DEFAULT 'subgroups',
    recipients          TEXT,
    group_column        TEXT NOT NULL,
    subject_template    TEXT NOT NULL,
    alert_message       TEXT,
    recurrence          TEXT DEFAULT 'weekly',
    weekdays            TEXT DEFAULT 'monday',
    month_day           INTEGER,
    send_time           TEXT DEFAULT '08:00',
    next_run_at         DATETIME,
    last_run_at         DATETIME,
    last_sent_at        DATETIME,
    last_status         TEXT,
    last_error          TEXT,
    last_row_count      INTEGER,
    created_by          TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pbi_recurrence_groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recurrence_id   INTEGER NOT NULL REFERENCES pbi_recurrences(id) ON DELETE CASCADE,
    match_value     TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    recipients      TEXT NOT NULL,
    enabled         INTEGER DEFAULT 1,
    UNIQUE(recurrence_id, match_value)
);

CREATE TABLE IF NOT EXISTS pbi_recurrence_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recurrence_id   INTEGER NOT NULL REFERENCES pbi_recurrences(id) ON DELETE CASCADE,
    position        INTEGER DEFAULT 0,
    column_name     TEXT NOT NULL,
    operator        TEXT NOT NULL,
    compare_value   TEXT
);

CREATE TABLE IF NOT EXISTS pbi_recurrence_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recurrence_id   INTEGER NOT NULL REFERENCES pbi_recurrences(id) ON DELETE CASCADE,
    trigger_type    TEXT NOT NULL,
    mode            TEXT NOT NULL,
    status          TEXT NOT NULL,
    started_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at     DATETIME,
    exported_rows   INTEGER DEFAULT 0,
    matched_rows    INTEGER DEFAULT 0,
    email_count     INTEGER DEFAULT 0,
    detail          TEXT,
    error           TEXT
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
CREATE INDEX IF NOT EXISTS idx_email_schedules_key ON email_schedules(schedule_key);
CREATE INDEX IF NOT EXISTS idx_email_schedules_next_run ON email_schedules(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_pbi_recurrences_next_run ON pbi_recurrences(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_pbi_recurrence_groups_parent ON pbi_recurrence_groups(recurrence_id);
CREATE INDEX IF NOT EXISTS idx_pbi_recurrence_rules_parent ON pbi_recurrence_rules(recurrence_id, position);
CREATE INDEX IF NOT EXISTS idx_pbi_recurrence_runs_parent ON pbi_recurrence_runs(recurrence_id, started_at);
CREATE INDEX IF NOT EXISTS idx_report_pages_report_id ON report_pages(report_id);
CREATE INDEX IF NOT EXISTS idx_report_visuals_page_id ON report_visuals(page_id);
CREATE INDEX IF NOT EXISTS idx_visual_fields_visual_id ON visual_fields(visual_id);
CREATE INDEX IF NOT EXISTS idx_report_measures_report_id ON report_measures(report_id);
CREATE INDEX IF NOT EXISTS idx_report_columns_report_id ON report_columns(report_id);

CREATE TABLE IF NOT EXISTS pbi_usage_days (
    date TEXT PRIMARY KEY,
    synced_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pbi_report_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_name TEXT NOT NULL,
    report_id INTEGER REFERENCES reports(id),
    view_date TEXT NOT NULL,
    view_count INTEGER DEFAULT 0,
    unique_users INTEGER DEFAULT 0,
    UNIQUE(report_name, view_date)
);

CREATE TABLE IF NOT EXISTS pbi_report_user_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_guid TEXT,
    report_name TEXT NOT NULL,
    report_id INTEGER REFERENCES reports(id),
    user_id TEXT NOT NULL,
    view_date TEXT NOT NULL,
    view_count INTEGER DEFAULT 0,
    UNIQUE(report_guid, report_name, user_id, view_date)
);

CREATE TABLE IF NOT EXISTS premium_viewers (
    email TEXT PRIMARY KEY,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pbi_sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type   TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    message     TEXT,
    details     TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flow_sites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL,
    adapter         TEXT NOT NULL DEFAULT 'web_export',
    base_url        TEXT,
    auth_url        TEXT,
    discovery_enabled INTEGER NOT NULL DEFAULT 0,
    discovery_interval_hours INTEGER NOT NULL DEFAULT 168,
    discovery_scope_json TEXT NOT NULL DEFAULT '["Mobile"]',
    discovery_weekday TEXT NOT NULL DEFAULT 'saturday',
    discovery_time TEXT NOT NULL DEFAULT '06:00',
    next_scan_at    DATETIME,
    last_scan_at    DATETIME,
    last_scan_status TEXT,
    last_scan_error TEXT,
    enabled         INTEGER DEFAULT 1,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flow_reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id             INTEGER NOT NULL REFERENCES flow_sites(id),
    name                TEXT NOT NULL,
    report_url          TEXT NOT NULL,
    ready_text          TEXT,
    open_export_text    TEXT,
    download_text       TEXT NOT NULL DEFAULT 'Download CSV',
    automation_json     TEXT NOT NULL DEFAULT '{}',
    notes               TEXT,
    discovery_key       TEXT,
    source_kind         TEXT NOT NULL DEFAULT 'manual',
    last_seen_at        DATETIME,
    stale               INTEGER NOT NULL DEFAULT 0,
    enabled             INTEGER DEFAULT 1,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(site_id, name)
);

CREATE TABLE IF NOT EXISTS flow_report_filters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id           INTEGER NOT NULL REFERENCES flow_reports(id),
    filter_key          TEXT NOT NULL,
    label               TEXT NOT NULL,
    control_label       TEXT NOT NULL,
    control_type        TEXT NOT NULL,
    options_json        TEXT NOT NULL DEFAULT '[]',
    automation_json     TEXT NOT NULL DEFAULT '{}',
    required            INTEGER DEFAULT 0,
    source_kind         TEXT NOT NULL DEFAULT 'manual',
    last_seen_at        DATETIME,
    stale               INTEGER NOT NULL DEFAULT 0,
    position            INTEGER DEFAULT 0,
    enabled             INTEGER DEFAULT 1,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_id, filter_key)
);

CREATE TABLE IF NOT EXISTS flows (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT UNIQUE NOT NULL,
    site_id             INTEGER NOT NULL REFERENCES flow_sites(id),
    report_id           INTEGER NOT NULL REFERENCES flow_reports(id),
    enabled             INTEGER DEFAULT 0,
    selections_json     TEXT NOT NULL DEFAULT '{}',
    download_mode       TEXT NOT NULL DEFAULT 'single',
    period_strategy     TEXT NOT NULL DEFAULT 'fixed',
    window_weeks        INTEGER,
    file_format         TEXT NOT NULL DEFAULT 'csv',
    browser_mode        TEXT NOT NULL DEFAULT 'headless',
    start_week          TEXT,
    end_week            TEXT,
    target_folder       TEXT NOT NULL,
    filename_template   TEXT NOT NULL,
    schedule_type       TEXT NOT NULL DEFAULT 'manual',
    schedule_time       TEXT,
    schedule_days       TEXT,
    next_run_at         DATETIME,
    last_run_at         DATETIME,
    last_status         TEXT,
    last_error          TEXT,
    sql_handoff_enabled INTEGER NOT NULL DEFAULT 0,
    sql_mode            TEXT,
    sql_database        TEXT,
    sql_schema          TEXT,
    sql_table           TEXT,
    created_by          TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flow_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    flow_id             INTEGER NOT NULL REFERENCES flows(id),
    trigger_type        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'queued',
    requested_by        TEXT,
    worker_id           TEXT,
    job_json            TEXT NOT NULL,
    progress_json       TEXT,
    artifact_json       TEXT,
    error               TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    claimed_at          DATETIME,
    started_at          DATETIME,
    finished_at         DATETIME,
    heartbeat_at        DATETIME
);

CREATE TABLE IF NOT EXISTS flow_run_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES flow_runs(id),
    period_key      TEXT,
    file_path       TEXT NOT NULL,
    filename        TEXT NOT NULL,
    file_size       INTEGER,
    checksum        TEXT,
    row_count       INTEGER,
    status          TEXT NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flow_run_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES flow_runs(id),
    status          TEXT NOT NULL,
    stage           TEXT,
    message         TEXT,
    details_json    TEXT NOT NULL DEFAULT '{}',
    error           TEXT,
    traceback       TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flow_operation_timings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type  TEXT NOT NULL,
    phase           TEXT NOT NULL,
    run_id          INTEGER REFERENCES flow_runs(id),
    scan_id         INTEGER REFERENCES flow_catalog_scans(id),
    site_id         INTEGER REFERENCES flow_sites(id),
    report_id       INTEGER REFERENCES flow_reports(id),
    duration_ms     INTEGER NOT NULL,
    item_count      INTEGER,
    status          TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    recorded_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flow_workers (
    worker_id       TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'offline',
    current_run_id  INTEGER REFERENCES flow_runs(id),
    current_scan_id INTEGER REFERENCES flow_catalog_scans(id),
    last_error      TEXT,
    last_seen_at    DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flow_catalog_scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id         INTEGER NOT NULL REFERENCES flow_sites(id),
    trigger_type    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    requested_by    TEXT,
    worker_id       TEXT,
    job_json        TEXT NOT NULL,
    progress_json   TEXT,
    result_json     TEXT,
    error           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    claimed_at      DATETIME,
    started_at      DATETIME,
    finished_at     DATETIME,
    heartbeat_at    DATETIME
);

CREATE TABLE IF NOT EXISTS flow_sql_catalog (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    database_name   TEXT NOT NULL,
    schema_name     TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    last_seen_at    DATETIME NOT NULL,
    stale           INTEGER NOT NULL DEFAULT 0,
    UNIQUE(database_name, schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS flow_sql_catalog_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    status          TEXT NOT NULL DEFAULT 'never_scanned',
    last_scan_at    DATETIME,
    duration_ms     INTEGER,
    target_count    INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_flow_reports_site ON flow_reports(site_id, enabled);
CREATE INDEX IF NOT EXISTS idx_flow_filters_report ON flow_report_filters(report_id, position);
CREATE INDEX IF NOT EXISTS idx_flows_schedule ON flows(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_flow_runs_queue ON flow_runs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_flow_runs_flow ON flow_runs(flow_id, created_at);
CREATE INDEX IF NOT EXISTS idx_flow_run_files_run ON flow_run_files(run_id);
CREATE INDEX IF NOT EXISTS idx_flow_run_events_run ON flow_run_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_flow_catalog_scans_queue ON flow_catalog_scans(status, created_at);
CREATE INDEX IF NOT EXISTS idx_flow_catalog_scans_site ON flow_catalog_scans(site_id, created_at);
CREATE INDEX IF NOT EXISTS idx_flow_sql_catalog_names ON flow_sql_catalog(database_name, schema_name, table_name, stale);
CREATE INDEX IF NOT EXISTS idx_flow_timings_operation ON flow_operation_timings(operation_type, phase, recorded_at);
CREATE INDEX IF NOT EXISTS idx_flow_timings_run ON flow_operation_timings(run_id);
CREATE INDEX IF NOT EXISTS idx_flow_timings_scan ON flow_operation_timings(scan_id);

CREATE INDEX IF NOT EXISTS idx_pbi_report_views_report_id ON pbi_report_views(report_id);
CREATE INDEX IF NOT EXISTS idx_pbi_report_views_date ON pbi_report_views(view_date);
CREATE INDEX IF NOT EXISTS idx_pbi_report_user_views_report_id ON pbi_report_user_views(report_id);
CREATE INDEX IF NOT EXISTS idx_pbi_report_user_views_date ON pbi_report_user_views(view_date);
CREATE INDEX IF NOT EXISTS idx_pbi_report_user_views_user ON pbi_report_user_views(user_id);
CREATE INDEX IF NOT EXISTS idx_pbi_sync_runs_type_status ON pbi_sync_runs(sync_type, status, finished_at);
"""


SCHEDULED_TASK_REBUILD_MIGRATIONS = [
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
]


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
    "ALTER TABLE sources ADD COLUMN freshness_rule_type TEXT",
    "ALTER TABLE sources ADD COLUMN freshness_schedule_days TEXT",
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
    "CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, role TEXT NOT NULL, email TEXT, include_all_alerts INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
    "ALTER TABLE people ADD COLUMN email TEXT",
    "ALTER TABLE people ADD COLUMN include_all_alerts INTEGER DEFAULT 0",
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
    *SCHEDULED_TASK_REBUILD_MIGRATIONS,
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
    # Email summary schedules
    """CREATE TABLE IF NOT EXISTS email_schedules (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        schedule_key    TEXT UNIQUE NOT NULL,
        person_id       INTEGER,
        content_types   TEXT DEFAULT 'tasks',
        enabled         INTEGER DEFAULT 0,
        recurrence      TEXT DEFAULT 'weekly',
        weekdays        TEXT DEFAULT 'monday',
        month_day       INTEGER,
        send_time       TEXT DEFAULT '09:00',
        recipients      TEXT,
        subject         TEXT,
        last_sent_at    DATETIME,
        next_run_at     DATETIME,
        last_error      TEXT,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    "ALTER TABLE email_schedules ADD COLUMN person_id INTEGER",
    "ALTER TABLE email_schedules ADD COLUMN content_types TEXT DEFAULT 'tasks'",
    "CREATE INDEX IF NOT EXISTS idx_email_schedules_key ON email_schedules(schedule_key)",
    "CREATE INDEX IF NOT EXISTS idx_email_schedules_next_run ON email_schedules(enabled, next_run_at)",
    "CREATE INDEX IF NOT EXISTS idx_email_schedules_person ON email_schedules(person_id)",
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
    "ALTER TABLE user_ips ADD COLUMN hostname TEXT",
    "ALTER TABLE user_ips ADD COLUMN client_key TEXT",
    "ALTER TABLE user_ips ADD COLUMN last_seen_at DATETIME",
    "ALTER TABLE user_ips ADD COLUMN updated_at DATETIME",
    """CREATE TABLE IF NOT EXISTS user_devices (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        client_key      TEXT NOT NULL UNIQUE,
        person_name     TEXT NOT NULL,
        last_ip_address TEXT,
        hostname        TEXT,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_seen_at    DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_user_devices_client_key ON user_devices(client_key)",
    # Power BI sync run tracking for stale-email safeguards
    """CREATE TABLE IF NOT EXISTS pbi_sync_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        sync_type   TEXT NOT NULL,
        status      TEXT NOT NULL,
        started_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        finished_at DATETIME,
        message     TEXT,
        details     TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pbi_sync_runs_type_status ON pbi_sync_runs(sync_type, status, finished_at)",
    # App settings used by scheduler controls
    """CREATE TABLE IF NOT EXISTS app_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    # Multi-user: actor tracking in event log
    "ALTER TABLE event_log ADD COLUMN actor TEXT",
    # Remove non-user diagnostic rows created by a reverted scheduler debug build.
    "DELETE FROM event_log WHERE entity_type = 'scheduler'",
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
    """CREATE TABLE IF NOT EXISTS pbi_report_user_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_guid TEXT,
    report_name TEXT NOT NULL,
    report_id INTEGER REFERENCES reports(id),
    user_id TEXT NOT NULL,
    view_date TEXT NOT NULL,
    view_count INTEGER DEFAULT 0,
    UNIQUE(report_guid, report_name, user_id, view_date)
)""",
    """CREATE TABLE IF NOT EXISTS premium_viewers (
    email TEXT PRIMARY KEY,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)""",
    "CREATE INDEX IF NOT EXISTS idx_pbi_report_user_views_report_id ON pbi_report_user_views(report_id)",
    "CREATE INDEX IF NOT EXISTS idx_pbi_report_user_views_date ON pbi_report_user_views(view_date)",
    "CREATE INDEX IF NOT EXISTS idx_pbi_report_user_views_user ON pbi_report_user_views(user_id)",
    # Allow actions to be about scripts or scheduled tasks, not just sources/reports
    "ALTER TABLE actions ADD COLUMN scheduled_task_id INTEGER REFERENCES scheduled_tasks(id)",
    "ALTER TABLE actions ADD COLUMN script_id INTEGER REFERENCES scripts(id)",
    "CREATE INDEX IF NOT EXISTS idx_actions_scheduled_task_id ON actions(scheduled_task_id)",
    "CREATE INDEX IF NOT EXISTS idx_actions_script_id ON actions(script_id)",
    # Power BI recurrence delivery without subgroup splitting
    "ALTER TABLE pbi_recurrences ADD COLUMN delivery_mode TEXT DEFAULT 'subgroups'",
    "ALTER TABLE pbi_recurrences ADD COLUMN recipients TEXT",
    # Recurrence ownership and failure notification routing
    "ALTER TABLE pbi_recurrences ADD COLUMN owner_name TEXT",
    """UPDATE pbi_recurrences
       SET owner_name = (
           SELECT r.owner
           FROM reports r
           WHERE lower(trim(r.name)) = lower(trim(pbi_recurrences.report_name))
           ORDER BY r.id
           LIMIT 1
       )
       WHERE owner_name IS NULL OR trim(owner_name) = ''""",
    # Recipient-facing context or action displayed above recurrence alert results
    "ALTER TABLE pbi_recurrences ADD COLUMN alert_message TEXT",
    # Persistent scanner findings and data-quality action linkage
    "ALTER TABLE actions ADD COLUMN fingerprint TEXT",
    "ALTER TABLE actions ADD COLUMN check_id INTEGER REFERENCES checks(id)",
    "CREATE INDEX IF NOT EXISTS idx_actions_fingerprint ON actions(fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_actions_check_id ON actions(check_id)",
    # Data-quality check metadata. The original tables existed without a
    # usable runner or management surface.
    "ALTER TABLE checks ADD COLUMN name TEXT",
    "ALTER TABLE checks ADD COLUMN created_at DATETIME",
    "ALTER TABLE checks ADD COLUMN updated_at DATETIME",
    "UPDATE checks SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP), updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)",
    # Complete probe accounting. Older runs remain valid with a zero default.
    "ALTER TABLE probe_runs ADD COLUMN no_rule INTEGER DEFAULT 0",
    # Website-specific report navigation stays local with the report catalog.
    "ALTER TABLE flow_reports ADD COLUMN automation_json TEXT NOT NULL DEFAULT '{}'",
    # Locally persisted ASAP catalog discovery. Scans only mark missing entries
    # stale; they never delete reports, filters, or saved flows.
    "ALTER TABLE flow_sites ADD COLUMN discovery_enabled INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE flow_sites ADD COLUMN discovery_interval_hours INTEGER NOT NULL DEFAULT 168",
    "ALTER TABLE flow_sites ADD COLUMN discovery_scope_json TEXT NOT NULL DEFAULT '[\"Mobile\"]'",
    "ALTER TABLE flow_sites ADD COLUMN discovery_weekday TEXT NOT NULL DEFAULT 'saturday'",
    "ALTER TABLE flow_sites ADD COLUMN discovery_time TEXT NOT NULL DEFAULT '06:00'",
    "ALTER TABLE flow_sites ADD COLUMN next_scan_at DATETIME",
    "ALTER TABLE flow_sites ADD COLUMN last_scan_at DATETIME",
    "ALTER TABLE flow_sites ADD COLUMN last_scan_status TEXT",
    "ALTER TABLE flow_sites ADD COLUMN last_scan_error TEXT",
    # Sites created before ASAP discovery existed used the generic adapter.
    # Upgrade only the canonical ASAP record (or its known portal host) so the
    # discovery endpoint works without asking users to edit an internal field.
    """UPDATE flow_sites SET adapter='asap_portal'
       WHERE adapter='web_export'
         AND (lower(trim(name))='asap'
              OR lower(COALESCE(auth_url, '')) LIKE '%asap.sec.samsung.net%'
              OR lower(COALESCE(base_url, '')) LIKE '%asap.sec.samsung.net%')""",
    "ALTER TABLE flow_reports ADD COLUMN discovery_key TEXT",
    "ALTER TABLE flow_reports ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'manual'",
    "ALTER TABLE flow_reports ADD COLUMN last_seen_at DATETIME",
    "ALTER TABLE flow_reports ADD COLUMN stale INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE flow_report_filters ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'manual'",
    "ALTER TABLE flow_report_filters ADD COLUMN last_seen_at DATETIME",
    "ALTER TABLE flow_report_filters ADD COLUMN stale INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE flow_workers ADD COLUMN current_scan_id INTEGER REFERENCES flow_catalog_scans(id)",
    """CREATE TABLE IF NOT EXISTS flow_catalog_scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id INTEGER NOT NULL REFERENCES flow_sites(id),
        trigger_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        requested_by TEXT,
        worker_id TEXT,
        job_json TEXT NOT NULL,
        progress_json TEXT,
        result_json TEXT,
        error TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        claimed_at DATETIME,
        started_at DATETIME,
        finished_at DATETIME,
        heartbeat_at DATETIME
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_flow_reports_discovery_key ON flow_reports(site_id, discovery_key) WHERE discovery_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_flow_catalog_scans_queue ON flow_catalog_scans(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_flow_catalog_scans_site ON flow_catalog_scans(site_id, created_at)",
    """CREATE TABLE IF NOT EXISTS flow_operation_timings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_type TEXT NOT NULL,
        phase TEXT NOT NULL,
        run_id INTEGER REFERENCES flow_runs(id),
        scan_id INTEGER REFERENCES flow_catalog_scans(id),
        site_id INTEGER REFERENCES flow_sites(id),
        report_id INTEGER REFERENCES flow_reports(id),
        duration_ms INTEGER NOT NULL,
        item_count INTEGER,
        status TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_flow_timings_operation ON flow_operation_timings(operation_type, phase, recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_flow_timings_run ON flow_operation_timings(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_flow_timings_scan ON flow_operation_timings(scan_id)",
    "ALTER TABLE flows ADD COLUMN browser_mode TEXT NOT NULL DEFAULT 'headless'",
    "ALTER TABLE flows ADD COLUMN period_strategy TEXT NOT NULL DEFAULT 'fixed'",
    "ALTER TABLE flows ADD COLUMN window_weeks INTEGER",
    "ALTER TABLE flows ADD COLUMN file_format TEXT NOT NULL DEFAULT 'csv'",
    "ALTER TABLE flows ADD COLUMN sql_mode TEXT",
    "ALTER TABLE flows ADD COLUMN sql_database TEXT",
    "ALTER TABLE flows ADD COLUMN sql_schema TEXT",
    "ALTER TABLE flows ADD COLUMN sql_table TEXT",
    """CREATE TABLE IF NOT EXISTS flow_run_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES flow_runs(id),
        status TEXT NOT NULL,
        stage TEXT,
        message TEXT,
        details_json TEXT NOT NULL DEFAULT '{}',
        error TEXT,
        traceback TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_flow_run_events_run ON flow_run_events(run_id, id)",
    "UPDATE flows SET file_format='csv', filename_template=replace(filename_template, '.xlsx', '.csv') WHERE file_format='xlsx' OR lower(filename_template) LIKE '%.xlsx'",
    """CREATE TABLE IF NOT EXISTS flow_sql_catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        database_name TEXT NOT NULL,
        schema_name TEXT NOT NULL,
        table_name TEXT NOT NULL,
        last_seen_at DATETIME NOT NULL,
        stale INTEGER NOT NULL DEFAULT 0,
        UNIQUE(database_name, schema_name, table_name)
    )""",
    """CREATE TABLE IF NOT EXISTS flow_sql_catalog_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        status TEXT NOT NULL DEFAULT 'never_scanned',
        last_scan_at DATETIME,
        duration_ms INTEGER,
        target_count INTEGER NOT NULL DEFAULT 0,
        error TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_flow_sql_catalog_names ON flow_sql_catalog(database_name, schema_name, table_name, stale)",
]


def _scheduled_tasks_has_host_unique_key(conn):
    """Return whether the one-time scheduled_tasks rebuild already ran."""
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scheduled_tasks'"
    ).fetchone()
    if not table:
        return False
    for index in conn.execute("PRAGMA index_list(scheduled_tasks)").fetchall():
        if not index[2]:
            continue
        columns = [
            row[2]
            for row in conn.execute(f"PRAGMA index_info('{index[1]}')").fetchall()
        ]
        if columns == ["task_name", "hostname"]:
            return True
    return False


def init_db():
    """Create all tables if they don't exist, then run migrations."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    scheduled_tasks_rebuild_complete = _scheduled_tasks_has_host_unique_key(conn)
    for migration in MIGRATIONS:
        if scheduled_tasks_rebuild_complete and migration in SCHEDULED_TASK_REBUILD_MIGRATIONS:
            continue
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
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
