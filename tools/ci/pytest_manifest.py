"""Pytest plugin that emits exact collection, outcome and phase timing evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path


_collected_files: list[str] = []
_collected_nodes: list[str] = []
_collection_errors: list[str] = []
_outcomes: dict[str, str] = {}
_timings = {"setup": 0.0, "call": 0.0, "teardown": 0.0}


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def pytest_collectreport(report):
    if report.failed:
        _collection_errors.append(str(report.longrepr))


def pytest_collection_finish(session):
    global _collected_files, _collected_nodes
    _collected_nodes = [item.nodeid for item in session.items]
    _collected_files = sorted({_relative(Path(str(item.path))) for item in session.items})


def pytest_runtest_logreport(report):
    if report.when in _timings:
        _timings[report.when] += float(report.duration)
    if report.when == "call":
        _outcomes[report.nodeid] = report.outcome
    elif report.when == "setup" and report.outcome in {"failed", "skipped"}:
        _outcomes[report.nodeid] = report.outcome
    elif report.when == "teardown" and report.failed:
        _outcomes[report.nodeid] = "failed"


def pytest_sessionfinish(session, exitstatus):
    output = os.environ.get("METRONOME_PYTEST_MANIFEST")
    if not output:
        return
    payload = {
        "schema_version": 1,
        "exit_code": int(exitstatus),
        "collected_files": _collected_files,
        "collected_nodes": _collected_nodes,
        "collection_errors": _collection_errors,
        "outcomes": dict(sorted(_outcomes.items())),
        "timing_seconds": {key: round(value, 6) for key, value in _timings.items()},
    }
    Path(output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
