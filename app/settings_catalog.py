"""The settings Metronome offers, grouped as the Settings page shows them.

Importing this module registers them. Add a setting here only together with
the code that reads it through ``settings_registry.get``.
"""
from __future__ import annotations

from app import config, flow_browser, flow_limits, flow_recording_timing, pipeline_insights_settings, settings as store
from app.settings_registry import GROUP_TESTS, Setting, file_value_writer, register, subscribe

# --- Connections -----------------------------------------------------------------
#
# These live in the protected settings file (.env), because worker processes
# read them too. Saving one applies it to the web service at once and to each
# worker on its next loop; no restart. Passwords are write-only.

PROBE = "Read-only probe database"
UPLOAD = "Flow upload database"
POWER_BI = "Power BI"
GROUP_TESTS[("connections", PROBE)] = "probe"
GROUP_TESTS[("connections", UPLOAD)] = "upload"


def _file(key, group, label, name, *, kind="text", default="", help="", placeholder="", **extra):
    return register(Setting(
        key=key, section="connections", group=group, label=label, kind=kind, default=default, help=help,
        placeholder=placeholder, optional=True, store="file", storage_key=name, **extra,
    ))


_file("conn.probe_host", PROBE, "Host", "PGHOST",
      help="Scanners use this account for SELECT queries only. It must not be able to write.")
_file("conn.probe_port", PROBE, "Port", "PGPORT", kind="int", default=5432, minimum=1, maximum=65535)
_file("conn.probe_database", PROBE, "Database", "PGDATABASE", default="postgres")
_file("conn.probe_user", PROBE, "User", "PGUSER")
_file("conn.probe_password", PROBE, "Password", "PGPASSWORD", kind="secret", default=None)

_file("conn.upload_host", UPLOAD, "Host", "DG_UPLOAD_PGHOST", placeholder="Same as the probe host",
      help="Where Flows write. A Flow with an owner signs in as that person's SQL user from Users; "
           "the shared account below is used only for Flows without an owner.")
_file("conn.upload_port", UPLOAD, "Port", "DG_UPLOAD_PGPORT", kind="int", default="", minimum=1, maximum=65535,
      placeholder="Same as probe")
_file("conn.upload_database", UPLOAD, "Default database", "DG_UPLOAD_PGDATABASE",
      placeholder="Same as the probe database")
_file("conn.upload_user", UPLOAD, "Shared user", "DG_UPLOAD_PGUSER")
_file("conn.upload_password", UPLOAD, "Shared password", "DG_UPLOAD_PGPASSWORD", kind="secret", default=None)

_file("conn.pbi_workspace", POWER_BI, "Workspace", "DG_PBI_WORKSPACE",
      help="The workspace whose refresh schedules and usage are synced.")
_file("conn.pbi_tenant", POWER_BI, "Tenant ID", "DG_PBI_TENANT_ID",
      help="Only for a service principal. Leave the next three empty to use the signed-in account.")
_file("conn.pbi_client_id", POWER_BI, "Service principal client ID", "DG_PBI_CLIENT_ID")
_file("conn.pbi_client_secret", POWER_BI, "Service principal secret", "DG_PBI_CLIENT_SECRET",
      kind="secret", default=None)
_file("conn.pbi_proxy", POWER_BI, "Proxy", "DG_PBI_PROXY", placeholder="Automatic (Windows proxy settings)",
      help="For example http://proxyhost:8080. A PAC script address is not valid here.")
_file("conn.github_token", "GitHub", "Access token for update checks", "DG_GITHUB_TOKEN", kind="secret",
      default=None, help="Optional. Avoids GitHub's request limit for anonymous checks.")

# --- Folders ---------------------------------------------------------------------

register(Setting(
    key="folders.reports", section="folders", group="Folders on the host PC", label="Reports folder",
    kind="path", default="", optional=True, store="file", applies="next_scan",
    help="The folder of .pbix files and TMDL exports the scanner reads.",
    # bio_path has the highest precedence of the names the scanner accepts, so
    # writing it makes this page's value the one in force.
    reader=lambda: config.REPORTS_PATH if config.REPORTS_PATH != "reports" else "",
    writer=file_value_writer("bio_path"),
))
register(Setting(
    key="folders.usage", section="folders", group="Folders on the host PC", label="Usage exports folder",
    kind="path", default="", optional=True, store="file", applies="next_scan",
    help="Optional. Power BI usage CSV exports.",
    reader=lambda: config.USAGE_FILES_PATH, writer=file_value_writer("usage_files_path"),
))
register(Setting(
    key="folders.python", section="folders", group="Folders on the host PC",
    label="Default Python for script Flows", kind="path", default="", optional=True, store="file",
    storage_key="DG_PYTHON_EXE", applies="new_runs", placeholder="The py launcher or python on PATH",
    help="A Flow can still choose its own interpreter.",
))

