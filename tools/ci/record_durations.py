"""Record how long each test file took, from the junit XML of a CI run.

    python tools/ci/record_durations.py --run <label> shard-0/test-results.xml shard-1/test-results.xml

Writes ``tools/ci/test_durations.json``, which ``pytest_shard.py`` uses to give
every runner about the same amount of work. Refresh it when the split has
drifted: download the ``python-*-results`` artifacts of a passing run and pass
every ``test-results.xml`` in them.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

OUTPUT = Path(__file__).with_name("test_durations.json")


def test_file(classname: str) -> str | None:
    """``tests.test_flows`` or ``tests.test_flows.TestCase`` -> ``tests/test_flows.py``."""
    parts = classname.split(".")
    for position, part in enumerate(parts):
        if part.startswith("test_"):
            return "/".join(parts[: position + 1]) + ".py"
    return None


def collect(paths: list[Path]) -> dict[str, float]:
    seconds: dict[str, float] = defaultdict(float)
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            name = test_file(case.get("classname") or "")
            if name:
                seconds[name] += float(case.get("time") or 0)
    return {name: round(total, 1) for name, total in sorted(seconds.items()) if total > 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("junit", nargs="+", type=Path)
    parser.add_argument("--run", required=True, help="where the timings came from, for the record")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    files = collect(args.junit)
    if not files:
        print("No test timings found.")
        return 1
    args.output.write_text(
        json.dumps({"recorded_from": args.run, "total_seconds": round(sum(files.values())), "files": files},
                   indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"Recorded {len(files)} files, {round(sum(files.values()))} seconds, in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
