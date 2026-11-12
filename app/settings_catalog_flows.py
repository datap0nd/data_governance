"""Flow run, worker, SQL step and delivery settings.

Imported by ``app.settings_catalog``. A value a worker process uses is copied
into each job when the job is queued (``flow_job_limits``), because workers
never read Metronome's database; such a setting is declared with
``applies="new_runs"``. Everything else is read by the web service at the
moment of use.

Each default is the constant the code used before the setting existed. A
worker constant that cannot be imported here without loading the whole worker
is repeated as a number, and ``tests/test_settings_flows.py`` keeps the two
equal.
"""
from __future__ import annotations

import logging

from app import (
    flow_capacity, flow_email_delivery, flow_python, flow_recorder_worker, flow_recording_runtime,
    flow_recordings, flow_replay, flow_retention, flow_sql, flow_view_refresh,
)
from app.settings_registry import Setting, register, subscribe

logger = logging.getLogger("settings")

RUN_LIMITS = "Run limits"
DOWNLOADS = "Downloads"
SQL_STEP = "SQL step"
SCRIPTS = "Scripts and catalogs"
FLOW_EMAIL = "Attachments and recipients"
VISUAL_EXPORTS = "Power BI visual exports"
KEEPS = " Runs already queued or in progress keep the old value."
SCAN_KEEPS = " Scans already queued or in progress keep the old value."


def _server(key, group, label, default, *, help, kind="int", section="flows", **extra):
    """A limit the web service reads at the moment it uses it."""
    return register(Setting(key=key, section=section, group=group, label=label, kind=kind, default=default,
                            help=help, **extra))


def _frozen(key, group, label, default, *, help, kind="int", keeps=KEEPS, **extra):
    """A limit copied into each job when it is queued; see ``flow_job_limits``."""
    return register(Setting(key=key, section="flows", group=group, label=label, kind=kind, default=default,
                            applies="new_runs", help=help + keeps, **extra))


# --- Run limits ------------------------------------------------------------------

_server("flows.run_silent_minutes", RUN_LIMITS, "Mark a run failed after no contact for", 10,
        minimum=5, maximum=120, unit="minutes",
        help="A run whose worker has not reported for this long is marked failed and its owner is told.")
_server("flows.worker_online_seconds", RUN_LIMITS, "A worker counts as online for", flow_capacity.ONLINE_SECONDS,
        minimum=60, maximum=600, unit="seconds",
        help="After this long without contact a worker is shown as offline, and a background worker is started again.")
_server("flows.run_folders_kept", RUN_LIMITS, "Run folders kept per Flow", flow_retention.RUN_FOLDER_KEEP,
        minimum=1, maximum=50, unit="folders",
        help="When a run starts, older run folders beyond this many are removed; a folder a retry still needs is kept.")
_server("flows.recording_start_seconds", RUN_LIMITS, "Wait for the recording browser to open",
        flow_recordings.STARTUP_TIMEOUT_SECONDS, minimum=30, maximum=900, unit="seconds",
        help="A recording or recording test whose browser has not opened after this long is stopped so it can be tried again.")
_frozen("flows.failure_screenshots_kept", RUN_LIMITS, "Failure screenshots kept per worker", 20,
        minimum=1, maximum=100, unit="screenshots",
        help="Each worker keeps this many pictures of the page from failed runs and scans, then replaces the oldest.")
_frozen("flows.recording_test_folders_kept", RUN_LIMITS, "Recording test folders kept per Flow",
        flow_recorder_worker.VALIDATION_FOLDERS_KEPT, minimum=1, maximum=20, unit="folders",
        help="Each test of a recording keeps its trace and output; older ones are removed after a new test.",
        keeps=" Tests already queued or in progress keep the old value.")

_server("flows.setup_sign_in_browsers", RUN_LIMITS, "Worker browsers signed in during setup",
        flow_capacity.DEFAULT_SETUP_SIGN_IN_BROWSERS, minimum=1, maximum=flow_capacity.MAX_SLOTS, unit="of each kind",
        help="Setup signs in this many background and this many visible worker browsers, lowest slots first. "
             "Every other worker signs in by itself, with the saved portal sign-in, the first time a Flow needs "
             "a portal.")

# --- Downloads -------------------------------------------------------------------

_frozen("flows.download_start_minutes", DOWNLOADS, "Wait for an ASAP download to start", 15,
        minimum=1, maximum=240, unit="minutes",
        help="An ASAP file that has not begun to arrive after this long counts as failed and is tried again.")
_frozen("flows.download_handover_minutes", DOWNLOADS, "Wait for a GSCM or recorded download to start", 60,
        minimum=5, maximum=480, unit="minutes",
        help="A file the portal has not prepared and handed to the browser after this long counts as failed "
             "and is tried again.")
_frozen("flows.export_attempts", DOWNLOADS, "Tries for each file", 3, minimum=1, maximum=10, unit="tries",
        help="A file that fails is started again from opening the report, up to this many tries, on ASAP and other websites.")
_frozen("flows.gscm_export_attempts", DOWNLOADS, "Tries for each GSCM file", 2, minimum=1, maximum=10, unit="tries",
        help="A GSCM file that fails is started again from opening the bookmark, up to this many tries.")
_frozen("flows.recorded_action_seconds", DOWNLOADS, "Time limit for one recorded action",
        flow_recording_runtime.ACTION_TIMEOUT_MS // 1000, minimum=10, maximum=1800, unit="seconds",
        help="A recorded click, entry or check that has not finished after this long fails the run; "
             "an action recorded with its own limit keeps it.")
_frozen("flows.network_replay", DOWNLOADS, "Repeat the portal's own export request when possible", True,
        kind="bool", env=("DG_FLOW_REPLAY",),
        help="An ASAP or GSCM file is fetched with the request the portal made for it last time, "
             "and the run clicks through the portal only when that does not work.")
