"""Scanner, freshness and update settings read by the web service.

Imported by ``app.settings_catalog``. Declare a setting here only together
with the code that reads it through ``settings_registry.get``.

Each default is the constant of the module that uses the value, so the page
and the code show the same number. Those ``app/main.py`` owns are written out
here instead, because this module cannot import it without a cycle;
``tests/test_settings_scanner_schedules.py`` holds each of them to its
constant.

The code reads these through ``app.scanner.limits``, which asks the registry
at the moment of use.
"""
from __future__ import annotations

from app import freshness
from app.scanner import jobs, pbi_fetch, pbi_sync, pbix_parser, prober, runner, walker
from app.settings_registry import Setting, register, subscribe

# --- Scanner ---------------------------------------------------------------------

SCHEDULE = "Schedule"
READS = "What a scan reads"
TROUBLE = "When a scan has trouble"
POSTGRES = "Read-only PostgreSQL checks"
POWER_BI = "Power BI"

register(Setting(
    key="scan.late_start_hours", section="scanner", group=SCHEDULE,
    # Under a day, so one day's refresh is never still waiting when the next is due.
    label="Start a late daily refresh within", kind="int", default=6, minimum=1, maximum=23, unit="hours",
    help="If Metronome was off, busy or updating at the scheduled time, the refresh still starts once "
         "that clears, up to this long afterwards.",
))
register(Setting(
    key="scan.retry_minutes", section="scanner", group=SCHEDULE,
    label="Try a refused daily refresh again every", kind="int", default=5, minimum=1, maximum=120,
    unit="minutes",
    help="When the refresh cannot start because other scanner work or an update is in progress, it tries "
         "again this often until its late-start time runs out.",
))


def _folder_names(names: list[str]) -> list[str]:
    for name in names:
        if "/" in name or "\\" in name:
            raise ValueError(f"{name} is a path. Enter the folder's own name only.")
    return names


register(Setting(
    key="scan.folder_depth", section="scanner", group=READS, label="Folder levels searched for reports",
    # A model export sits two folders below its report, or three when the
    # reports folder has a "reports" subfolder; fewer levels would hide them.
    kind="int", default=walker.MAX_DISCOVERY_DEPTH, minimum=3, maximum=10, unit="levels",
    applies="next_scan",
    help="How many folders below the reports folder the scanner opens; reports stored deeper than this "
         "are not found.",
))
register(Setting(
    key="scan.skip_folders", section="scanner", group=READS, label="Folder names to skip", kind="list",
    default=list(walker.SKIPPED_FOLDER_NAMES), max_items=100, applies="next_scan",
    placeholder="Archive, Old versions", check=_folder_names,
    help="A folder with one of these names is not opened, wherever it sits under the reports folder; "
         "capital letters do not matter. Separate names with commas.",
))
register(Setting(
    key="scan.max_pbix_mb", section="scanner", group=READS, label="Largest .pbix file the scanner opens",
    kind="int", default=pbix_parser.MAX_PBIX_MB, minimum=0, maximum=102400, unit="MB", applies="next_scan",
    help="A larger file is left unread and its report keeps what the last scan found; 0 means no limit.",
))

register(Setting(
    key="scan.stalled_after_seconds", section="scanner", group=TROUBLE, label="Show a scan as stalled after",
    kind="int", default=jobs.DEFAULT_STALE_AFTER_SECONDS, minimum=60, maximum=3600, unit="seconds",
    env=("DG_SCANNER_STALE_AFTER_SECONDS",),
    help="A running scan with no progress for this long is marked stalled on the Scanner page and the "
         "people listed for scan failures are emailed; the scan itself is not stopped.",
))

register(Setting(
    key="probe.connect_timeout_seconds", section="scanner", group=POSTGRES, label="Wait to connect",
    kind="int", default=prober.CONNECT_TIMEOUT_SECONDS, minimum=1, maximum=120, unit="seconds",
    help="How long a check waits for the PostgreSQL server to accept the connection before it gives up.",
))
register(Setting(
    key="probe.lock_timeout_seconds", section="scanner", group=POSTGRES, label="Wait for a locked table",
    kind="int", default=prober.LOCK_TIMEOUT_SECONDS, minimum=1, maximum=600, unit="seconds",
    help="A check that waits this long for a table locked by other work gives up on that table.",
))
register(Setting(
    key="probe.statement_timeout_seconds", section="scanner", group=POSTGRES,
    label="Longest a single query may run", kind="int", default=prober.STATEMENT_TIMEOUT_SECONDS,
    minimum=30, maximum=900, unit="seconds", env=("DG_PG_SCAN_STATEMENT_TIMEOUT_SECONDS",),
    help="A freshness or lineage query that runs longer than this is cancelled, so one slow table cannot "
         "hold up the whole scan.",
))

