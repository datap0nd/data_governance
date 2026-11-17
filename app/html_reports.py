"""HTML report refresh Flows: Metronome schedules, launches and watches.

An HTML report is a static report kept in its own Git repository and published
as a site (GitHub Pages). A separate *dashboard refresh controller*, installed
on the worker PC, owns everything that touches the report: extraction,
validation, the build and the push to GitHub, using the Git sign-in of the
Windows account it runs as. Metronome never clones, pulls, builds or pushes,
and holds no GitHub credentials.

A refresh is an ordinary Python **run-only** Flow whose single script is the
controller and whose arguments name one allowed report. The command is built
here, on the server, from Settings (host PC only) and the Flow's chosen report
when a run is queued, so a browser never supplies a command line, and a report
removed from the allowed list or a controller that is not configured stops the
run before it starts. Execution, Stop, time limit, live console and run history
are the existing run-only machinery.

The controller reports progress as stdout lines::

    METRONOME_STAGE {"version":1,"report":"<id>","stage":"<name>","status":"started"}
    METRONOME_STAGE {"version":1,"report":"<id>","stage":"<name>","status":"succeeded","duration_seconds":12.4}

Those lines are parsed on the server as the worker posts console output. Every
field is untrusted: only the version, report, stage name, status and duration
are read, names are cleaned of anything that looks like a credential, times are
the worker's own capture times, and nothing a line says can make a run
succeed. The process exit code alone decides the run's result.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app import flow_python

# --- Settings ----------------------------------------------------------------

CONTROLLER_REPOSITORY = "html_reports.controller_repository"
CONTROLLER_SCRIPT = "html_reports.controller_script"
REPORT_ROOT = "html_reports.report_root"
ALLOWED_REPORTS = "html_reports.allowed_reports"
ARGUMENTS = "html_reports.arguments"
DRY_RUN_ARGUMENTS = "html_reports.dry_run_arguments"
LIVE_ARGUMENTS = "html_reports.live_arguments"
SETTING_KEYS = (
    CONTROLLER_REPOSITORY, CONTROLLER_SCRIPT, REPORT_ROOT, ALLOWED_REPORTS,
    ARGUMENTS, DRY_RUN_ARGUMENTS, LIVE_ARGUMENTS,
)
DEFAULT_ARGUMENTS = "--report {report} --report-root {report_root}"
DEFAULT_DRY_RUN_ARGUMENTS = "--dry-run"
SETTINGS_PLACE = "Settings > Flows > HTML reports"

# The controller's report identifiers: letters, digits, '_', '-' and '.',
# starting with a letter or digit. No spaces, quotes, slashes or braces, so an
# identifier can never become a second argument, a path or a token.
REPORT_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
MODES = ("dry_run", "live")
# Tokens the server fills from Settings and the Flow; the worker fills the rest
# ({flow}, {run_id}, {date}) exactly as for every Python Flow.
SERVER_TOKENS = ("report", "report_root")
WORKER_TOKENS = ("flow", "run_id", "date")
TOKEN_RE = re.compile(r"\{([^{}]*)\}")
MAX_URL_CHARS = 500


def _setting(key: str):
    from app import settings_registry
    return settings_registry.get(key)


def controller_settings() -> dict:
    """The controller configuration as Settings holds it right now."""
    from app import settings_registry
    values = settings_registry.snapshot(SETTING_KEYS)
    return {
        "repository_url": values[CONTROLLER_REPOSITORY] or "",
        "script": values[CONTROLLER_SCRIPT] or "",
        "report_root": values[REPORT_ROOT] or "",
        "allowed_reports": list(values[ALLOWED_REPORTS] or []),
        "arguments": values[ARGUMENTS] or "",
        "dry_run_arguments": values[DRY_RUN_ARGUMENTS] or "",
        "live_arguments": values[LIVE_ARGUMENTS] or "",
    }


def public_settings() -> dict:
    """What the Flow builder shows: links, paths, the allowed reports and each exact command."""
    settings = controller_settings()
    problems = setup_problems(settings)
    previews = {}
    if not problems:
        for key in settings["allowed_reports"]:
            try:
                previews[key] = {mode: command({"report_key": key, "mode": mode}, settings)[1] for mode in MODES}
            except ValueError:
                continue
    return {**settings, "ready": not problems, "problems": problems, "settings_place": SETTINGS_PLACE,
            "previews": previews}


def setup_problems(settings: dict) -> list[str]:
    """Why a refresh cannot be queued with these settings; empty when it can."""
    problems = []
    if not settings.get("script"):
        problems.append(f"Set the controller script on the worker in {SETTINGS_PLACE}.")
    if not settings.get("allowed_reports"):
        problems.append(f"Add at least one allowed report ID in {SETTINGS_PLACE}.")
    if "{report_root}" in (settings.get("arguments") or "") and not settings.get("report_root"):
        problems.append(f"The controller arguments use {{report_root}}: set the report root in {SETTINGS_PLACE}.")
    if not str(settings.get("dry_run_arguments") or "").strip():
        problems.append(f"Set the dry-run arguments in {SETTINGS_PLACE}, so a test never publishes.")
    return problems


# --- Setting checks (used by the declarations in settings_catalog_flows) -----

def _has_credentials(url: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://[^/?#]*@", url, re.IGNORECASE))


def clean_link(value: Any, label: str) -> str:
    """An optional http(s) link for display: never with a user name or token in it."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_URL_CHARS or not flow_python._single_line(text) or any(char.isspace() for char in text):
        raise ValueError(f"{label} must be one web address of at most {MAX_URL_CHARS} characters.")
    if not re.match(r"^https?://[^/\s]+", text, re.IGNORECASE):
        raise ValueError(f"{label} must start with https:// (or http://).")
    if _has_credentials(text):
        raise ValueError(f"{label} must not contain a user name, password or token.")
    return text


