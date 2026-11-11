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


# --- Runners get the same minutes, not the same number of tests ----------------------

from tools.ci import record_durations  # noqa: E402
from tools.ci.pytest_shard import DURATIONS_FILE, load_durations  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def _seconds(group, durations):
    return sum(durations[name] for name in {nodeid.split("::")[0] for nodeid in group})


def test_partition_balances_recorded_time_not_test_count():
    # One slow file with two tests, and six quick files with ten tests each.
    ids = [f"tests/test_slow.py::test_{case}" for case in range(2)]
    ids += [f"tests/test_quick_{file}.py::test_{case}" for file in range(6) for case in range(10)]
    durations = {"tests/test_slow.py": 120.0, **{f"tests/test_quick_{file}.py": 20.0 for file in range(6)}}

    by_time = partition(ids, 2, durations)
    assert sorted(_seconds(group, durations) for group in by_time) == [120.0, 120.0]
    # By count the slow file is paired with half the quick ones: 180 against 60.
    by_count = partition(ids, 2)
    assert sorted(_seconds(group, durations) for group in by_count) == [60.0, 180.0]
    # Every test still runs once, files stay whole, and order does not matter.
    assert sorted(by_time[0] + by_time[1]) == sorted(ids)
    for filename in durations:
        assert sum(any(nodeid.startswith(filename + "::") for nodeid in group) for group in by_time) == 1
    assert [set(group) for group in partition(list(reversed(ids)), 2, durations)] == [set(group) for group in by_time]


def test_a_file_never_timed_counts_its_tests_at_the_average():
    ids = [f"tests/test_known.py::test_{case}" for case in range(10)]
    ids += [f"tests/test_new.py::test_{case}" for case in range(10)]
    ids += [f"tests/test_small.py::test_{case}" for case in range(2)]
    # 10 tests took 50 seconds, so the new file's 10 tests count as 50 too.
    groups = partition(ids, 2, {"tests/test_known.py": 50.0, "tests/test_gone.py": 999.0})
    together = next(group for group in groups if "tests/test_small.py::test_0" in group)
    assert {nodeid.split("::")[0] for nodeid in together} in (
        {"tests/test_small.py", "tests/test_new.py"}, {"tests/test_small.py", "tests/test_known.py"})
    assert len(groups[0]) + len(groups[1]) == len(ids)


def test_unusable_recorded_times_fall_back_to_counting_tests(tmp_path):
    assert load_durations(tmp_path / "absent.json") == {}
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert load_durations(tmp_path / "bad.json") == {}
    (tmp_path / "odd.json").write_text(json.dumps({"files": {"a": "fast", "b": -1, "c": True, "d": 2}}), encoding="utf-8")
    assert load_durations(tmp_path / "odd.json") == {"d": 2.0}
    ids = [f"tests/test_{file}.py::test_{case}" for file, size in enumerate((20, 8, 7, 4, 1)) for case in range(size)]
    assert partition(ids, 2, {}) == partition(ids, 2)


def test_recorded_times_come_from_junit_results(tmp_path):
    for index, cases in enumerate((
        '<testcase classname="tests.test_flows" name="a" time="1.25"/>'
        '<testcase classname="tests.test_flows.TestRuns" name="b" time="0.75"/>',
        '<testcase classname="tests.test_scan" name="c" time="3.0"/>'
        '<testcase classname="conftest" name="d" time="9.0"/>',
    )):
        (tmp_path / f"shard-{index}.xml").write_text(
            f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>', encoding="utf-8")
    output = tmp_path / "durations.json"
    assert record_durations.main(["--run", "a test", "--output", str(output),
                                  str(tmp_path / "shard-0.xml"), str(tmp_path / "shard-1.xml")]) == 0
    assert load_durations(output) == {"tests/test_flows.py": 2.0, "tests/test_scan.py": 3.0}
    assert json.loads(output.read_text())["recorded_from"] == "a test"


def _workflow_shard_counts():
    import re

    text = WORKFLOW.read_text(encoding="utf-8")
    matrix = re.search(r"(?m)^\s+shard: \[([0-9, ]+)\]", text).group(1).replace(" ", "").split(",")
    return matrix, int(re.search(r"--ci-shard-count=(\d+)", text).group(1)), int(
        re.search(r"shard_audit\.py --root \.ci-results --count (\d+)", text).group(1))


def test_workflow_names_one_shard_count_in_all_three_places():
    matrix, run_count, audit_count = _workflow_shard_counts()
    assert matrix == [str(index) for index in range(len(matrix))]
    assert len(matrix) == run_count == audit_count
    assert run_count >= 4, "fewer runners makes every pull request wait longer"


def test_the_recorded_run_splits_evenly_across_the_workflows_runners():
    durations = load_durations(DURATIONS_FILE)
    assert len(durations) > 100 and sum(durations.values()) > 600
    count = _workflow_shard_counts()[1]
    ids = [f"{name}::test_{case}" for name in durations for case in range(2)]
    loads = [_seconds(group, durations) for group in partition(ids, count, durations)]
    average = sum(loads) / count
    # The slowest runner sets how long a pull request waits.
    assert max(loads) <= average * 1.10, loads
    assert max(durations.values()) <= average, "one file is longer than a whole runner's share"


def test_actual_pytest_run_uses_the_recorded_times_it_is_given(tmp_path):
    (tmp_path / "test_slow.py").write_text("def test_one(): assert True\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("def test_two(): assert True\ndef test_three(): assert True\n", encoding="utf-8")
    (tmp_path / "test_c.py").write_text("def test_four(): assert True\ndef test_five(): assert True\n", encoding="utf-8")
    recorded = tmp_path / "recorded.json"
    recorded.write_text(json.dumps({"files": {"test_slow.py": 60, "test_b.py": 30, "test_c.py": 30}}), encoding="utf-8")
    runs = []
    for index in range(2):
        completed = run_shard(tmp_path, index, f"--ci-shard-durations={recorded}")
        assert completed.returncode == 0, completed.stdout + completed.stderr
        runs.append(json.loads((tmp_path / f"manifest-{index}.json").read_text()))
    # The one slow test runs alone; by count it would have shared a runner.
    assert sorted(len(run["selected"]) for run in runs) == [1, 4]
    assert audit(runs, platforms=[sys.platform], count=2, revision="fixture-revision") == {sys.platform: 5}
