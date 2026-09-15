"""Atomically create or refresh private automatic-auditor configuration."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def provision(root: Path, reader_url: str, dsn: str = ""):
    root = root.resolve()
    host_path = root / "host" / "host.json"
    reader_path = root / "reader" / "reader.json"
    exchange = root / "exchange"
    exchange.mkdir(parents=True, exist_ok=True)
    old_host = json.loads(host_path.read_text(encoding="utf-8")) if host_path.is_file() else {}
    old_reader = json.loads(reader_path.read_text(encoding="utf-8")) if reader_path.is_file() else {}
    token = old_host.get("reader_token") or old_reader.get("reader_token") or secrets.token_urlsafe(48)
    category_key = old_reader.get("category_key") or secrets.token_urlsafe(48)
    manifest = str(exchange / "manifest.sqlite")
    policy = str(exchange / "policy.json")
    _write(host_path, {"version": 1, "reader_url": reader_url,
                       "reader_token": token, "manifest_path": manifest,
                       "policy_path": policy})
    _write(reader_path, {"version": 1, "reader_token": token,
                         "category_key": category_key,
                         "manifest_path": manifest, "policy_path": policy,
                         "reader_dsn": dsn or old_reader.get("reader_dsn", "")})
    return host_path, reader_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--reader-url", default="http://127.0.0.1:8766")
    args = parser.parse_args()
    # Reuse only the existing probe identity. Uploader credentials are never a fallback.
    values = {name: os.environ.get(name, "") for name in
              ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")}
    dsn = ""
    if all(values.values()):
        from urllib.parse import quote
        dsn = (f"postgresql://{quote(values['PGUSER'])}:{quote(values['PGPASSWORD'])}"
               f"@{values['PGHOST']}:{values['PGPORT']}/{quote(values['PGDATABASE'])}")
    host, reader = provision(Path(args.root), args.reader_url, dsn)
    print(host)
    print(reader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
