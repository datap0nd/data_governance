#!/usr/bin/env python3
"""Run Metronome against the offline sandbox built by tools/seed_sandbox.py.

Sets every environment variable the app needs so all data comes from the
sandbox folder, starts the sandbox PostgreSQL cluster when one exists, and
launches uvicorn. Nothing outside the sandbox folder is touched, so the
whole environment disappears when the folder is deleted.

Usage:
    python tools/run_sandbox.py                 # http://localhost:8000
    python tools/run_sandbox.py --port 8123
    python tools/run_sandbox.py --stop          # stop the sandbox PostgreSQL
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", default=str(BASE_DIR / "local_sandbox"),
                        help="Sandbox folder (default: <repo>/local_sandbox)")
    parser.add_argument("--port", type=int, default=8000, help="App port (default: 8000)")
    parser.add_argument("--host", default="127.0.0.1", help="App bind host (default: 127.0.0.1)")
    parser.add_argument("--stop", action="store_true",
                        help="Stop the sandbox PostgreSQL cluster and exit")
    args = parser.parse_args()

    dest = Path(args.dest).resolve()
    config_path = dest / "sandbox_config.json"
    if not config_path.exists():
        raise SystemExit(
            f"No sandbox found at {dest}. Build one first: python tools/seed_sandbox.py"
        )
    config = json.loads(config_path.read_text(encoding="utf-8"))

    from tools.seed_sandbox import find_pg_bin, start_postgres, stop_postgres

    pg_bin = Path(config["pg_bin"]) if config.get("pg_bin") else find_pg_bin()
    pgdata = dest / "pgdata"
    pg_enabled = bool(config.get("pg_enabled")) and pg_bin is not None and pgdata.exists()

    if args.stop:
        if pg_enabled:
            stop_postgres(pg_bin, pgdata)
            print(f"Sandbox PostgreSQL at {pgdata} stopped.")
        else:
            print("No sandbox PostgreSQL to stop.")
        return

    env = os.environ.copy()
    env.update({
        "DG_DB_PATH": str(dest / "governance.db"),
        "DG_TMDL_ROOT": str(dest),
        "usage_files_path": str(dest / "usage"),
        "DG_IMPORT_SCRIPT_DIR": str(dest / "generated_imports"),
        "DG_AI_MOCK": env.get("DG_AI_MOCK", "true"),
    })
    if pg_enabled:
        start_postgres(pg_bin, pgdata)
        env.update({
            "PGHOST": "127.0.0.1",
            "PGPORT": str(config["pg_port"]),
            "PGUSER": config["pg_user"],
            "PGPASSWORD": config["pg_password"],
            "PGDATABASE": config["pg_database"],
            "DG_UPLOAD_PGUSER": config["pg_user"],
            "DG_UPLOAD_PGPASSWORD": config["pg_password"],
            "DG_UPLOAD_SCHEMA": "bi_reporting",
        })
        print(f"Sandbox PostgreSQL running on 127.0.0.1:{config['pg_port']}")
    else:
        print("Sandbox PostgreSQL not enabled - SQL sources will probe as unknown.")

    print(f"Starting Metronome on http://{args.host}:{args.port} (data: {dest})")
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--host", args.host, "--port", str(args.port)]
    raise SystemExit(subprocess.call(cmd, cwd=BASE_DIR, env=env))


if __name__ == "__main__":
    main()
