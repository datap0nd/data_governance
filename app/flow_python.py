"""Python-script Flow source: run saved scripts in order on the worker.

Standard library only. The portable ``run_flow.py`` generator copies ``flow_*``
modules verbatim and rejects application imports, so nothing here may touch
``app.config``, the database or FastAPI.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

SOURCE_TYPE = "python"
ADAPTER = "python_script"
MODULE = "Python"
OUTPUT_FORMATS = ("csv", "xlsx")
MAX_SCRIPTS = 20
SCRIPT_TIMEOUT_SECONDS = 3600
# Tail of a script's stdout/stderr kept in the failure message and step record.
OUTPUT_TAIL_CHARS = 4000


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


def normalize_scripts(values) -> list[str]:
    """Clean, dedupe and validate the ordered script list from the editor."""
    scripts: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        script = str(value or "").strip().strip('"').strip("'").strip()
        if not script:
            continue
        key = script.replace("\\", "/").casefold()
        if key in seen:
            continue
        seen.add(key)
        scripts.append(script)
    if not scripts:
        raise ValueError("Add at least one Python script.")
    for script in scripts:
        if not _is_absolute_worker_path(script):
            raise ValueError("Python scripts must use absolute paths visible to the worker.")
        if any(token in script for token in ("*", "?")):
            raise ValueError("Python scripts must name one exact .py file each, without wildcards.")
        if not _basename(script).casefold().endswith(".py") or _basename(script).casefold() == ".py":
            raise ValueError("Python scripts must be .py files.")
    if len(scripts) > MAX_SCRIPTS:
        raise ValueError(f"Choose at most {MAX_SCRIPTS} Python scripts.")
    return scripts


def saved_scripts(flow: dict) -> list[str]:
    """The ordered script list from a flow row or ``_flow_out`` dict."""
    scripts = flow.get("python_scripts")
    if scripts is None:
        try:
            scripts = json.loads(flow.get("python_scripts_json") or "[]")
        except (TypeError, ValueError):
            scripts = []
    return [str(item) for item in scripts if str(item or "").strip()] if isinstance(scripts, list) else []


def job_section(flow: dict) -> dict:
    """Build ``job["python_source"]`` from a ``_flow_out`` dict."""
    enabled = (flow.get("source_type") or "portal") == SOURCE_TYPE
    destination = "sql" if flow.get("sql_handoff_enabled") else "file"
    output_format = str(flow.get("file_format") or "csv").strip().casefold()
    if destination == "sql" or output_format not in OUTPUT_FORMATS:
        output_format = "csv"
    return {
        "enabled": enabled,
        "scripts": saved_scripts(flow) if enabled else [],
        "output_format": output_format,
        "destination": destination,
        "timeout_seconds": SCRIPT_TIMEOUT_SECONDS,
    }


def describe(scripts: list[str]) -> str:
    """Script basenames in execution order, for labels and messages."""
    return " → ".join(_basename(str(item)) for item in scripts or [])


def script_command(script: Path, input_path: Path | None, output_path: Path) -> list[str]:
    command = [sys.executable, str(script)]
    if input_path is not None:
        command += ["--input", str(input_path)]
    command += ["--output", str(output_path)]
    return command


def step_environment(base: dict, *, input_path, output_path, results_dir, step, steps,
                     output_format, flow_name, run_id) -> dict:
    environment = dict(base)
    environment.pop("METRONOME_FLOW_INPUT", None)
    environment.update({
        "METRONOME_FLOW_OUTPUT": str(output_path),
        "METRONOME_FLOW_RESULTS_DIR": str(results_dir),
        "METRONOME_FLOW_STEP": str(int(step)),
        "METRONOME_FLOW_STEPS": str(int(steps)),
        "METRONOME_FLOW_OUTPUT_FORMAT": str(output_format),
        "METRONOME_FLOW_NAME": str(flow_name),
        "METRONOME_FLOW_RUN_ID": str(run_id),
        "PYTHONIOENCODING": "utf-8",
    })
    if input_path is not None:
        environment["METRONOME_FLOW_INPUT"] = str(input_path)
    return environment


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def run_scripts(scripts: list[Path], final_output: Path, steps_folder: Path, *, environment: dict,
                flow_name: str, run_id: int, output_format: str, timeout_seconds: int = SCRIPT_TIMEOUT_SECONDS,
                progress=None, step_result=None) -> list[dict]:
    """Run every script in order; each must write exactly its ``--output`` file.

    ``step_result`` receives each step's record as soon as the step ends: the
    same dict that is returned for a successful step, or one carrying ``error``
    for the failed step before the failure is raised, so a broken chain still
    leaves a structured record per step that ran.
    """
    scripts = check_scripts(scripts)
    total = len(scripts)
    records: list[dict] = []
    previous_output: Path | None = None
    for index, script in enumerate(scripts, start=1):
        output = final_output if index == total else steps_folder / f"step-{index}-{script.stem}.csv"
        label = f"Python script {script.name} (step {index} of {total})"
        checksum = _checksum(script)
        if progress is not None:
            progress(index, total, script, checksum)
        command = script_command(script, previous_output, output)
        step_env = step_environment(
            environment, input_path=previous_output, output_path=output, results_dir=steps_folder,
            step=index, steps=total, output_format=output_format, flow_name=flow_name, run_id=run_id,
        )
        record = {
            "index": index,
            "steps": total,
            "script": str(script),
            "script_name": script.name,
            "script_checksum": checksum,
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

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command, cwd=str(script.parent), env=step_env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            record["duration_ms"] = round((time.perf_counter() - started) * 1000)
            record["stdout"] = _tail(exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout)
            record["stderr"] = _tail(exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr)
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
        previous_output = output
    return records
