"""Python-script Flow source: run saved scripts in order on the worker.

Standard library only. The portable ``run_flow.py`` generator copies ``flow_*``
modules verbatim and rejects application imports, so nothing here may touch
``app.config``, the database or FastAPI.
"""
from __future__ import annotations

import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import queue
import signal
import threading
import time
from pathlib import Path

SOURCE_TYPE = "python"
ADAPTER = "python_script"
MODULE = "Python"
OUTPUT_FORMATS = ("csv", "xlsx")
MAX_SCRIPTS = 20
SCRIPT_TIMEOUT_SECONDS = 3600
# Workers advertise this capability once they honour per-script arguments and
# values; a job that uses either is never claimed by an older worker.
ARGUMENTS_CAPABILITY = "python_script_arguments_v1"
RUN_CAPABILITY = "python_script_run_v1"
# Workers advertise this once they start run-only scripts the way PowerShell
# would: on Windows in the signed-in desktop session with the account's
# standard rights, never inside the session-0 worker service.
DESKTOP_CAPABILITY = "python_script_desktop_v1"
# Per-script arguments are one line typed by the owner; values are one short
# line each and the same script runs once per value.
MAX_ARGUMENT_CHARS = 2000
MAX_VALUES = 200
MAX_VALUE_CHARS = 500
# Tail of a script's stdout/stderr kept in the failure message and step record.
OUTPUT_TAIL_CHARS = 4000

ARGUMENT_TOKEN_RE = re.compile(r"\{(flow|run_id|date|value)\}")
VALUE_TOKEN = "{value}"


def _is_absolute_worker_path(value: str) -> bool:
    # Same rule as app.routers.flows._is_absolute_worker_path; reimplemented
    # here so the portable bundle never imports the router.
    return bool(
        value.startswith("/")
        or value.startswith("\\\\")
        or re.match(r"^[A-Za-z]:[\\/]", value)
    )


def _basename(value: str) -> str:
    return re.split(r"[\\/]", str(value or "").rstrip("\\/"))[-1]


def _single_line(text: str) -> bool:
    return not any(ord(char) < 32 or ord(char) == 127 for char in text)


# --- Arguments: Windows-style splitting, tokens and validation ---------------

def split_arguments(text: str) -> list[str]:
    """Split one line of arguments the way a Windows program sees them.

    Whitespace separates tokens; a double-quoted span keeps its spaces; a
    doubled quote inside a quoted span (``""``) is a literal quote; backslashes
    are ordinary characters, so ``C:\\data\\in.xlsx`` passes through unchanged.
    No shell is involved.
    """
    text = str(text or "")
    tokens: list[str] = []
    current: list[str] = []
    in_token = False
    quoted = False
    position = 0
    length = len(text)
    while position < length:
        char = text[position]
        if quoted:
            if char == '"':
                if position + 1 < length and text[position + 1] == '"':
                    current.append('"')
                    position += 2
                    continue
                quoted = False
                position += 1
                continue
            current.append(char)
            position += 1
            continue
        if char == '"':
            quoted = True
            in_token = True
            position += 1
            continue
        if char.isspace():
            if in_token:
                tokens.append("".join(current))
                current = []
                in_token = False
            position += 1
            continue
        current.append(char)
        in_token = True
        position += 1
    if quoted:
        raise ValueError("Script arguments have an unclosed quote.")
    if in_token:
        tokens.append("".join(current))
    return tokens


def quote_argument(value: str) -> str:
    """One token that ``split_arguments`` gives back verbatim."""
    value = str(value if value is not None else "")
    if value and not any(char.isspace() for char in value) and '"' not in value:
        return value
    return '"' + value.replace('"', '""') + '"'


def render_arguments(text: str, *, flow_name, run_id, date, value=None) -> str:
    """Replace ``{flow}``, ``{run_id}``, ``{date}`` and ``{value}`` before splitting.

    Unknown ``{...}`` spans are left as typed. ``date`` is the Dubai calendar
    date supplied by the worker as a string (this module never computes one);
    ``None`` leaves ``{date}`` as typed. A ``None`` value renders ``{value}`` as
    an empty string.
    """
    replacements = {
        "flow": str(flow_name if flow_name is not None else ""),
        "run_id": str(run_id if run_id is not None else ""),
        "date": None if date is None else str(date),
        "value": "" if value is None else str(value),
    }

    def replace(match: re.Match) -> str:
        replacement = replacements[match.group(1)]
        return match.group(0) if replacement is None else replacement

    return ARGUMENT_TOKEN_RE.sub(replace, str(text or ""))