def check_repository_link(value: str) -> str:
    return clean_link(value, "The controller repository")


def check_controller_script(value: str) -> str:
    text = str(value or "").strip().strip('"')
    if not text:
        return ""
    if any(char in text for char in '*?"{}'):
        raise ValueError("Name one exact .py file, without wildcards, quotes or braces.")
    if not flow_python._basename(text).casefold().endswith(".py") or flow_python._basename(text).casefold() == ".py":
        raise ValueError("The controller script must be a .py file, such as D:\\Tools\\dashboard-refresh-controller\\refresh.py.")
    return text


def check_report_root(value: str) -> str:
    text = str(value or "").strip().strip('"')
    if any(char in text for char in '*?"{}'):
        raise ValueError("Enter one folder, without wildcards, quotes or braces.")
    return text


def check_allowed_reports(values: list[str]) -> list[str]:
    for item in values:
        if not REPORT_KEY_RE.fullmatch(item):
            raise ValueError(
                f"{item} is not a report ID. Use letters, digits, '_', '-' or '.', with no spaces."
            )
    return values


def _check_tokens(text: str, allowed: tuple[str, ...]) -> None:
    for name in TOKEN_RE.findall(text):
        if name not in allowed:
            known = ", ".join("{" + token + "}" for token in allowed)
            raise ValueError(f"{{{name}}} is not a known token. Use {known}.")


