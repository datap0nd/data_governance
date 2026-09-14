from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci.pytest_shard import partition
from tools.ci.shard_audit import audit, main


ROOT = Path(__file__).resolve().parents[1]
IDS = ["tests/test_a.py::test_one", "tests/test_a.py::test_two", "tests/test_b.py::test_three"]


def manifests(platform="linux"):
    return [dict(schema_version=1, revision="abc", platform=platform, index=index,
                 count=2, collected=list(IDS), selected=selected, finished=list(selected), exit_code=0)
            for index, selected in enumerate(partition(IDS, 2))]


def test_partition_preserves_every_test_once_and_keeps_file_fixtures_together():
    ids = [f"tests/test_{file}.py::test_{case}" for file, size in enumerate((20, 8, 7, 4, 1))
           for case in range(size)]
    groups = partition(ids, 2)
    assert set(groups[0]).isdisjoint(groups[1])
    assert sorted(groups[0] + groups[1]) == sorted(ids)
    assert abs(len(groups[0]) - len(groups[1])) <= 1
    for filename in {nodeid.split("::")[0] for nodeid in ids}:
        assert sum(any(nodeid.startswith(filename + "::") for nodeid in group) for group in groups) == 1
    # Assignment does not depend on module collection order.
    assert [set(group) for group in partition(list(reversed(ids)), 2)] == [set(group) for group in groups]


@pytest.mark.parametrize("ids,count", [([], 2), (["a", "a"], 2), (["a"], 0)])
def test_partition_rejects_invalid_collection(ids, count):
    with pytest.raises(ValueError):
        partition(ids, count)


def test_audit_accepts_full_coverage_per_platform():
    assert audit(manifests() + manifests("win32"), platforms=["linux", "win32"], count=2,
                 revision="abc") == {"linux": 3, "win32": 3}


@pytest.mark.parametrize("damage", [
    "missing_shard", "duplicate_shard", "wrong_revision", "failed", "unfinished", "overlap",
    "missing_test", "changed_collection", "duplicate_test", "unexpected_platform", "wrong_count",
])
def test_audit_rejects_incomplete_or_invalid_evidence(damage):
    runs = copy.deepcopy(manifests())
    if damage == "missing_shard":
        runs.pop()
    elif damage == "duplicate_shard":
        runs.append(copy.deepcopy(runs[0]))
    elif damage == "wrong_revision":
        runs[0]["revision"] = "old"
    elif damage == "failed":
        runs[0]["exit_code"] = 1
    elif damage == "unfinished":
        runs[0]["finished"].pop()
    elif damage == "overlap":
        runs[1]["selected"].append(IDS[0])
        runs[1]["finished"].append(IDS[0])
    elif damage == "missing_test":
        runs[0]["selected"].pop()
        runs[0]["finished"].pop()
    elif damage == "changed_collection":
        runs[1]["collected"].append("tests/test_new.py::test_new")
    elif damage == "duplicate_test":
        runs[0]["collected"].append(IDS[0])
    elif damage == "unexpected_platform":
        runs[0]["platform"] = "other"
    elif damage == "wrong_count":
        runs[0]["count"] = 3
    with pytest.raises(ValueError):
        audit(runs, platforms=["linux"], count=2, revision="abc")


def test_audit_cli_rejects_missing_and_malformed_artifacts(tmp_path):
    args = ["--root", str(tmp_path), "--platforms", "linux", "--count", "2", "--revision", "abc"]
    assert main(args) == 1
    (tmp_path / "shard-manifest.json").write_text("{bad json", encoding="utf-8")
    assert main(args) == 1


def run_shard(fixture: Path, index: int, *extra: str):
    environment = dict(os.environ, PYTHONPATH=str(ROOT), GITHUB_SHA="fixture-revision")
    # Parent CI shard options must never constrain a synthetic child run.
    environment.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(fixture), "-q", "-p", "tools.ci.pytest_shard",
         f"--ci-shard-index={index}", "--ci-shard-count=2",
         f"--ci-shard-manifest={fixture / ('manifest-' + str(index) + '.json')}", *extra],
        cwd=fixture, env=environment, text=True, capture_output=True, timeout=60, check=False,
    )


def test_actual_pytest_runs_partition_parameterized_and_skipped_tests(tmp_path):
    (tmp_path / "test_a.py").write_text(
        "import pytest\n@pytest.mark.parametrize('value', [1, 2, 3])\n"
        "def test_values(value): assert value > 0\n"
        "@pytest.mark.skip(reason='synthetic skip')\ndef test_skip(): pass\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("def test_other(): assert True\n", encoding="utf-8")
    runs = []
    for index in range(2):
        completed = run_shard(tmp_path, index)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        runs.append(json.loads((tmp_path / f"manifest-{index}.json").read_text()))
    assert audit(runs, platforms=[sys.platform], count=2, revision="fixture-revision") == {sys.platform: 5}


def test_pytest_failure_remains_a_failure_and_audit_rejects_it(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_bad(): assert False\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("def test_good(): assert True\n", encoding="utf-8")
    completed = run_shard(tmp_path, 0)
    assert completed.returncode == 1
    manifest = json.loads((tmp_path / "manifest-0.json").read_text())
    assert manifest["finished"] == manifest["selected"]
    with pytest.raises(ValueError, match="unsuccessful"):
        audit([manifest], platforms=[sys.platform], count=2, revision="fixture-revision")


def test_invalid_shard_fails_before_tests(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_good(): assert True\n", encoding="utf-8")
    assert run_shard(tmp_path, 2).returncode == 4