def _clean_arguments(value) -> str:
    text = str(value if value is not None else "").strip()
    if not _single_line(text):
        raise ValueError("Script arguments must be a single line.")
    if len(text) > MAX_ARGUMENT_CHARS:
        raise ValueError(f"Script arguments must be {MAX_ARGUMENT_CHARS} characters or fewer.")
    split_arguments(text)  # an unclosed quote is reported at save time, beside Save
    return text


def _clean_values(values) -> list[str]:
    cleaned: list[str] = []
    for item in values if isinstance(values, list) else []:
        text = str(item if item is not None else "").strip()
        if not text:
            continue
        if not _single_line(text) or len(text) > MAX_VALUE_CHARS:
            raise ValueError(f"Each value must be a single line of {MAX_VALUE_CHARS} characters or fewer.")
        cleaned.append(text)  # duplicates stay: the owner may want a repeat run
    if len(cleaned) > MAX_VALUES:
        raise ValueError(f"Choose at most {MAX_VALUES} values per script.")
    return cleaned


def aligned_arguments(values, count: int) -> list[str]:
    """One argument string per script: padded with "" and truncated to ``count``."""
    items = [str(item if item is not None else "") for item in values] if isinstance(values, list) else []
    return (items + [""] * count)[:count]


def aligned_values(values, count: int) -> list[list[str]]:
    """One value list per script: padded with [] and truncated to ``count``."""
    rows: list[list[str]] = []
    for item in values if isinstance(values, list) else []:
        rows.append([str(entry if entry is not None else "") for entry in item] if isinstance(item, list) else [])
    return (rows + [[] for _ in range(count)])[:count]


def normalize_steps(scripts, arguments=None, values=None) -> tuple[list[str], list[str], list[list[str]]]:
    """Clean, dedupe and validate the ordered script rows from the editor.

    Blank script rows are dropped together with their arguments and values;
    the arguments and values lists are padded or truncated to the scripts'
    length first. Rows are deduplicated on (normalized path, arguments,
    values), so the same script may run twice with different arguments.
    """
    raw_scripts = list(scripts or [])
    count = len(raw_scripts)
    rows: list[tuple[str, str, list[str]]] = []
    seen: set[tuple] = set()
    for value, argument, row_values in zip(raw_scripts, aligned_arguments(arguments, count), aligned_values(values, count)):
        script = str(value or "").strip().strip('"').strip("'").strip()
        if not script:
            continue
        argument = _clean_arguments(argument)
        row_values = _clean_values(row_values)
        key = (script.replace("\\", "/").casefold(), argument, tuple(row_values))
        if key in seen:
            continue
        seen.add(key)
        rows.append((script, argument, row_values))
    if not rows:
        raise ValueError("Add at least one Python script.")
    for script, _argument, _row_values in rows:
        if not _is_absolute_worker_path(script):
            raise ValueError("Python scripts must use absolute paths visible to the worker.")
        if any(token in script for token in ("*", "?")):
            raise ValueError("Python scripts must name one exact .py file each, without wildcards.")
        if not _basename(script).casefold().endswith(".py") or _basename(script).casefold() == ".py":
            raise ValueError("Python scripts must be .py files.")
    if len(rows) > MAX_SCRIPTS:
        raise ValueError(f"Choose at most {MAX_SCRIPTS} Python scripts.")
    return (
        [script for script, _a, _v in rows],
        [argument for _s, argument, _v in rows],
        [row_values for _s, _a, row_values in rows],
    )


def normalize_scripts(values) -> list[str]:
    """Clean, dedupe and validate an ordered script list without arguments."""
    return normalize_steps(values, None, None)[0]


def saved_steps(flow: dict) -> tuple[list[str], list[str], list[list[str]]]:
    """Aligned (scripts, arguments, values) from a flow row or ``_flow_out`` dict."""
    def stored(key: str, default):
        value = flow.get(key)
        if value is None:
            try:
                value = json.loads(flow.get(f"{key}_json") or "")
            except (TypeError, ValueError):
                value = default
        return value if isinstance(value, list) else default

    raw_scripts = stored("python_scripts", [])
    count = len(raw_scripts)
    scripts: list[str] = []
    arguments: list[str] = []
    values: list[list[str]] = []
    for script, argument, row_values in zip(
        raw_scripts,
        aligned_arguments(stored("python_script_arguments", []), count),
        aligned_values(stored("python_script_values", []), count),
    ):
        if not str(script or "").strip():
            continue
        scripts.append(str(script))
        arguments.append(argument)
        values.append(row_values)
    return scripts, arguments, values