register(Setting(
    key="pbi.request_timeout_seconds", section="scanner", group=POWER_BI,
    label="Wait for an answer from Power BI", kind="int", default=pbi_fetch.REQUEST_TIMEOUT_SECONDS,
    minimum=10, maximum=600, unit="seconds",
    help="A request to Power BI that gets no answer in this time is treated as failed.",
))
register(Setting(
    key="pbi.sync_wait_minutes", section="scanner", group=POWER_BI, label="Wait for a Power BI sync to finish",
    kind="int", default=pbi_sync.PBI_SYNC_WAIT_TIMEOUT_SECONDS // 60, minimum=1, maximum=240,
    unit="minutes", applies="next_scan",
    help="A refresh waits this long for the Power BI sync it started, then reports the sync as failed.",
))
register(Setting(
    key="pbi.usage_days", section="scanner", group=POWER_BI, label="Days of report views to fetch",
    kind="int", default=pbi_fetch.USAGE_DAYS_BACK, minimum=1, maximum=90, unit="days",
    env=("DG_PBI_USAGE_DAYS_BACK",), applies="next_scan",
    help="Each usage sync asks Power BI for report views from this many past days that are not stored yet.",
))
register(Setting(
    key="pbi.activity_pages_per_day", section="scanner", group=POWER_BI, label="Pages of activity read per day",
    kind="int", default=pbi_fetch.MAX_ACTIVITY_PAGES_PER_DAY, minimum=1, maximum=5000, unit="pages",
    applies="next_scan",
    help="A day with more activity than this is left incomplete and fetched again at the next sync.",
))

# --- Freshness and alerts --------------------------------------------------------

OUTDATED = "Outdated sources"

register(Setting(
    key="freshness.grace_hours", section="freshness", group=OUTDATED,
    label="Time allowed after a scheduled refresh", kind="int", default=freshness.GRACE_HOURS,
    minimum=0, maximum=168, unit="hours",
    help="A source or Flow with a set refresh time counts as late only once this long has passed since "
         "that time without new data.",
))
register(Setting(
    key="freshness.alert_severity", section="freshness", group=OUTDATED,
    label="Alert level for an outdated source", kind="choice", default=prober.FRESHNESS_ALERT_SEVERITY,
    choices=prober.FRESHNESS_ALERT_SEVERITIES, choice_labels=("Critical", "Warning"), applies="next_scan",
    help="Used for alerts raised from the next freshness check on; alerts that are already open keep "
         "their level.",
))
register(Setting(
    key="freshness.report_lag_hours", section="freshness", group=OUTDATED,
    label="A report may be behind its source by", kind="int", default=prober.REPORT_LAG_HOURS,
    minimum=0, maximum=168, unit="hours", applies="next_scan",
    help="A report that last refreshed more than this long before its newest source data gets a "
         "schedule alert.",
))
register(Setting(
    key="freshness.probe_history_days", section="freshness", group=OUTDATED,
    label="Keep freshness check history for", kind="int", default=prober.SOURCE_PROBE_RETENTION_DAYS,
    minimum=7, maximum=3650, unit="days", applies="next_scan",
    help="Older results are deleted after each freshness check; the latest result for every source is "
         "always kept.",
))

# The grace is used for every source and Flow row a page lists, so the
# freshness module keeps it in memory and reads it again when it is saved.
subscribe(("freshness.grace_hours",), freshness.refresh_grace_period)

# --- Updates, backups and logs ---------------------------------------------------

UPDATES = "Updates"
BACKUPS = "Backups"

register(Setting(
    key="updates.check_seconds", section="updates", group=UPDATES, label="Check for a new version every",
    kind="int", default=90, minimum=60, maximum=86400, unit="seconds",
    help="How often Metronome asks GitHub whether a newer version has been released.",
))
register(Setting(
    key="updates.retry_minutes", section="updates", group=UPDATES,
    label="After a failed install, try the same version again after", kind="int", default=30,
    minimum=5, maximum=1440, unit="minutes",
    help="Stops a version that fails to install from being tried again at every check; choosing Install "
         "on the Updates page is never held back.",
))

register(Setting(
    key="backups.daily_time", section="updates", group=BACKUPS, label="Daily database backup", kind="time",
    default="06:00",
    help="When Metronome copies its own database each day.",
))
register(Setting(
    key="backups.keep", section="updates", group=BACKUPS, label="Backups to keep", kind="int",
    default=runner.KEEP_ALL_BACKUPS, minimum=0, maximum=3650, unit="backups",
    help="After each backup, before a scan or at the daily time, older copies beyond this number are "
         "deleted; 0 keeps every backup.",
))
