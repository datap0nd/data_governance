"""Split complete pytest collection by file; execute sequentially on each runner.

Files are spread so every runner gets about the same amount of time, using
the seconds each file took in a recorded run (``test_durations.json`` beside
this module, refreshed with ``record_durations.py``). The slowest runner sets
how long a pull request waits, so an even split is what makes the gate fast.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest

DURATIONS_FILE = Path(__file__).with_name("test_durations.json")


def load_durations(path: Path | str | None) -> dict[str, float]:
    """Recorded seconds per test file. Missing or unreadable means none known."""
    if not path:
        return {}
    try:
        recorded = json.loads(Path(path).read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if not isinstance(recorded, dict):
        return {}
    return {str(name): float(seconds) for name, seconds in recorded.items()
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0}


def partition(nodeids: list[str], count: int, durations: dict[str, float] | None = None) -> list[list[str]]:
    """Keep file fixtures/order together, balancing by recorded time.

    A file with no recorded time counts each of its tests at the average of
    the files that have one, so a new test file is placed sensibly before it
    has ever been timed. With no recorded times at all this balances by the
    number of tests. The result depends only on the collection and the
    recorded times, never on collection order, so every runner computes the
    same split.
    """
    if count < 1 or not nodeids or len(set(nodeids)) != len(nodeids):
        raise ValueError("shards require a positive count and unique, nonempty collection")
    files: dict[str, list[str]] = defaultdict(list)
    for nodeid in nodeids:
        files[nodeid.split("::", 1)[0]].append(nodeid)
    known = {name: seconds for name, seconds in (durations or {}).items() if name in files}
    timed_tests = sum(len(files[name]) for name in known)
    per_test = sum(known.values()) / timed_tests if timed_tests else 1.0
    weight = {name: known.get(name, len(tests) * per_test) for name, tests in files.items()}
    groups: list[list[str]] = [[] for _ in range(count)]
    load = [0.0] * count
    # Heaviest first, each to the runner with the least so far.
    for filename in sorted(files, key=lambda name: (-weight[name], -len(files[name]), name)):
        target = min(range(count), key=lambda index: (load[index], len(groups[index]), index))
        groups[target].extend(files[filename])
        load[target] += weight[filename]
    return groups


def pytest_addoption(parser):
    group = parser.getgroup("ci-shards")
    group.addoption("--ci-shard-index", type=int, required=True)
    group.addoption("--ci-shard-count", type=int, required=True)
    group.addoption("--ci-shard-manifest", required=True)
    group.addoption("--ci-shard-durations", default=str(DURATIONS_FILE))


def pytest_configure(config):
    count = config.getoption("--ci-shard-count")
    index = config.getoption("--ci-shard-index")
    if count < 1 or not 0 <= index < count:
        raise pytest.UsageError("CI shard index must be in [0, count)")
    config.pluginmanager.register(ShardRun(config, index, count), "ci-shard-run")


class ShardRun:
    def __init__(self, config, index: int, count: int):
        self.config = config
        self.manifest = {
            "schema_version": 1,
            "revision": os.environ.get("GITHUB_SHA", "local"),
            "platform": sys.platform,
            "index": index,
            "count": count,
            "collected": [],
            "selected": [],
            "finished": [],
            "exit_code": None,
        }

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, items):
        collected = [item.nodeid for item in items]
        try:
            durations = load_durations(self.config.getoption("--ci-shard-durations"))
            selected = set(partition(collected, self.manifest["count"], durations)[self.manifest["index"]])
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
        if not selected:
            raise pytest.UsageError("CI shard is empty; reduce shard count")
        kept = [item for item in items if item.nodeid in selected]
        deselected = [item for item in items if item.nodeid not in selected]
        self.manifest["collected"] = collected
        self.manifest["selected"] = [item.nodeid for item in kept]
        items[:] = kept
        self.config.hook.pytest_deselected(items=deselected)

    def pytest_runtest_logreport(self, report):
        if report.when == "teardown":
            self.manifest["finished"].append(report.nodeid)

    def pytest_sessionfinish(self, exitstatus):
        self.manifest["exit_code"] = int(exitstatus)
        path = Path(self.config.getoption("--ci-shard-manifest"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8")