_frozen("flows.replay_max_age_days", DOWNLOADS, "Reuse a saved export request for",
        flow_replay.RECIPE_MAX_AGE_DAYS, minimum=1, maximum=90, unit="days",
        help="A saved request older than this is thrown away and recorded again from the next download in the browser.")
_frozen("flows.replay_request_minutes", DOWNLOADS, "Wait for a repeated export request",
        flow_replay.REPLAY_TIMEOUT_MS // 60_000, minimum=1, maximum=60, unit="minutes",
        help="When the portal has not answered a repeated request after this long, the run downloads through the browser instead.")

# --- SQL step --------------------------------------------------------------------

_frozen("flows.sql_connect_seconds", SQL_STEP, "Wait to connect to the database",
        flow_sql.SQL_CONNECT_TIMEOUT_SECONDS, minimum=5, maximum=600, unit="seconds",
        help="The SQL step fails when the database has not accepted the connection after this long.")
_frozen("flows.sql_lock_minutes", SQL_STEP, "Wait for a busy target table",
        flow_sql.SQL_LOCK_TIMEOUT_SECONDS // 60, minimum=1, maximum=240, unit="minutes",
        help="When someone else is using the target table, the SQL step waits this long and then fails without writing.")
_frozen("flows.sql_statement_minutes", SQL_STEP, "Longest single load or replace",
        flow_sql.SQL_STATEMENT_TIMEOUT_SECONDS // 60, minimum=1, maximum=1440, unit="minutes",
        help="Each file load, and the replace that follows, is stopped after this long and nothing is written.")
_frozen("flows.view_lock_minutes", SQL_STEP, "Wait for a busy materialized view",
        flow_view_refresh.LOCK_TIMEOUT_MS // 60_000, minimum=1, maximum=240, unit="minutes",
        help="When a materialized view a Flow refreshes after its SQL step is in use, the refresh waits this long and then fails.")
_frozen("flows.view_refresh_minutes", SQL_STEP, "Longest refresh of one materialized view",
        flow_view_refresh.STATEMENT_TIMEOUT_MS // 60_000, minimum=1, maximum=1440, unit="minutes",
        help="A refresh a Flow runs after its SQL step is stopped after this long; the rows already written stay.")

# --- Scripts and catalogs --------------------------------------------------------

_frozen("flows.script_default_minutes", SCRIPTS, "Stop a script that sets no time limit after",
        flow_python.SCRIPT_TIMEOUT_SECONDS // 60, minimum=1, maximum=1440, unit="minutes",
        help="Used for a transformation script, and for a Python-script Flow saved without its own time limit.")
_server("flows.sql_catalog_time", SCRIPTS, "Daily scan of SQL tables", "05:30", kind="time",
        help="When Metronome reads the list of databases, schemas and tables a Flow can write to. Dubai time.")
_frozen("flows.catalog_scan_minutes", SCRIPTS, "Longest ASAP catalog scan", 90,
        minimum=10, maximum=720, unit="minutes", keeps=SCAN_KEEPS,
        help="A scan of the ASAP report menus stops opening reports after this long and keeps what it has found.")
_frozen("flows.scan_overlay_minutes", SCRIPTS, "Catalog scan: wait for one report to load", 30,
        minimum=1, maximum=120, unit="minutes", keeps=SCAN_KEEPS,
        help="During an ASAP catalog scan, a report that is still loading after this long is skipped.")


def _reschedule_sql_catalog_scan() -> None:
    """Move the running service's daily job to the saved time.

    Runs inside a save, so it never raises: the time is already saved and
    takes effect at the next start in any case.
    """
    try:
        from app.routers import flows

        flows.reschedule_sql_catalog_refresh()
    except Exception:
        logger.exception("The daily SQL table scan keeps its old time until Metronome restarts")


subscribe(("flows.sql_catalog_time",), _reschedule_sql_catalog_scan)

# --- Email -----------------------------------------------------------------------

_server("email.flow_attachment_mb", FLOW_EMAIL, "Largest total attachment size",
        flow_email_delivery.MAX_ATTACHMENT_BYTES // (1024 * 1024), section="email",
        minimum=1, maximum=150, unit="MB",
        help="When a Flow's files are larger than this in total, its email says where they are instead of attaching them.")
_server("email.flow_max_recipients", FLOW_EMAIL, "Most recipients for one Flow email",
        flow_email_delivery.MAX_RECIPIENTS, section="email", minimum=1, maximum=100, unit="addresses",
        help="A Flow cannot be saved with more addresses than this; a Flow saved earlier keeps the ones it has.")

_server("email.visual_export_timeout_seconds", VISUAL_EXPORTS, "Wait for Power BI to return a table", 120,
        section="email", minimum=10, maximum=1800, unit="seconds", env=("DG_PBI_VISUAL_EXPORT_TIMEOUT_SECONDS",),
        help="An alert email fails, and its owner is told, when Power BI has not returned the table after this long.")
_server("email.visual_export_max_rows", VISUAL_EXPORTS, "Most rows read from a table", 30000,
        section="email", minimum=1, maximum=30000, unit="rows", env=("DG_PBI_VISUAL_EXPORT_MAX_ROWS",),
        help="Power BI returns at most 30,000 rows, and a table that reaches the limit is treated as cut off.")
_server("email.visual_export_cut_off", VISUAL_EXPORTS, "When a table is cut off at the row limit", "send",
        section="email", kind="choice", choices=("send", "hold"),
        choice_labels=("Send the email with a notice at the top", "Do not send, and report the alert as failed"),
        help="A cut-off table is missing every row beyond the limit.")
