"""Split complete pytest collection by file; execute sequentially on each runner."""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest


def partition(nodeids: list[str], count: int) -> list[list[str]]:
    """Keep file fixtures/order together, balancing by collected test count."""
    if count < 1 or not nodeids or len(set(nodeids)) != len(nodeids):
        raise ValueError("shards require a positive count and unique, nonempty collection")
    files: dict[str, list[str]] = defaultdict(list)
    for nodeid in nodeids:
        files[nodeid.split("::", 1)[0]].append(nodeid)
    groups: list[list[str]] = [[] for _ in range(count)]
    for filename in sorted(files, key=lambda name: (-len(files[name]), name)):
        target = min(range(count), key=lambda index: (len(groups[index]), index))
        groups[target].extend(files[filename])
    return groups


def pytest_addoption(parser):
    group = parser.getgroup("ci-shards")
    group.addoption("--ci-shard-index", type=int, required=True)
    group.addoption("--ci-shard-count", type=int, required=True)
    group.addoption("--ci-shard-manifest", required=True)


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
            selected = set(partition(collected, self.manifest["count"])[self.manifest["index"]])
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
