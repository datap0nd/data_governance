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
    created_at: str | None = None
    updated_at: str | None = None


class SourceUpdate(BaseModel):
    owner: str | None = None
    refresh_schedule: str | None = None
    tags: str | None = None


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
    status: str | None = None  # derived from source statuses
    source_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class ReportUpdate(BaseModel):
    owner: str | None = None
    recipients: str | None = None
    frequency: str | None = None


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

class AlertOut(BaseModel):
    id: int
    source_id: int | None = None
    source_name: str | None = None
    severity: str
    message: str
    acknowledged: bool = False
    acknowledged_by: str | None = None
    created_at: str | None = None


# --- Dashboard ---

class DashboardStats(BaseModel):
    sources_total: int = 0
    sources_fresh: int = 0
    sources_stale: int = 0
    sources_error: int = 0
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