def check_arguments(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Enter the controller's arguments, including {report}.")
    flow_python.split_arguments(text)  # an unclosed quote is refused here
    _check_tokens(text, SERVER_TOKENS + WORKER_TOKENS)
    if "{report}" not in text:
        raise ValueError("The arguments must pass the report with {report}, for example --report {report}.")
    return text


def check_mode_arguments(value: str) -> str:
    text = str(value or "").strip()
    flow_python.split_arguments(text)
    _check_tokens(text, WORKER_TOKENS)
    return text


def check_dry_run_arguments(value: str) -> str:
    text = check_mode_arguments(value)
    if not text:
        raise ValueError("A dry run needs the argument that stops publishing, such as --dry-run.")
    return text


# --- The Flow's own configuration --------------------------------------------

def normalize_config(value: Any) -> dict:
    """The shape of a Flow's ``html_report`` block, before Settings are consulted."""
    if not isinstance(value, dict):
        raise ValueError("Choose the HTML report this Flow refreshes.")
    unknown = set(value) - {"report_key", "mode", "repository_url", "site_url"}
    if unknown:
        raise ValueError("The HTML report settings contain fields Metronome does not accept.")
    report_key = str(value.get("report_key") or "").strip()
    if not REPORT_KEY_RE.fullmatch(report_key):
        raise ValueError("Choose a report ID from the allowed list.")
    mode = str(value.get("mode") or "").strip()
    if mode not in MODES:
        raise ValueError("Choose Dry run or Live for this HTML report.")
    return {
        "report_key": report_key,
        "mode": mode,
        "repository_url": clean_link(value.get("repository_url"), "The report repository"),
        "site_url": clean_link(value.get("site_url"), "The published site"),
    }


def saved_config(raw: Any) -> dict | None:
    """A Flow's saved block, or None for every Flow that is not an HTML report refresh."""
    if raw in (None, ""):
        return None
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return normalize_config(value)
    except (TypeError, ValueError):
        # A damaged block must not turn the Flow into an ordinary script Flow:
        # keep it recognisable so queuing it fails closed below.
        return {"report_key": "", "mode": "dry_run", "repository_url": "", "site_url": "", "damaged": True}


def check_allowed(config: dict, settings: dict) -> None:
    if config.get("damaged"):
        raise ValueError("This Flow's HTML report settings are damaged. Open the Flow and save it again.")
    if config["report_key"] not in settings.get("allowed_reports", []):
        raise ValueError(
            f"Report {config['report_key']} is not in the allowed list in {SETTINGS_PLACE}. "
            "Metronome does not run a report that is not allowed."
        )


def command(config: dict, settings: dict, *, dry_run: bool | None = None) -> tuple[str, str]:
    """``(controller script, arguments line)`` for one run; ``ValueError`` when it may not run.

    Fails closed: a missing controller, a report that is no longer allowed or
    an argument pattern that needs a missing report root stops the run. The
    report ID and report root are substituted here and quoted as single
    tokens; the worker fills {flow}, {run_id} and {date} as usual.
    """
    problems = setup_problems(settings)
    if problems:
        raise ValueError(problems[0])
    check_allowed(config, settings)
    script = check_controller_script(settings["script"])
    template = check_arguments(settings["arguments"])
    report_root = check_report_root(settings.get("report_root") or "")
    if report_root and not flow_python._is_absolute_worker_path(report_root):
        raise ValueError(f"The report root in {SETTINGS_PLACE} must be a full path on the worker.")
    values = {"report": config["report_key"], "report_root": report_root}
    rendered = " ".join(
        _token_text(TOKEN_RE.sub(
            lambda match: values[match.group(1)] if match.group(1) in values else match.group(0), token,
        ))
        for token in flow_python.split_arguments(template)
    )
    publish = config["mode"] == "live" if dry_run is None else not dry_run
    extra = check_mode_arguments(settings["live_arguments"]) if publish else check_dry_run_arguments(settings["dry_run_arguments"])
    line = " ".join(part for part in (rendered, extra) if part)
    if len(line) > flow_python.MAX_ARGUMENT_CHARS:
        raise ValueError("The controller arguments are too long.")
    return script, line


def _token_text(token: str) -> str:
    """One argument as the worker's splitter reads it back as exactly one argument.

    The template was split first, so a placeholder the pattern already quoted
    (``"{report_root}"``) is quoted once, not twice. A token that still holds
    a worker token ({flow}, {run_id}, {date}) is always quoted, because the
    worker fills it in before splitting and a Flow name may contain spaces.
    """
    if TOKEN_RE.search(token):
        return '"' + token.replace('"', '""') + '"'
    return flow_python.quote_argument(token)


def apply_to_flow(flow: dict, config: dict, settings: dict, *, dry_run: bool | None = None) -> dict:
    """The Flow as a Python run-only Flow whose one script is the controller."""
    script, arguments = command(config, settings, dry_run=dry_run)
    return {
        **flow,
        "python_scripts": [script],
        "python_script_arguments": [arguments],
        "python_script_values": [[]],
        "python_run_mode": "run",
        # The worker's configured Python (Settings > Folders > Default Python
        # for script Flows, DG_PYTHON_EXE), then the py launcher.
        "python_interpreter": None,
    }


def job_section(config: dict, settings: dict, *, dry_run: bool | None = None) -> dict:
    mode = config["mode"]
    if dry_run is not None:
        mode = "dry_run" if dry_run else "live"
    return {
        "report_key": config["report_key"],
        "mode": mode,
        "publishes": mode == "live",
        "repository_url": config.get("repository_url") or "",
        "site_url": config.get("site_url") or "",
        "controller_repository": settings.get("repository_url") or "",
    }


# --- METRONOME_STAGE events ----------------------------------------------------

EVENT_PREFIX = "METRONOME_STAGE"
EVENT_VERSION = 1
START_STATUSES = ("started", "running")
END_STATUSES = ("succeeded", "failed")
MAX_STAGES = 100
MAX_STAGE_NAME = 200
# Seven days: longer than any Flow time limit (24 hours); a larger figure is
# not a duration this run could have had.
MAX_DURATION_SECONDS = 7 * 24 * 3600

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+@"), r"\1[redacted]@"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})\b"), "[redacted]"),
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [redacted]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization|credential)s?\s*[:=]\s*\S+"),
     r"\1=[redacted]"),
)


