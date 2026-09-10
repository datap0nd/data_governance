"""Run one serial or sharded pytest selection and preserve a result manifest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--os", dest="os_key", required=True)
    parser.add_argument("--shard", default="serial")
    parser.add_argument("--basetemp", type=Path, required=True)
    args = parser.parse_args()
    files = json.loads(args.files_json.read_text(encoding="utf-8"))
    if not isinstance(files, list) or not files:
        raise SystemExit("test selection must be a non-empty JSON list")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.junit.parent.mkdir(parents=True, exist_ok=True)
    args.basetemp.mkdir(parents=True, exist_ok=True)
    pytest_manifest = args.output.with_name("pytest-manifest.json")
    environment = os.environ.copy()
    environment["METRONOME_PYTEST_MANIFEST"] = str(pytest_manifest)
    started = time.monotonic()
    command = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "-q",
        "-ra",
        "--durations=20",
        "-p",
        "tools.ci.pytest_manifest",
        f"--basetemp={args.basetemp}",
        f"--junitxml={args.junit}",
    ]
    completed = subprocess.run(command, env=environment, check=False)
    duration = round(time.monotonic() - started, 3)
    pytest_result = (
        json.loads(pytest_manifest.read_text(encoding="utf-8"))
        if pytest_manifest.exists()
        else {
            "exit_code": completed.returncode,
            "collected_files": [],
            "collected_nodes": [],
            "collection_errors": ["pytest did not produce its manifest"],
            "outcomes": {},
            "timing_seconds": {},
        }
    )
    payload = {
        "schema_version": 1,
        "os": args.os_key,
        "shard": args.shard,
        "assigned": files,
        "exit_code": completed.returncode,
        "duration_seconds": duration,
        "pytest": pytest_result,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
