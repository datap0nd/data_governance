from pydantic import BaseModel
from datetime import datetime


# --- Sources ---

class SourceOut(BaseModel):
    id: int
    name: str
    type: str
    connection_info: str | None = None
    source_query: str | None = None
    owner: str | None = None
    refresh_schedule: str | None = None
    tags: str | None = None
    discovered_by: str = "manual"
    status: str | None = None  # populated from latest probe
    last_updated: str | None = None
    report_count: int = 0
    custom_fresh_days: int | None = None
    upstream_id: int | None = None
    upstream_name: str | None = None
    upstream_refresh_day: str | None = None
    linked_scripts: str | None = None
    linked_task_count: int = 0
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class FreshnessRuleRequest(BaseModel):
    fresh_days: int


class SourceUpdate(BaseModel):
    owner: str | None = None
    refresh_schedule: str | None = None
    tags: str | None = None
    upstream_id: int | None = None


# --- Reports ---

class ReportOut(BaseModel):
    id: int
    name: str
    tmdl_path: str | None = None
    owner: str | None = None          # Report Owner (from TMDL)
    business_owner: str | None = None  # Business Owner (from TMDL)
    recipients: str | None = None
    frequency: str | None = None
    last_published: str | None = None
    powerbi_url: str | None = None
    status: str | None = None  # derived from source statuses
    source_count: int = 0
    worst_source_updated: str | None = None  # oldest source last_data_at
    unused_pct: int | None = None  # % of measures+columns not used in visuals
    pbi_dataset_id: str | None = None
    pbi_refresh_schedule: str | None = None
    pbi_last_refresh_at: str | None = None
    pbi_refresh_status: str | None = None
    pbi_refresh_error: str | None = None
    linked_task_count: int = 0
    views_30d: int | None = None
    unique_users_30d: int | None = None
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class ReportUpdate(BaseModel):
    owner: str | None = None
    recipients: str | None = None
    frequency: str | None = None
    business_owner: str | None = None


# --- Report Tables ---

class ReportTableOut(BaseModel):
    id: int
    report_id: int
    table_name: str
    source_id: int | None = None
    source_name: str | None = None
    source_expression: str | None = None
    last_scanned: str | None = None


# --- Lineage ---

class LineageEdge(BaseModel):
    source_id: int
    source_name: str
    source_type: str
    source_status: str = "unknown"
    report_id: int
    report_name: str


# --- Scanner ---

class ScanRunOut(BaseModel):
    id: int
    started_at: str | None = None
    finished_at: str | None = None
    reports_scanned: int | None = None
    sources_found: int | None = None
    new_sources: int | None = None
    changed_queries: int | None = None
    broken_refs: int | None = None
    status: str | None = None
    log: str | None = None


# --- Alerts ---

class AlertResolveRequest(BaseModel):
    status: str  # "acknowledged" or "resolved"
    reason: str | None = None


class AlertOut(BaseModel):
    id: int
    source_id: int | None = None
    source_name: str | None = None
    severity: str
    message: str
    acknowledged: bool = False
    acknowledged_by: str | None = None
    assigned_to: str | None = None
    resolution_status: str | None = None
    resolution_reason: str | None = None
    resolved_at: str | None = None
    created_at: str | None = None


# --- Actions ---

class ActionOut(BaseModel):
    id: int
    source_id: int | None = None
    source_name: str | None = None
    report_id: int | None = None
    report_name: str | None = None
    report_names: list[str] = []
    top_report_id: int | None = None
    top_report_name: str | None = None
    top_report_degradation_days: int = 0
    source_days_outdated: int = 0
    # Asset abstraction - an action is about either a source or a report
    asset_type: str | None = None  # "source", "report", ...
    asset_id: int | None = None
    asset_name: str | None = None
    # Days the asset has been in a problem state (covers both source
    # outdated days and report refresh-overdue days)
    asset_days: int = 0
    # Extra troubleshooting context for specific alert types. For
    # schedule_mismatch: list of sources that refreshed after the report,
    # each as {id, name, delta_hours} so the frontend can render them as
    # clickable links showing how far behind the report is.
    detail_items: list[dict] = []
    # Short actionable recommendation shown when the row is expanded.
    recommendation: str | None = None
    type: str  # stale_source, error_source, broken_ref, changed_query, refresh_failed, refresh_overdue, schedule_mismatch
    status: str = "open"  # open, acknowledged, investigating, expected, resolved
    assigned_to: str | None = None
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    resolved_at: str | None = None