def clean_text(value: Any, limit: int = MAX_STAGE_NAME) -> str:
    """One display line: no control characters, nothing shaped like a credential."""
    text = "".join(" " if ord(char) < 32 or ord(char) == 127 or char in "\x85\u2028\u2029" else char
                   for char in str(value))
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].rstrip()


def parse_event(text: str) -> tuple[str, dict | None]:
    """Classify one console line.

    Returns ``("none", None)`` for an ordinary line, ``("malformed", None)``
    for a ``METRONOME_STAGE`` line that cannot be read, ``("ignored", None)``
    for a readable event of another version, and ``("event", event)`` with
    only the fields Metronome uses.
    """
    raw = str(text or "")
    if not raw.startswith(EVENT_PREFIX):
        return "none", None
    rest = raw[len(EVENT_PREFIX):]
    if not rest or not rest[0].isspace():
        return "malformed", None
    try:
        payload = json.loads(rest.strip())
    except (TypeError, ValueError, RecursionError):
        return "malformed", None
    if not isinstance(payload, dict):
        return "malformed", None
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        return "malformed", None
    if version != EVENT_VERSION:
        return "ignored", None
    report = payload.get("report")
    stage = payload.get("stage")
    status = payload.get("status")
    if not isinstance(report, str) or not isinstance(stage, str) or not isinstance(status, str):
        return "malformed", None
    name = clean_text(stage)
    status = status.strip().casefold()
    if not name or not report.strip():
        return "malformed", None
    if status not in START_STATUSES + END_STATUSES:
        return "malformed", None
    event = {"report": report.strip(), "stage": name, "status": status}
    if status in END_STATUSES:
        duration = payload.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            return "malformed", None
        duration = float(duration)
        if not math.isfinite(duration) or duration < 0 or duration > MAX_DURATION_SECONDS:
            return "malformed", None
        event["duration_seconds"] = round(duration, 3)
    return "event", event