def saved_scripts(flow: dict) -> list[str]:
    """The ordered script list from a flow row or ``_flow_out`` dict."""
    return saved_steps(flow)[0]


def job_section(flow: dict) -> dict:
    """Build ``job["python_source"]`` from a ``_flow_out`` dict."""
    enabled = (flow.get("source_type") or "portal") == SOURCE_TYPE
    destination = "sql" if flow.get("sql_handoff_enabled") else "file"
    output_format = str(flow.get("file_format") or "csv").strip().casefold()
    if destination == "sql" or output_format not in OUTPUT_FORMATS:
        output_format = "csv"
    scripts, arguments, values = saved_steps(flow) if enabled else ([], [], [])
    return {
        "enabled": enabled,
        "mode": flow.get("python_run_mode") or "outputs",
        "interpreter": flow.get("python_interpreter") or "",
        "scripts": scripts,
        "arguments": arguments,
        "values": values,
        "output_format": output_format,
        "destination": destination,
        "timeout_seconds": int(flow.get("python_timeout_minutes") or 60) * 60,
    }


def resolve_interpreter(configured: str | None = None, environment: dict | None = None) -> tuple[str, str]:
    """Choose the computer's Python, never the embedded worker interpreter."""
    env = os.environ if environment is None else environment
    requested = str(configured or env.get("DG_PYTHON_EXE") or "").strip()
    if requested:
        if not Path(requested).is_file():
            raise RuntimeError(f"Configured Python interpreter does not exist: {requested}")
        return requested, "Flow setting" if configured else "DG_PYTHON_EXE"
    for candidate, reason in (
        (shutil.which("py"), "py launcher on PATH"),
        (r"C:\Windows\py.exe", "Windows py launcher"),
        (str(Path(env.get("LOCALAPPDATA", "")) / "Programs/Python/Launcher/py.exe") if env.get("LOCALAPPDATA") else None, "user py launcher"),
        (shutil.which("python"), "python on PATH"),
    ):
        if candidate and Path(candidate).is_file() and "windowsapps" not in str(candidate).casefold():
            return str(candidate), reason
    raise RuntimeError("No computer Python interpreter was found. Set Python to use on the Flow or DG_PYTHON_EXE.")


def run_plan(scripts, arguments=None, values=None) -> list[dict]:
    """The flat, ordered list of script runs: one per value of each row.

    A row without values runs once with ``value`` ``None``. Every run knows its
    1-based flat ``index`` and the ``steps`` total, its ``row`` and ``rows``,
    and its ``position`` among the ``row_runs`` of its row.
    """
    scripts = list(scripts or [])
    count = len(scripts)
    plan: list[dict] = []
    for row, (script, argument, row_values) in enumerate(
        zip(scripts, aligned_arguments(arguments, count), aligned_values(values, count)), start=1,
    ):
        runs = row_values or [None]
        for position, value in enumerate(runs, start=1):
            plan.append({
                "row": row, "rows": count, "script": script, "arguments_text": argument,
                "value": value, "position": position, "row_runs": len(runs),
            })
    for index, run in enumerate(plan, start=1):
        run["index"] = index
        run["steps"] = len(plan)
    return plan


def run_count(section: dict) -> int:
    """Number of script runs a ``python_source`` section expands to."""
    section = section or {}
    return len(run_plan(section.get("scripts") or [], section.get("arguments"), section.get("values")))


def deliverable_count(section: dict) -> int:
    """Number of final files: one per value of the last row, at least one."""
    section = section or {}
    scripts = section.get("scripts") or []
    if not scripts:
        return 0
    values = aligned_values(section.get("values"), len(scripts))
    return len(values[-1]) or 1


