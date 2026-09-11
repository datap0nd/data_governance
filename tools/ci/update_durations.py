"""Build checked-in file weights from one complete sharded JUnit run."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from tools.ci.sharding import SHARD_COUNT, inventory


def _test_file(classname: str, files: set[str]) -> str | None:
    parts = classname.split(".")
    for length in range(len(parts), 0, -1):
        candidate = "/".join(parts[:length]) + ".py"
        if candidate in files:
            return candidate
    return None


def junit_file_weights(root: Path, os_key: str, files: list[str]) -> dict[str, float]:
    junit_files = sorted(root.glob(f"python-{os_key}-*/junit.xml"))
    if len(junit_files) != SHARD_COUNT:
        raise ValueError(f"{os_key} has {len(junit_files)} JUnit files, expected {SHARD_COUNT}")
    known = set(files)
    totals: dict[str, float] = defaultdict(float)
    unknown: set[str] = set()
    for junit in junit_files:
        for case in ET.parse(junit).getroot().iter("testcase"):
            path = _test_file(case.attrib.get("classname", ""), known)
            if path is None:
                unknown.add(case.attrib.get("classname", ""))
            else:
                totals[path] += float(case.attrib.get("time", 0))
    if unknown:
        raise ValueError(f"JUnit contains unknown test modules: {sorted(unknown)}")
    return {path: round(max(totals.get(path, 0), 0.001), 3) for path in sorted(files)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--os", dest="os_key", choices=("ubuntu", "windows"), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    files = inventory(args.root.resolve())
    weights = junit_file_weights(args.artifacts_root, args.os_key, files)
    existing = json.loads(args.output.read_text(encoding="utf-8"))
    payload = {
        "schema_version": 1,
        "source": f"GitHub sharded JUnit run {args.run_id}; unlisted files use the conservative default",
        "default_seconds": existing["default_seconds"],
        "files": weights,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"os": args.os_key, "files": len(weights), "total_seconds": round(sum(weights.values()), 3)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