# --- Scanner -------------------------------------------------------------------


def _read_refresh_time() -> str:
    return store.get_overall_refresh_time()["time"]


def _write_refresh_time(value: str) -> None:
    hour, minute = value.split(":")
    store.set_overall_refresh_time(int(hour), int(minute))


register(Setting(
    key="scan.daily_time", section="scanner", group="Schedule", label="Daily refresh", kind="time",
    default=f"{store.DEFAULT_OVERALL_REFRESH_HOUR:02d}:{store.DEFAULT_OVERALL_REFRESH_MINUTE:02d}",
    help="Power BI sync, report scan, lineage and freshness probe.",
    reader=_read_refresh_time, writer=_write_refresh_time,
))


def _read_recipients() -> list[str]:
    from app.scanner import notifications

    return notifications.get_notification_settings()["recipients"]


def _write_recipients(value: list[str]) -> None:
    from app.scanner import notifications

    notifications.save_notification_settings(value)


register(Setting(
    key="scan.failure_recipients", section="scanner", group="When a scan has trouble",
    label="Email scan failures to", kind="list", default=[], item="email", max_items=50,
    help="Separate addresses with commas. Leave empty to send none.",
    reader=_read_recipients, writer=_write_recipients,
))


def _insights(field: str):
    def read():
        return getattr(pipeline_insights_settings.get_pipeline_insights_settings(), field)

    def write(value) -> None:
        current = pipeline_insights_settings.get_pipeline_insights_settings().public_dict()
        current[field] = value
        pipeline_insights_settings.save_pipeline_insights_settings(current)

    return read, write


_weekday_read, _weekday_write = _insights("weekday")
_insights_time_read, _insights_time_write = _insights("time")
_samples_read, _samples_write = _insights("samples_scheduled")
_explanations_read, _explanations_write = _insights("explanations_scheduled")

register(Setting(
    key="insights.weekday", section="scanner", group="Weekly pipeline insights", label="Day of the week",
    kind="choice", default="sunday", choices=pipeline_insights_settings.WEEKDAYS,
    choice_labels=tuple(day.capitalize() for day in pipeline_insights_settings.WEEKDAYS),
    help="When row samples and AI explanations for pipeline tables are refreshed.",
    reader=_weekday_read, writer=_weekday_write,
))
register(Setting(
    key="insights.time", section="scanner", group="Weekly pipeline insights", label="Time", kind="time",
    default="10:00", reader=_insights_time_read, writer=_insights_time_write,
))
register(Setting(
    key="insights.samples", section="scanner", group="Weekly pipeline insights", label="Refresh row samples",
    kind="bool", default=True, reader=_samples_read, writer=_samples_write,
))
register(Setting(
    key="insights.explanations", section="scanner", group="Weekly pipeline insights",
    label="Refresh AI explanations", kind="bool", default=True,
    reader=_explanations_read, writer=_explanations_write,
))

# --- Freshness and alerts --------------------------------------------------------

register(Setting(
    key="pipelines.scan_max_age_hours", section="freshness", group="Pipelines",
    label="Pipelines need a scan newer than", kind="int", default=48, minimum=24, maximum=168, unit="hours",
    help="A pipeline plan is refused when the last complete lineage scan is older than this.",
))

# --- Flows -----------------------------------------------------------------------