def filename_token(value) -> str:
    """``{value}`` in a filename template: anything outside [A-Za-z0-9._-] becomes ``_``."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value if value is not None else ""))


def describe(scripts: list[str], arguments=None, values=None) -> str:
    """Script basenames in execution order, for labels and messages.

    Each name is followed by its raw arguments when present and by
    ``(N values)`` when the row runs once per value.
    """
    parts: list[str] = []
    for index, script in enumerate(scripts or []):
        label = _basename(str(script))
        argument = str(arguments[index] or "").strip() if isinstance(arguments, list) and index < len(arguments) else ""
        if argument:
            label += " " + argument
        row_values = values[index] if isinstance(values, list) and index < len(values) else None
        if isinstance(row_values, list) and row_values:
            label += f" ({len(row_values)} value{'s' if len(row_values) != 1 else ''})"
        parts.append(label)
    return " → ".join(parts)


def script_command(script: Path, input_path: Path | None, output_path: Path, arguments=()) -> list[str]:
    """The command line: the owner's arguments come right after the script, before Metronome's flags."""
    command = [sys.executable, str(script), *[str(item) for item in arguments or ()]]
    if input_path is not None:
        command += ["--input", str(input_path)]
    command += ["--output", str(output_path)]
    return command


def step_environment(base: dict, *, input_path, output_path, results_dir, step, steps,
                     output_format, flow_name, run_id, inputs=None, value=None) -> dict:
    environment = dict(base)
    environment.pop("METRONOME_FLOW_INPUT", None)
    environment.pop("METRONOME_FLOW_INPUTS", None)
    environment.update({
        "METRONOME_FLOW_OUTPUT": str(output_path),
        "METRONOME_FLOW_RESULTS_DIR": str(results_dir),
        "METRONOME_FLOW_STEP": str(int(step)),
        "METRONOME_FLOW_STEPS": str(int(steps)),
        "METRONOME_FLOW_OUTPUT_FORMAT": str(output_format),
        "METRONOME_FLOW_NAME": str(flow_name),
        "METRONOME_FLOW_RUN_ID": str(run_id),
        "METRONOME_FLOW_VALUE": "" if value is None else str(value),
        "PYTHONIOENCODING": "utf-8",
    })
    if input_path is not None:
        environment["METRONOME_FLOW_INPUT"] = str(input_path)
    if inputs:
        environment["METRONOME_FLOW_INPUTS"] = os.pathsep.join(str(item) for item in inputs)
    return environment


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_script_names(scripts) -> list[Path]:
    """The checks that need no file access: how many scripts, and the .py suffix.

    Used when the scripts are opened somewhere else (the signed-in Windows
    session), which then confirms that each one exists.
    """
    checked = [Path(str(item)) for item in scripts or []]
    if not checked:
        raise RuntimeError("Python-script job has no scripts to run.")
    if len(checked) > MAX_SCRIPTS:
        raise RuntimeError(f"Python-script job lists more than {MAX_SCRIPTS} scripts.")
    for script in checked:
        if script.suffix.casefold() != ".py":
            raise RuntimeError(f"Python script is not a .py file: {script.name}")
    return checked


def check_scripts(scripts: list[Path]) -> list[Path]:
    """Fail closed before anything runs: every script exists and is a .py file."""
    checked = [Path(str(item)) for item in scripts or []]
    if not checked:
        raise RuntimeError("Python-script job has no scripts to run.")
    if len(checked) > MAX_SCRIPTS:
        raise RuntimeError(f"Python-script job lists more than {MAX_SCRIPTS} scripts.")
    for script in checked:
        if script.suffix.casefold() != ".py":
            raise RuntimeError(f"Python script is not a .py file: {script.name}")
        if not script.is_file():
            raise RuntimeError(f"Python script does not exist or is not readable: {script}")
    return checked


def _tail(text) -> str:
    return (text or "").strip()[-OUTPUT_TAIL_CHARS:]


def _kill_process_tree(process, observer=None) -> None:
    """End the launched process group and any descendants the worker tracked."""
    if observer is not None and hasattr(observer, "kill_all"):
        observer.kill_all()
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_process(command: list[str], *, cwd: Path, environment: dict,
                timeout_seconds: float, wait_descendants: bool = False,
                observer=None, on_line=None, on_tick=None, stop_requested=None) -> subprocess.CompletedProcess:
    """Run with live, bounded line draining and a whole-tree timeout/Stop.

    Reader threads never block on the observer or network. Their bounded queue
    records drops; the worker's live observer publishes the count separately.
    """
    process = subprocess.Popen(
        command, cwd=str(cwd), env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                       getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
        start_new_session=os.name != "nt",
    )
    if observer is not None and hasattr(observer, "start"):
        observer.start(process)
    lines = queue.Queue(maxsize=5000)
    tails = {"stdout": "", "stderr": ""}
    finished = {"stdout": False, "stderr": False}
    root_exited_at = None

    def decode_line(data: bytearray) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode(locale.getpreferredencoding(False), errors="replace")

    def reader(stream: str, pipe):
        pending = bytearray()
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                for byte in chunk:
                    if byte in (10, 13):
                        if pending:
                            item = decode_line(pending)
                            try:
                                lines.put_nowait((stream, item[:2000]))
                            except queue.Full:
                                if observer is not None and hasattr(observer, "note_drop"):
                                    observer.note_drop()
                            pending.clear()
                    elif len(pending) < 8000:
                        pending.append(byte)
            if pending:
                item = decode_line(pending)
                try:
                    lines.put_nowait((stream, item[:2000]))
                except queue.Full:
                    if observer is not None and hasattr(observer, "note_drop"):
                        observer.note_drop()
        finally:
            finished[stream] = True

    threads = [threading.Thread(target=reader, args=(name, pipe), daemon=True)
               for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout_seconds
    last_tick = 0.0
    try:
        while True:
            try:
                stream, line = lines.get(timeout=0.1)
                if on_line is not None:
                    sanitized = on_line(stream, line, process.pid)
                    if isinstance(sanitized, str):
                        line = sanitized
                tails[stream] = (tails[stream] + line + "\n")[-OUTPUT_TAIL_CHARS:]
            except queue.Empty:
                pass
            now = time.monotonic()
            if observer is not None and now - last_tick >= 1:
                observer.sample()
            if on_tick is not None and now - last_tick >= 1:
                on_tick(process, observer)
            if now - last_tick >= 1:
                last_tick = now
            if stop_requested is not None and stop_requested():
                raise RuntimeError("Python script run was stopped.")
            if now >= deadline:
                raise TimeoutError(f"Python script exceeded its {round(timeout_seconds)} second time limit.")
            if process.poll() is not None and root_exited_at is None:
                root_exited_at = now
            descendants = (observer.running_descendants() if observer is not None and
                           hasattr(observer, "running_descendants") else [])
            pipes_done = finished["stdout"] and finished["stderr"]
            if (root_exited_at is not None and not wait_descendants and now - root_exited_at > .5
                    and lines.empty()):
                break
            if (root_exited_at is not None and (not wait_descendants or not descendants)
                    and pipes_done and lines.empty()):
                break
        return subprocess.CompletedProcess(command, process.returncode,
                                           tails["stdout"], tails["stderr"])
    except (TimeoutError, RuntimeError):
        _kill_process_tree(process, observer)
        raise
    finally:
        if observer is not None and hasattr(observer, "sample"):
            observer.sample()


def run_scripts(scripts: list[Path], final_outputs, steps_folder: Path, *, environment: dict,
                flow_name: str, run_id: int, output_format: str, arguments=None, values=None,
                date: str | None = None, timeout_seconds: int = SCRIPT_TIMEOUT_SECONDS,
                progress=None, step_result=None, observer_factory=None,
                on_line=None, on_tick=None, stop_requested=None) -> list[dict]:
    """Run every script row in order, once per value; each run must write its ``--output``.

    ``final_outputs`` holds one reserved final path per run of the last row (a
    single ``Path`` is accepted for one deliverable). ``--input`` is always the
    previous run's output and ``METRONOME_FLOW_INPUTS`` lists every output of
    the previous row. ``progress`` receives each run's record before the run
    starts; ``step_result`` receives it as soon as the run ends: the same dict
    that is returned for a successful run, or one carrying ``error`` for the
    failed run before the failure is raised, so a broken chain still leaves a
    structured record per run that happened.
    """
    scripts = check_scripts(scripts)
    plan = run_plan(scripts, arguments, values)
    finals = [Path(final_outputs)] if isinstance(final_outputs, (str, Path)) else [Path(item) for item in final_outputs]
    last_row = len(scripts)
    expected = sum(1 for run in plan if run["row"] == last_row)
    if len(finals) != expected:
        raise RuntimeError(
            f"Python-script job reserved {len(finals)} final file(s) for {expected} run(s) of the last script."
        )
    records: list[dict] = []
    previous_output: Path | None = None
    row_outputs: dict[int, list[Path]] = {}
    for run in plan:
        script = run["script"]
        index, total = run["index"], run["steps"]
        output = finals[run["position"] - 1] if run["row"] == last_row else steps_folder / f"step-{index}-{script.stem}.csv"
        label = f"Python script {script.name} (step {index} of {total})"
        checksum = _checksum(script)
        record = {
            "index": index,
            "steps": total,
            "row": run["row"],
            "script": str(script),
            "script_name": script.name,
            "script_checksum": checksum,
            "value": run["value"],
            "arguments": [],
            "arguments_text": "",
            "output_path": str(output),
            "output_size": 0,
            "exit_code": None,
            "duration_ms": 0,
            "stdout": "",
            "stderr": "",
        }

        def fail(message: str, cause: BaseException | None = None):
            record["error"] = message
            record["output_size"] = output.stat().st_size if output.is_file() else 0
            if step_result is not None:
                step_result(record)
            raise RuntimeError(message) from cause

        try:
            text = render_arguments(
                run["arguments_text"], flow_name=flow_name, run_id=run_id, date=date, value=run["value"],
            )
            if run["value"] is not None and VALUE_TOKEN not in run["arguments_text"]:
                # The value is one extra token after the typed arguments.
                text = f"{text} {quote_argument(run['value'])}".strip()
            split = split_arguments(text)
        except ValueError as exc:
            fail(f"{label} has invalid arguments: {exc}", exc)
        record["arguments"] = split
        record["arguments_text"] = text
        if progress is not None:
            progress(record)
        command = script_command(script, previous_output, output, split)
        step_env = step_environment(
            environment, input_path=previous_output, output_path=output, results_dir=steps_folder,
            step=index, steps=total, output_format=output_format, flow_name=flow_name, run_id=run_id,
            inputs=row_outputs.get(run["row"] - 1), value=run["value"],
        )
        started = time.perf_counter()
        try:
            observer = observer_factory() if observer_factory is not None else None
            completed = run_process(
                command, cwd=script.parent, environment=step_env,
                timeout_seconds=timeout_seconds,
                observer=observer, on_line=on_line, on_tick=on_tick,
                stop_requested=stop_requested,
            )
        except TimeoutError as exc:
            record["duration_ms"] = round((time.perf_counter() - started) * 1000)
            fail(f"{label} timed out after {timeout_seconds} seconds and was stopped.", exc)
        except OSError as exc:
            record["duration_ms"] = round((time.perf_counter() - started) * 1000)
            fail(f"{label} could not be started: {exc}", exc)
        record["duration_ms"] = round((time.perf_counter() - started) * 1000)
        record["exit_code"] = completed.returncode
        stdout = record["stdout"] = _tail(completed.stdout)
        stderr = record["stderr"] = _tail(completed.stderr)
        if completed.returncode != 0:
            detail = stderr or stdout or "no output"
            fail(f"{label} failed with exit code {completed.returncode}: {detail}")
        if not output.is_file() or output.stat().st_size <= 0:
            detail = f" Script output: {stderr or stdout}" if (stderr or stdout) else ""
            fail(
                f"{label} completed but did not create --output {output}. "
                f"The script must write the file named by --output (METRONOME_FLOW_OUTPUT).{detail}"
            )
        record["output_size"] = output.stat().st_size
        records.append(record)
        if step_result is not None:
            step_result(record)
        row_outputs.setdefault(run["row"], []).append(output)
        previous_output = output
    return records


# File-producing variables that a run-only script must never inherit.
RUN_MODE_STALE_VARIABLES = ("METRONOME_FLOW_OUTPUT", "METRONOME_FLOW_INPUT", "METRONOME_FLOW_INPUTS",
                            "METRONOME_FLOW_RESULTS_DIR", "METRONOME_FLOW_OUTPUT_FORMAT")


def run_variables(*, flow_name, run_id, step, steps, value=None) -> dict:
    """What Metronome sets for one run-only step, on top of the script's environment."""
    return {
        "METRONOME_FLOW_MODE": "run", "METRONOME_FLOW_NAME": str(flow_name),
        "METRONOME_FLOW_RUN_ID": str(run_id), "METRONOME_FLOW_STEP": str(step),
        "METRONOME_FLOW_STEPS": str(steps),
        "METRONOME_FLOW_VALUE": "" if value is None else str(value),
        # Output reaches Metronome through a pipe, not a console: keep prints
        # live and Unicode-safe, as they are in a console window.
        "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
    }