def _instant(value: Any, fallback: datetime) -> datetime:
    """A worker capture time as naive UTC; the fallback when it cannot be read."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fallback
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def _parse_stamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def empty_state() -> dict:
    return {"version": 1, "watermark": 0, "events": 0, "malformed": 0, "ignored": 0,
            "overflow": 0, "duplicates": 0, "stages": []}


def load_state(raw: Any) -> dict:
    state = empty_state()
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        value = None
    if isinstance(value, dict):
        for key in ("watermark", "events", "malformed", "ignored", "overflow", "duplicates"):
            if isinstance(value.get(key), int) and not isinstance(value.get(key), bool):
                state[key] = max(0, value[key])
        if isinstance(value.get("stages"), list):
            state["stages"] = [item for item in value["stages"] if isinstance(item, dict)][:MAX_STAGES]
    return state


def apply_lines(state: dict, lines: list[dict], report_key: str, *, received_at: datetime) -> dict:
    """Fold newly posted console lines into the run's stage record.

    Only stdout lines numbered above the watermark are read, so a retried post
    never counts an event twice. Lines are read in their own order.
    """
    fresh = sorted(
        (line for line in lines if int(line.get("line_no") or 0) > state["watermark"]),
        key=lambda line: int(line.get("line_no") or 0),
    )
    for line in fresh:
        state["watermark"] = max(state["watermark"], int(line.get("line_no") or 0))
        if line.get("stream") != "stdout":
            continue
        kind, event = parse_event(line.get("text") or "")
        if kind == "none":
            continue
        if kind in ("malformed", "ignored"):
            state[kind] += 1
            continue
        if event["report"] != report_key:
            state["ignored"] += 1
            continue
        state["events"] += 1
        _apply_event(state, event, _instant(line.get("at"), received_at))
    return state


def _apply_event(state: dict, event: dict, at: datetime) -> None:
    stages = state["stages"]
    open_stage = next(
        (stage for stage in reversed(stages) if stage.get("name") == event["stage"] and stage.get("status") == "running"),
        None,
    )
    if event["status"] in START_STATUSES:
        if open_stage is not None:
            state["duplicates"] += 1
            return
        if len(stages) >= MAX_STAGES:
            state["overflow"] += 1
            return
        stages.append({"name": event["stage"], "status": "running", "started_at": _stamp(at),
                       "finished_at": None, "duration_seconds": None, "start_inferred": False})
        return
    duration = event["duration_seconds"]
    if open_stage is None:
        if len(stages) >= MAX_STAGES:
            state["overflow"] += 1
            return
        # A completion without its start: place the start where its duration
        # says it began, and say so on the page.
        open_stage = {"name": event["stage"], "status": "running",
                      "started_at": _stamp(at - timedelta(seconds=duration)),
                      "finished_at": None, "duration_seconds": None, "start_inferred": True}
        stages.append(open_stage)
    open_stage.update(status=event["status"], finished_at=_stamp(at), duration_seconds=duration)


def current_stage(state: dict) -> str | None:
    running = [stage for stage in state.get("stages", []) if stage.get("status") == "running"]
    return running[-1]["name"] if running else None


TERMINAL = ("succeeded", "failed", "cancelled")


def summary(job_section_value: dict | None, raw_state: Any, run: dict, *, dropped: int = 0) -> dict | None:
    """The run's HTML report block for the run log and run history.

    The run's own status, which follows the process exit code, is the result.
    Stage events only describe progress; they never turn a failed, stopped or
    unfinished run into a success, and a run that succeeded while its stage
    record looks wrong says so instead of hiding it.
    """
    if not isinstance(job_section_value, dict) or not job_section_value.get("report_key"):
        return None
    state = load_state(raw_state)
    status = str(run.get("status") or "")
    terminal = status in TERMINAL
    stages = []
    for stage in state["stages"]:
        item = {key: stage.get(key) for key in ("name", "status", "started_at", "finished_at",
                                                 "duration_seconds", "start_inferred")}
        item["name"] = clean_text(item.get("name") or "")
        if item["status"] not in ("running",) + END_STATUSES:
            item["status"] = "running"
        if item["status"] == "running" and terminal:
            item["status"] = "incomplete"
            item["finished_at"] = run.get("finished_at")
        stages.append(item)
    started = _parse_stamp(run.get("started_at"))
    finished = _parse_stamp(run.get("finished_at"))
    total = (finished - started).total_seconds() if started and finished and finished >= started else None
    failed = [item["name"] for item in stages if item["status"] == "failed"]
    incomplete = [item["name"] for item in stages if item["status"] == "incomplete"]
    notices = []

    def notice(level: str, text: str) -> None:
        notices.append({"level": level, "text": text})

    if state["malformed"]:
        notice("attention", f"{state['malformed']} METRONOME_STAGE line(s) could not be read and were ignored. "
                            "The original lines are in the console below.")
    if state["ignored"]:
        notice("attention", f"{state['ignored']} stage event(s) named another report or event version and were ignored.")
    if state["duplicates"]:
        notice("attention", f"{state['duplicates']} repeated start event(s) were ignored.")
    if state["overflow"]:
        notice("attention", f"Only the first {MAX_STAGES} stages are shown; {state['overflow']} more event(s) were not recorded.")
    if dropped:
        notice("attention", f"{dropped} console line(s) were dropped while the run was busy, so stage events may be missing.")
    if terminal and not stages:
        notice("attention", "The controller reported no stages for this run.")
    if status == "succeeded":
        if failed:
            notice("attention", "The controller reported a failed stage but exited with code 0. "
                                "The run result follows the exit code; check the stages and the console.")
        if incomplete:
            notice("attention", f"{len(incomplete)} stage(s) never reported completion.")
    elif status in ("failed", "cancelled") and stages and not failed and not incomplete:
        notice("failure", "Every reported stage succeeded, but the controller "
                          + ("was stopped" if status == "cancelled" else "exited with an error")
                          + ". The run is not a success.")
    publishes = bool(job_section_value.get("publishes"))
    if status == "succeeded":
        notice("info", "Pushed to GitHub. GitHub Pages may still be deploying; open the site to confirm the new version."
               if publishes else "Dry run: nothing was published.")
    return {
        "report_key": job_section_value.get("report_key"),
        "mode": "live" if publishes else "dry_run",
        "publishes": publishes,
        "repository_url": job_section_value.get("repository_url") or "",
        "site_url": job_section_value.get("site_url") or "",
        "controller_repository": job_section_value.get("controller_repository") or "",
        "stages": stages,
        "counts": {key: state[key] for key in ("events", "malformed", "ignored", "duplicates", "overflow")},
        "total_seconds": total,
        "failed_stages": failed,
        "incomplete_stages": incomplete,
        "notices": notices,
    }


def history_line(block: dict | None) -> str:
    """A short line for the run history table."""
    if not block:
        return ""
    stages = block.get("stages") or []
    done = sum(1 for stage in stages if stage.get("status") == "succeeded")
    parts = ["Live · publishes" if block.get("publishes") else "Dry run · no publishing"]
    if stages:
        parts.append(f"{done} of {len(stages)} stage(s) succeeded")
    else:
        parts.append("no stages reported")
    if block.get("failed_stages"):
        parts.append("failed at " + block["failed_stages"][-1])
    elif block.get("incomplete_stages"):
        parts.append("stopped during " + block["incomplete_stages"][-1])
    return " · ".join(parts)


# --- Reports and lineage ---------------------------------------------------------
#
# Every HTML report refresh Flow has one row in Reports (``reports.report_kind``
# 'html', ``reports.refresh_flow_id`` the Flow). The row carries the report's
# repository and site links, its owner and schedule; its health comes from the
# Flow's runs and any sources linked to it. The Power BI sync never archives it,
# and the report scanner never writes a Power BI report over it.

KIND_HTML = "html"
KIND_POWER_BI = "powerbi"


class ReportNameTaken(ValueError):
    """Another report in Reports already has this Flow's name."""


