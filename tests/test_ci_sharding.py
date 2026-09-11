from __future__ import annotations

import copy
import contextlib
import os
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools.ci.sharding import SHARD_COUNT, ShardError, partition, reconcile_os, validate_plan
from tools.ci.update_durations import junit_file_weights


def sample_plan():
    files = [f"tests/test_{index}.py" for index in range(1, 13)]
    groups = {str(index + 1): files[index * 2 : index * 2 + 2] for index in range(SHARD_COUNT)}
    return {"inventory": files, "assignments": {"ubuntu": groups}}


def sample_results(plan):
    results = []
    for shard, files in plan["assignments"]["ubuntu"].items():
        outcomes = {f"{path}::test_ok": "passed" for path in files}
        results.append(
            {
                "shard": shard,
                "assigned": files,
                "exit_code": 0,
                "pytest": {
                    "collected_files": files,
                    "collection_errors": [],
                    "outcomes": outcomes,
                },
            }
        )
    return results


def test_longest_first_partition_is_deterministic_balanced_and_includes_new_files():
    files = [f"tests/test_{index}.py" for index in range(12)] + ["tests/test_new.py"]
    weights = {files[0]: 100, files[1]: 90}
    first = partition(files, weights, default=30)
    second = partition(list(reversed(files)), weights, default=30)
    assert first == second
    assert sorted(path for group in first for path in group) == sorted(files)
    assert all(group for group in first)
    assert any("tests/test_new.py" in group for group in first)


def test_plan_rejects_missing_duplicate_and_empty_assignments():
    plan = sample_plan()
    validate_plan(plan)
    duplicate = copy.deepcopy(plan)
    duplicate["assignments"]["ubuntu"]["2"][0] = duplicate["assignments"]["ubuntu"]["1"][0]
    with pytest.raises(ShardError, match="exact-once"):
        validate_plan(duplicate)
    empty = copy.deepcopy(plan)
    empty["assignments"]["ubuntu"]["6"] = []
    with pytest.raises(ShardError):
        validate_plan(empty)


def test_reconciliation_covers_inventory_results_and_platform_skips_exactly_once():
    plan = sample_plan()
    results = sample_results(plan)
    first_node = next(iter(results[0]["pytest"]["outcomes"]))
    results[0]["pytest"]["outcomes"][first_node] = "skipped"
    summary = reconcile_os(plan, "ubuntu", results)
    assert summary == {"os": "ubuntu", "files": 12, "tests": 12, "skipped": 1, "serial_compared": False}


@pytest.mark.parametrize("mutation, message", [
    ("missing", "5 result manifests"),
    ("failed", "failed with exit code"),
    ("stale", "stale or altered"),
    ("collection", "collection errors"),
])
def test_reconciliation_rejects_incomplete_or_failed_shards(mutation, message):
    plan = sample_plan()
    results = sample_results(plan)
    if mutation == "missing":
        results.pop()
    elif mutation == "failed":
        results[0]["exit_code"] = 1
    elif mutation == "stale":
        results[0]["assigned"] = []
    else:
        results[0]["pytest"]["collection_errors"] = ["boom"]
    with pytest.raises(ShardError, match=message):
        reconcile_os(plan, "ubuntu", results)


def test_serial_equivalence_rejects_an_outcome_difference():
    plan = sample_plan()
    results = sample_results(plan)
    outcomes = {node: result for shard in results for node, result in shard["pytest"]["outcomes"].items()}
    serial = {
        "exit_code": 0,
        "pytest": {"collected_files": plan["inventory"], "outcomes": dict(outcomes)},
    }
    assert reconcile_os(plan, "ubuntu", results, serial)["serial_compared"]
    serial["pytest"]["outcomes"][next(iter(outcomes))] = "failed"
    with pytest.raises(ShardError, match="parallel outcomes differ"):
        reconcile_os(plan, "ubuntu", results, serial)


def test_database_template_copy_matches_schema_and_remains_immutable(
    metronome_empty_database_template, metronome_fresh_database, tmp_path
):
    with contextlib.closing(sqlite3.connect(metronome_empty_database_template)) as template_db:
        template_schema = template_db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    with contextlib.closing(sqlite3.connect(metronome_fresh_database)) as copy_db:
        copy_schema = copy_db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        copy_db.execute("INSERT INTO app_settings(key,value) VALUES ('template-test','copy-only')")
        copy_db.commit()
    assert copy_schema == template_schema
    second = tmp_path / "second.db"
    second.write_bytes(metronome_empty_database_template.read_bytes())
    with contextlib.closing(sqlite3.connect(second)) as second_db:
        assert second_db.execute("SELECT value FROM app_settings WHERE key='template-test'").fetchone() is None


def test_database_copy_uses_a_test_owned_flow_root(
    metronome_fresh_database, tmp_path
):
    root = Path(os.environ["DG_FLOWS_ROOT"])
    assert root.is_dir()
    assert tmp_path.name in root.parts
    marker = root / "test-owned.txt"
    marker.write_text("isolated", encoding="utf-8")
    assert marker.read_text(encoding="utf-8") == "isolated"


def test_duration_update_aggregates_every_junit_file_and_rejects_missing_shards(tmp_path):
    files = [f"tests/test_{index}.py" for index in range(1, 7)]
    for index, path in enumerate(files, start=1):
        destination = tmp_path / f"python-windows-{index}" / "junit.xml"
        destination.parent.mkdir()
        suite = ET.Element("testsuite")
        ET.SubElement(
            suite,
            "testcase",
            classname=path.removesuffix(".py").replace("/", "."),
            name="test_case",
            time=str(index / 10),
        )
        ET.ElementTree(suite).write(destination, encoding="unicode")
    assert junit_file_weights(tmp_path, "windows", files) == {
        path: round(index / 10, 3) for index, path in enumerate(files, start=1)
    }
    (tmp_path / "python-windows-6" / "junit.xml").unlink()
    with pytest.raises(ValueError, match="5 JUnit files"):
        junit_file_weights(tmp_path, "windows", files)