def run_environment(base: dict, variables: dict) -> dict:
    """``base`` without stale file-producing variables, plus one step's ``variables``."""
    environment = dict(base)
    for key in RUN_MODE_STALE_VARIABLES:
        environment.pop(key, None)
    environment.update(variables)
    return environment


def run_scripts_only(scripts: list[Path], *, environment: dict, flow_name: str,
                     run_id: int, interpreter: str, arguments=None, values=None,
                     date: str | None = None, timeout_seconds: int = SCRIPT_TIMEOUT_SECONDS,
                     progress=None, step_result=None, observer_factory=None,
                     on_line=None, on_tick=None, stop_requested=None,
                     session=None, checksums=None) -> list[dict]:
    """Run scripts exactly as typed without requiring or producing any file.

    ``session`` starts each step somewhere else instead of as a child of this
    process: the worker passes the signed-in Windows session. It receives
    only the variables Metronome sets, because the script keeps that
    session's own environment, and ``checksums`` holds each script's SHA-256
    as that session read it, since this process may not see the same drives.
    """
    scripts = check_scripts(scripts) if session is None else check_script_names(scripts)
    plan = run_plan(scripts, arguments, values)
    deadline = time.monotonic() + timeout_seconds
    records = []
    for run in plan:
        script = run["script"]
        index, total = run["index"], run["steps"]
        label = f"Python script {script.name} (step {index} of {total})"
        text = render_arguments(run["arguments_text"], flow_name=flow_name,
                                run_id=run_id, date=date, value=run["value"])
        if run["value"] is not None and VALUE_TOKEN not in run["arguments_text"]:
            text = f"{text} {quote_argument(run['value'])}".strip()
        split = split_arguments(text)
        command = [interpreter, str(script), *split]
        variables = run_variables(flow_name=flow_name, run_id=run_id, step=index,
                                  steps=total, value=run["value"])
        checksum = (checksums or {}).get(str(script)) if session is not None else _checksum(script)
        record = {"index": index, "steps": total, "row": run["row"],
                  "script": str(script), "script_name": script.name,
                  "script_checksum": checksum, "value": run["value"],
                  "arguments": split, "arguments_text": text,
                  "output_path": None, "output_size": 0, "exit_code": None,
                  "duration_ms": 0, "stdout": "", "stderr": ""}
        if progress is not None:
            progress(record)
        observer = observer_factory() if observer_factory is not None else None
        started = time.perf_counter()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("The Flow time limit elapsed before this step started.")
            if session is None:
                completed = run_process(
                    command, cwd=script.parent, environment=run_environment(environment, variables),
                    timeout_seconds=remaining,
                    wait_descendants=True, observer=observer,
                    on_line=on_line, on_tick=on_tick, stop_requested=stop_requested,
                )
            else:
                completed = session.run(
                    command, cwd=script.parent, variables=variables,
                    timeout_seconds=remaining, observer=observer,
                    on_line=on_line, on_tick=on_tick, stop_requested=stop_requested,
                )
            record["exit_code"] = completed.returncode
            record["stdout"] = _tail(completed.stdout)
            record["stderr"] = _tail(completed.stderr)
            if completed.returncode != 0:
                raise RuntimeError(f"{label} failed with exit code {completed.returncode}: "
                                   f"{record['stderr'] or record['stdout'] or 'no output'}")
        except (OSError, TimeoutError, RuntimeError) as exc:
            record["duration_ms"] = round((time.perf_counter() - started) * 1000)
            record["error"] = str(exc) if str(exc).startswith(label) else f"{label}: {exc}"
            if step_result is not None:
                step_result(record)
            if isinstance(exc, TimeoutError):
                raise TimeoutError(record["error"]) from exc
            if isinstance(exc, OSError):
                raise RuntimeError(record["error"]) from exc
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - started) * 1000)
        records.append(record)
        if step_result is not None:
            step_result(record)
    return records


def requires_arguments_capability(section: dict) -> bool:
    """True when a job uses arguments or values an older worker would ignore."""
    section = section or {}
    if not section.get("enabled"):
        return False
    if any(str(item or "").strip() for item in section.get("arguments") or []):
        return True
    return any(row for row in section.get("values") or [] if isinstance(row, list) and row)