class ActionUpdate(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    notes: str | None = None


# --- Create (manual entry) ---

class CreateSourceRequest(BaseModel):
    name: str
    type: str
    connection_info: str | None = None
    source_query: str | None = None
    owner: str | None = None
    refresh_schedule: str | None = None
    tags: str | None = None
    upstream_id: int | None = None
    report_ids: list[int] | None = None


class CreateReportRequest(BaseModel):
    name: str
    owner: str | None = None
    business_owner: str | None = None
    frequency: str | None = None
    powerbi_url: str | None = None


class CreateUpstreamRequest(BaseModel):
    name: str
    code: str
    refresh_day: str | None = None


class CustomEntryOut(BaseModel):
    id: int
    entity_type: str
    name: str
    detail: str | None = None
    created_at: str | None = None


# --- Dashboard ---

class DashboardStats(BaseModel):
    sources_total: int = 0
    sources_fresh: int = 0
    sources_stale: int = 0  # kept for API compat, always 0
    sources_outdated: int = 0
    sources_unknown: int = 0
    reports_total: int = 0
    reports_ok: int = 0
    reports_warning: int = 0
    reports_error: int = 0
    checks_total: int = 0
    checks_pass: int = 0
    checks_warn: int = 0
    checks_fail: int = 0
    alerts_active: int = 0
    last_scan: ScanRunOut | None = None


# --- Tasks ---

class TaskLinkInfo(BaseModel):
    entity_type: str
    entity_id: int
    entity_name: str | None = None


class TaskLinkRequest(BaseModel):
    entity_type: str
    entity_id: int


class TaskOut(BaseModel):
    id: int
    title: str
    description: str | None = None
    status: str = "backlog"
    priority: str = "medium"
    assigned_to: str | None = None
    due_date: str | None = None
    position: int = 0
    email_owner: bool = False
    linked_entities: list[TaskLinkInfo] = []
    created_at: str | None = None
    updated_at: str | None = None


class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    status: str = "backlog"
    priority: str = "medium"
    assigned_to: str | None = None
    due_date: str | None = None
    email_owner: bool = False
    linked_entities: list[TaskLinkRequest] = []


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assigned_to: str | None = None
    due_date: str | None = None
    email_owner: bool | None = None
    linked_entities: list[TaskLinkRequest] | None = None


class TaskMove(BaseModel):
    status: str
    position: int = 0


# --- People ---

class PersonOut(BaseModel):
    id: int
    name: str
    role: str
    created_at: str | None = None

class PersonCreate(BaseModel):
    name: str
    role: str


# --- Scripts ---

class ScriptOut(BaseModel):
    id: int
    path: str
    display_name: str
    owner: str | None = None
    last_modified: str | None = None
    last_scanned: str | None = None
    file_size: int | None = None
    tables_read: list[str] = []
    tables_written: list[str] = []
    hostname: str | None = None
    machine_alias: str | None = None
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class ScriptUpdate(BaseModel):
    owner: str | None = None


# --- Power Automate Flows ---

class PowerAutomateFlowOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    owner: str | None = None
    schedule: str | None = None
    source_url: str | None = None
    output_source_id: int | None = None
    output_source_name: str | None = None
    output_description: str | None = None
    status: str | None = "active"
    account: str | None = None
    last_run_time: str | None = None
    notes: str | None = None
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class PowerAutomateFlowCreate(BaseModel):
    name: str
    description: str | None = None
    owner: str | None = None
    schedule: str | None = None
    source_url: str | None = None
    output_source_id: int | None = None
    output_description: str | None = None
    status: str = "active"
    account: str | None = None
    last_run_time: str | None = None
    notes: str | None = None


class PowerAutomateFlowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    owner: str | None = None
    schedule: str | None = None
    source_url: str | None = None
    output_source_id: int | None = None
    output_description: str | None = None
    status: str | None = None
    account: str | None = None
    last_run_time: str | None = None
    notes: str | None = None


class ScriptTableOut(BaseModel):
    id: int
    script_id: int
    table_name: str
    direction: str
    source_id: int | None = None
    source_name: str | None = None


# --- Custom Reports ---

class CustomReportOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    frequency: str | None = None
    owner: str | None = None
    stakeholders: str | None = None
    steps: str | None = None
    data_sources: str | None = None
    output_description: str | None = None
    estimated_hours: float | None = None
    status: str | None = "active"
    last_completed: str | None = None
    tags: str | None = None
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class CustomReportCreate(BaseModel):
    name: str
    description: str | None = None
    frequency: str | None = None
    owner: str | None = None
    stakeholders: str | None = None
    steps: str | None = None
    data_sources: str | None = None
    output_description: str | None = None
    estimated_hours: float | None = None
    status: str = "active"
    last_completed: str | None = None
    tags: str | None = None


class CustomReportUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    frequency: str | None = None
    owner: str | None = None
    stakeholders: str | None = None
    steps: str | None = None
    data_sources: str | None = None
    output_description: str | None = None
    estimated_hours: float | None = None
    status: str | None = None
    last_completed: str | None = None
    tags: str | None = None


# --- Documentation ---

class DocEntityLinkInfo(BaseModel):
    entity_type: str
    entity_id: int
    entity_name: str | None = None


class DocEntityLinkRequest(BaseModel):
    entity_type: str
    entity_id: int


class DocumentationOut(BaseModel):
    id: int
    report_id: int | None = None
    report_name: str | None = None
    title: str
    business_purpose: str | None = None
    business_audience: str | None = None
    business_cadence: str | None = None
    technical_lineage_mermaid: str | None = None
    technical_sources: str | None = None
    technical_transformations: str | None = None
    technical_known_issues: str | None = None
    information_tab: str | None = None
    status: str | None = "draft"
    created_by: str | None = None
    linked_entities: list[DocEntityLinkInfo] = []
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class DocumentationCreate(BaseModel):
    report_id: int | None = None
    title: str
    business_purpose: str | None = None
    business_audience: str | None = None
    business_cadence: str | None = None
    technical_lineage_mermaid: str | None = None
    technical_sources: str | None = None
    technical_transformations: str | None = None
    technical_known_issues: str | None = None
    information_tab: str | None = None
    status: str = "draft"
    linked_entities: list[DocEntityLinkRequest] = []


class DocumentationUpdate(BaseModel):
    report_id: int | None = None
    title: str | None = None
    business_purpose: str | None = None
    business_audience: str | None = None
    business_cadence: str | None = None
    technical_lineage_mermaid: str | None = None
    technical_sources: str | None = None
    technical_transformations: str | None = None
    technical_known_issues: str | None = None
    information_tab: str | None = None
    status: str | None = None
    linked_entities: list[DocEntityLinkRequest] | None = None


# --- Event Log ---

class EventLogOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int | None = None
    entity_name: str | None = None
    action: str
    detail: str | None = None
    actor: str | None = None
    created_at: str | None = None


# --- Scheduled Tasks (Windows Task Scheduler) ---

class ScheduledTaskOut(BaseModel):
    id: int
    task_name: str
    task_path: str
    status: str | None = None
    last_run_time: str | None = None
    last_result: str | None = None
    next_run_time: str | None = None
    author: str | None = None
    run_as_user: str | None = None
    action_command: str | None = None
    action_args: str | None = None
    schedule_type: str | None = None
    enabled: bool = True
    script_id: int | None = None
    script_name: str | None = None
    hostname: str | None = None
    machine_alias: str | None = None
    archived: bool = False
    last_scanned: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