def schedule_text(flow: dict) -> str:
    """The Flow's main schedule in words, as the Reports page shows it (Dubai time)."""
    kind = str(flow.get("schedule_type") or "manual")
    time = flow.get("schedule_time") or ""
    if kind == "daily":
        return f"Daily {time} Dubai".strip()
    if kind == "weekly":
        days = flow.get("schedule_days")
        if isinstance(days, str):
            try:
                days = json.loads(days)
            except ValueError:
                days = []
        names = ", ".join(str(day)[:3].title() for day in (days or []))
        return f"Weekly {names} {time} Dubai".replace("  ", " ").strip()
    if kind == "monthly":
        return f"Monthly on day {flow.get('schedule_day') or 1} {time} Dubai".strip()
    return "Manual"


def sync_report(db, flow_id: int, *, now: str) -> int | None:
    """Create or update the Reports row of one HTML report refresh Flow.

    Returns the report id, or None when the Flow is not an HTML report
    refresh. Raises ``ReportNameTaken`` when a Power BI report (or another
    Flow's HTML report) already uses the Flow's name, so the caller can refuse
    the save instead of merging two reports.
    """
    flow = db.execute(
        """SELECT f.id, f.name, f.html_report_json, f.schedule_type, f.schedule_time,
                  f.schedule_days, f.schedule_day, p.name AS owner_name
           FROM flows f LEFT JOIN people p ON p.id = f.owner_person_id WHERE f.id=?""",
        (flow_id,),
    ).fetchone()
    if not flow:
        return None
    config = saved_config(flow["html_report_json"])
    if config is None:
        return None
    linked = db.execute("SELECT id FROM reports WHERE refresh_flow_id=?", (flow_id,)).fetchone()
    holder = db.execute(
        "SELECT id, report_kind, refresh_flow_id FROM reports WHERE name=?", (flow["name"],),
    ).fetchone()
    if holder and (not linked or holder["id"] != linked["id"]):
        orphan = holder["report_kind"] == KIND_HTML and holder["refresh_flow_id"] is None
        if not orphan:
            raise ReportNameTaken(
                f"Reports already has a report named {flow['name']}. Give this Flow another name, "
                "so its HTML report has its own entry."
            )
        if linked:
            # The Flow moves onto the earlier entry of the same name; its own
            # entry is archived, never deleted, so its history stays readable.
            db.execute("UPDATE reports SET refresh_flow_id=NULL, archived=1, updated_at=? WHERE id=?",
                       (now, linked["id"]))
        linked = holder
    values = (
        flow["name"], KIND_HTML, flow_id, config.get("repository_url") or None,
        config.get("site_url") or None, schedule_text(dict(flow)), flow["owner_name"], now,
    )
    if linked:
        db.execute(
            """UPDATE reports SET name=?, report_kind=?, refresh_flow_id=?, repository_url=?, site_url=?,
                      frequency=?, owner=COALESCE(NULLIF(owner, ''), ?), archived=0, updated_at=?
               WHERE id=?""",
            (*values, linked["id"]),
        )
        return int(linked["id"])
    cursor = db.execute(
        """INSERT INTO reports (name, report_kind, refresh_flow_id, repository_url, site_url,
                                frequency, owner, discovered_by, archived, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'flow', 0, ?, ?)""",
        (*values, now),
    )
    return int(cursor.lastrowid)