register(Setting(
    key="flows.browser", section="flows", group="Workers", label="Portal browser", kind="choice",
    default=flow_browser.DEFAULT, choices=("chrome", "msedge"), choice_labels=("Google Chrome", "Microsoft Edge"),
    storage_key=flow_browser.SETTING, applies="new_runs",
    help="Each browser keeps its own sign-in profile. Queued and running work keeps the browser it started with.",
))
register(Setting(
    key="flows.total_workers", section="flows", group="Workers", label="Total active workers", kind="int",
    default=flow_limits.DEFAULT_TOTAL_CAPACITY, minimum=1, maximum=flow_limits.MAX_SLOTS, unit="workers",
    storage_key="flows_total_capacity",
    help="Shared limit across background and visible work. Lowering it lets active work finish.",
))
register(Setting(
    key="flows.background_workers", section="flows", group="Workers", label="Background workers", kind="int",
    default=flow_limits.DEFAULT_HEADLESS_CAPACITY, minimum=1, maximum=flow_limits.MAX_SLOTS, unit="workers",
    storage_key="flows_headless_capacity",
    help="After raising this, run setup.ps1 on the host PC once to install the extra worker services.",
))
register(Setting(
    key="flows.visible_windows", section="flows", group="Workers", label="Visible browser windows", kind="int",
    default=flow_limits.DEFAULT_HEADED_CAPACITY, minimum=1, maximum=flow_limits.MAX_SLOTS, unit="windows",
    storage_key="flows_headed_capacity",
))
register(Setting(
    key="flows.recording_wait_seconds", section="flows", group="Workers",
    label="Wait before each recorded action", kind="int", default=flow_recording_timing.DEFAULT_SECONDS,
    minimum=1, maximum=flow_recording_timing.MAX_SECONDS, unit="seconds",
    storage_key=flow_recording_timing.SETTING, applies="new_runs",
))

# --- Email -----------------------------------------------------------------------

register(Setting(
    key="email.require_fresh_pbi", section="email", group="Scheduled alert emails",
    label="Hold an email until Power BI has synced", kind="bool", default=True,
    env=("DG_EMAIL_REQUIRE_FRESH_PBI",),
    help="Stops the alert summary going out with yesterday's refresh results when the sync is late.",
))
register(Setting(
    key="email.pbi_sync_max_age_hours", section="email", group="Scheduled alert emails",
    label="A sync counts as current for", kind="float", default=24.0, minimum=1, maximum=168, unit="hours",
    env=("DG_EMAIL_MAX_PBI_SYNC_AGE_HOURS",),
))
register(Setting(
    key="email.pbi_sync_grace_minutes", section="email", group="Scheduled alert emails",
    label="Wait after the daily refresh time", kind="float", default=30.0, minimum=0, maximum=240,
    unit="minutes", env=("DG_EMAIL_PBI_SYNC_GRACE_MINUTES",),
    help="Until this long after the daily refresh was due, yesterday's sync is still accepted.",
))
register(Setting(
    key="email.pbi_stale_retry_minutes", section="email", group="Scheduled alert emails",
    label="Try a held email again after", kind="float", default=30.0, minimum=5, maximum=240, unit="minutes",
    env=("DG_EMAIL_PBI_STALE_RETRY_MINUTES",),
))

# --- Updates, backups and logs ---------------------------------------------------

register(Setting(
    key="updates.auto", section="updates", group="Updates", label="Install new versions automatically",
    kind="bool", default=True, storage_key="automatic_main_updates_enabled",
    help="When off, new versions wait until Install is chosen on the Updates page.",
))
register(Setting(
    key="logs.keep_days", section="updates", group="Logs", label="Delete logs not written to for", kind="int",
    default=14, minimum=1, maximum=365, unit="days",
))
register(Setting(
    key="logs.max_total_mb", section="updates", group="Logs", label="Largest total size of the logs folder",
    kind="int", default=1024, minimum=50, maximum=102400, unit="MB",
    help="When the folder is over this, the oldest rotated logs are deleted first. A log a service is writing to is never deleted.",
))
register(Setting(
    key="logs.request_log", section="updates", group="Logs", label="Requests written to the log", kind="choice",
    default="changes", choices=("changes", "errors", "all"),
    choice_labels=("Failures and changes", "Failures only", "Every request"),
    help="Every request includes page refreshes and worker polling, and fills the log quickly. Use it while diagnosing.",
))


def _apply_request_log_mode() -> None:
    from app import logging_setup

    logging_setup.refresh_request_log_mode()


subscribe(("logs.request_log",), _apply_request_log_mode)


# The remaining declarations live beside the code that reads them.
from app import settings_catalog_flows, settings_catalog_scanner  # noqa: E402,F401