def detach_report(db, flow_id: int, *, now: str) -> None:
    """A deleted Flow leaves its report archived in Reports, with its links and documentation."""
    db.execute(
        "UPDATE reports SET refresh_flow_id=NULL, archived=1, updated_at=? WHERE refresh_flow_id=?",
        (now, flow_id),
    )


def report_refreshes(db) -> dict[int, dict]:
    """Per HTML report: its Flow, the latest finished refresh and the latest published one."""
    rows = db.execute(
        """SELECT r.id AS report_id, f.id AS flow_id, f.name AS flow_name, f.enabled,
                  last.status AS last_status, last.finished_at AS last_finished_at,
                  last.error AS last_error,
                  (SELECT fr.finished_at FROM flow_runs fr
                    WHERE fr.flow_id=f.id AND fr.status='succeeded'
                      AND json_extract(fr.job_json, '$.html_report.publishes')=1
                      AND json_extract(fr.job_json, '$.html_report.report_key')
                          = json_extract(f.html_report_json, '$.report_key')
                    ORDER BY fr.id DESC LIMIT 1) AS last_published_at
           FROM reports r
           JOIN flows f ON f.id = r.refresh_flow_id
           -- Runs of a report ID the Flow refreshed before are not this report's history.
           LEFT JOIN flow_runs last ON last.id = (
               SELECT id FROM flow_runs
                WHERE flow_id=f.id AND status IN ('succeeded','failed','cancelled')
                  AND json_extract(job_json, '$.html_report.report_key')
                      = json_extract(f.html_report_json, '$.report_key')
                ORDER BY id DESC LIMIT 1)
           WHERE r.report_kind=?""",
        (KIND_HTML,),
    ).fetchall()
    return {int(row["report_id"]): dict(row) for row in rows}


def refresh_health(refresh: dict | None) -> str:
    """'healthy', 'degraded' or 'unknown' from the latest finished refresh."""
    if not refresh or not refresh.get("last_status"):
        return "unknown"
    return "healthy" if refresh["last_status"] == "succeeded" else "degraded"
