import json
import os
from pathlib import Path

import pytest

from app import flow_publish, flow_worker


def _artifact(path: Path, *, index: int, count: int) -> dict:
    metadata = flow_publish.read_size_checksum(path)
    return {
        "period_key": [f"2026-W{29 + index:02d}"],
        "export_view": None,
        "bundle_index": index,
        "bundle_count": count,
        "status": "saved",
        "file_path": str(path),
        "filename": path.name,
        "file_size": metadata["file_size"],
        "checksum": metadata["checksum"],
        "deliverable_file_path": str(path),
        "deliverable_filename": path.name,
        "deliverable_file_size": metadata["file_size"],
        "deliverable_checksum": metadata["checksum"],
        "storage_scope": "worker_private",
        "artifact_store_id": "store-a",
    }


def test_private_target_root_is_stable_and_ownership_checked(tmp_path):
    assert flow_publish.normalize_target_path("") == ""
    profile = tmp_path / "profile"
    first = flow_publish.private_target_root(profile, Path(r"C:\Reports\Daily"))
    second = flow_publish.private_target_root(profile, Path(r"c:/reports/daily/"))

    assert first == second
    marker = json.loads((first / flow_publish.TARGET_MARKER).read_text(encoding="utf-8"))
    assert marker["target_key"] == r"c:\reports\daily"

    marker["target_key"] = r"c:\other"
    (first / flow_publish.TARGET_MARKER).write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(RuntimeError, match="different target"):
        flow_publish.private_target_root(profile, Path(r"C:\Reports\Daily"))


def test_publish_bundle_replaces_matching_files_and_cleans_owned_debris(tmp_path):
    target = tmp_path / "target"
    private = tmp_path / "private"
    target.mkdir()
    private.mkdir()
    old = target / "stable.xlsx"
    old.write_bytes(b"old workbook")
    source = private / "stable.xlsx"
    source.write_bytes(b"new validated workbook")

    result = flow_publish.publish_bundle(target, 41, [_artifact(source, index=1, count=1)])

    assert old.read_bytes() == b"new validated workbook"
    assert result[0]["published_file_path"] == str(old)
    assert not list(target.glob(".metronome-publish-*"))


def test_publish_bundle_rolls_back_an_earlier_file_when_a_later_replace_fails(
    tmp_path, monkeypatch,
):
    target = tmp_path / "target"
    private = tmp_path / "private"
    target.mkdir()
    private.mkdir()
    destinations = [target / "one.xlsx", target / "two.xlsx"]
    sources = [private / "one.xlsx", private / "two.xlsx"]
    for index, path in enumerate(destinations, start=1):
        path.write_bytes(f"old-{index}".encode())
    for index, path in enumerate(sources, start=1):
        path.write_bytes(f"new-{index}".encode())
    real_replace = os.replace
    failed = False

    def replace(source, destination):
        nonlocal failed
        if not failed and str(source).endswith("-2.tmp") and Path(destination) == destinations[1]:
            failed = True
            raise PermissionError("workbook is open")
        return real_replace(source, destination)

    monkeypatch.setattr(flow_publish.os, "replace", replace)
    with pytest.raises(RuntimeError, match="previous bundle was restored"):
        flow_publish.publish_bundle(
            target, 42,
            [_artifact(sources[0], index=1, count=2), _artifact(sources[1], index=2, count=2)],
        )

    assert [path.read_bytes() for path in destinations] == [b"old-1", b"old-2"]
    assert not list(target.glob(".metronome-publish-*"))


def test_direct_resume_publishes_union_and_materializes_carried_artifact(tmp_path):
    target = tmp_path / "target"
    current_run = tmp_path / "current"
    earlier_run = tmp_path / "earlier"
    target.mkdir()
    current_run.mkdir()
    earlier_run.mkdir()
    carried_path = earlier_run / "week-30.xlsx"
    current_path = current_run / "week-31.xlsx"
    carried_path.write_bytes(b"week 30")
    current_path.write_bytes(b"week 31")
    carried = _artifact(carried_path, index=1, count=2)
    current = _artifact(current_path, index=2, count=2)
    job = {
        "downloads": {
            "output_mode": "direct_replace",
            "target_folder": str(target),
        },
        "resume": {"from_run_id": 10, "completed": [carried]},
        "_runtime_run_folder": str(current_run),
        "_runtime_artifact_store_id": "store-a",
    }
    events = []

    published = flow_worker._publish_direct_artifacts(
        job, [current], run_id=11,
        report_progress=lambda status, detail, artifacts=None: events.append(detail["stage"]),
    )

    assert [item["published_filename"] for item in published] == [
        "week-30.xlsx", "week-31.xlsx",
    ]
    assert (target / "week-30.xlsx").read_bytes() == b"week 30"
    assert (target / "week-31.xlsx").read_bytes() == b"week 31"
    assert Path(published[0]["file_path"]).parent == current_run
    assert events == ["direct_publish", "publish_complete"]


def test_publish_rejects_non_regular_destination(tmp_path):
    target = tmp_path / "target"
    private = tmp_path / "private"
    target.mkdir()
    private.mkdir()
    source = private / "stable.xlsx"
    source.write_bytes(b"new")
    (target / "stable.xlsx").mkdir()

    with pytest.raises(RuntimeError, match="not a regular file"):
        flow_publish.publish_bundle(target, 43, [_artifact(source, index=1, count=1)])


def test_direct_run_folder_is_registered_under_the_private_hashed_parent(tmp_path):
    target = tmp_path / "target"
    profile = tmp_path / "profile"
    target.mkdir()
    registered = []
    job = {
        "flow": {"id": 7},
        "downloads": {
            "output_mode": "direct_replace",
            "target_folder": str(target),
        },
    }

    run_folder = flow_worker._prepare_run_folder(
        job,
        profile,
        run_id=44,
        register_folder=lambda path: registered.append(path) or {"ops": []},
        report_progress=lambda *_args, **_kwargs: None,
    )

    private_parent = flow_publish.private_target_root(profile, target)
    assert run_folder.parent == private_parent
    assert registered == [str(run_folder)]
    assert run_folder.name.startswith("#44_")
    assert job["_runtime_run_folder"] == str(run_folder)


def test_locked_rollback_retains_only_owned_journal_backup_and_temp(tmp_path, monkeypatch):
    target = tmp_path / "target"
    private = tmp_path / "private"
    target.mkdir()
    private.mkdir()
    destination = target / "stable.xlsx"
    destination.write_bytes(b"old")
    source = private / "stable.xlsx"
    source.write_bytes(b"new")
    real_replace = os.replace

    def replace(source_path, destination_path):
        source_path = Path(source_path)
        if source_path.name.endswith("-1.tmp"):
            raise PermissionError("destination is open")
        if source_path.name.endswith("-1.bak"):
            raise PermissionError("restore is blocked")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(flow_publish.os, "replace", replace)
    with pytest.raises(RuntimeError, match="Rollback also failed"):
        flow_publish.publish_bundle(target, 45, [_artifact(source, index=1, count=1)])

    assert (target / ".metronome-publish-45.json").is_file()
    assert (target / ".metronome-publish-45-1.bak").read_bytes() == b"old"
    assert (target / ".metronome-publish-45-1.tmp").read_bytes() == b"new"


def test_unowned_publish_journal_blocks_reconciliation_without_touching_user_files(tmp_path):
    target = tmp_path / "target"
    private = tmp_path / "private"
    target.mkdir()
    private.mkdir()
    stable = target / "stable.xlsx"
    stable.write_bytes(b"user file")
    source = private / "stable.xlsx"
    source.write_bytes(b"new")
    (target / ".metronome-publish-99.json").write_text(
        json.dumps({"owner": "someone-else"}), encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="ownership marker"):
        flow_publish.publish_bundle(target, 46, [_artifact(source, index=1, count=1)])

    assert stable.read_bytes() == b"user file"


def test_similarly_named_staging_file_without_journal_is_never_deleted(tmp_path):
    target = tmp_path / "target"
    private = tmp_path / "private"
    target.mkdir()
    private.mkdir()
    source = private / "stable.xlsx"
    source.write_bytes(b"new")
    lookalike = target / ".metronome-publish-48-1.tmp"
    lookalike.write_bytes(b"belongs to user")

    with pytest.raises(RuntimeError, match="nothing was removed"):
        flow_publish.publish_bundle(target, 48, [_artifact(source, index=1, count=1)])

    assert lookalike.read_bytes() == b"belongs to user"
    assert not (target / ".metronome-publish-48.json").exists()


def test_reconciliation_removes_new_file_after_crash_before_install_was_journaled(
    tmp_path, monkeypatch,
):
    target = tmp_path / "target"
    private = tmp_path / "private"
    target.mkdir()
    private.mkdir()
    source = private / "new.csv"
    source.write_bytes(b"new")
    real_write = flow_publish._write_journal
    writes = 0

    def crash_before_third_write(path, journal):
        nonlocal writes
        writes += 1
        if writes == 3:
            raise SystemExit("simulated process crash")
        return real_write(path, journal)

    monkeypatch.setattr(flow_publish, "_write_journal", crash_before_third_write)
    with pytest.raises(SystemExit, match="simulated process crash"):
        flow_publish.publish_bundle(target, 49, [_artifact(source, index=1, count=1)])
    assert (target / "new.csv").read_bytes() == b"new"

    monkeypatch.setattr(flow_publish, "_write_journal", real_write)
    reconciled = flow_publish.reconcile_target(target)

    assert reconciled == [{"run_id": 49, "outcome": "rolled_back"}]
    assert not (target / "new.csv").exists()
    assert not list(target.glob(".metronome-publish-*"))


def test_reconciliation_promotes_fsynced_journal_scratch_after_rename_crash(
    tmp_path, monkeypatch,
):
    target = tmp_path / "target"
    private = tmp_path / "private"
    target.mkdir()
    private.mkdir()
    source = private / "stable.csv"
    source.write_bytes(b"new")
    real_replace = os.replace
    crashed = False

    def replace(source_path, destination_path):
        nonlocal crashed
        source_path = Path(source_path)
        destination_path = Path(destination_path)
        if (
            not crashed
            and source_path.name == ".metronome-publish-50.json.tmp"
            and destination_path.name == ".metronome-publish-50.json"
        ):
            crashed = True
            raise OSError("journal rename interrupted")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(flow_publish.os, "replace", replace)
    with pytest.raises(OSError, match="journal rename interrupted"):
        flow_publish.publish_bundle(target, 50, [_artifact(source, index=1, count=1)])
    scratch = target / ".metronome-publish-50.json.tmp"
    assert scratch.is_file()

    monkeypatch.setattr(flow_publish.os, "replace", real_replace)
    assert flow_publish.reconcile_target(target) == [
        {"run_id": 50, "outcome": "rolled_back"},
    ]
    assert not scratch.exists()


@pytest.mark.parametrize(
    ("primary_name", "original_name", "expected_name"),
    [
        ("report_normalized.csv", "report.xlsx", "report.xlsx"),
        ("report.csv", "report_raw.csv", "report.csv"),
        ("report.html", "report.html", "report.html"),
        ("report.txt", "report.txt", "report.txt"),
    ],
)
def test_direct_deliverable_selection_publishes_only_the_configured_output(
    tmp_path, primary_name, original_name, expected_name,
):
    profile = tmp_path / "profile"
    private = tmp_path / "private"
    private.mkdir()
    primary = private / primary_name
    primary.write_bytes(b"primary")
    original = private / original_name
    if original != primary:
        original.write_bytes(b"original")
    artifact = {
        "file_path": str(primary),
        "filename": primary.name,
        "file_size": len(b"primary"),
        "checksum": flow_publish.read_size_checksum(primary)["checksum"],
        "original_file_path": str(original),
        "original_filename": original.name,
        "status": "saved",
    }

    flow_worker._decorate_artifact_storage(
        artifact, {"downloads": {"output_mode": "direct_replace"}}, profile,
    )

    assert artifact["deliverable_filename"] == expected_name
    assert artifact["storage_scope"] == "worker_private"
    assert artifact["original_checksum"] == flow_publish.read_size_checksum(original)["checksum"]


def test_corrupt_or_foreign_resume_artifact_is_downloaded_again(tmp_path):
    carried_path = tmp_path / "carried.csv"
    carried_path.write_bytes(b"before")
    carried = _artifact(carried_path, index=1, count=1)
    job = {
        "downloads": {"output_mode": "direct_replace"},
        "resume": {"completed": [carried]},
        "_runtime_artifact_store_id": "store-a",
    }
    assert flow_worker._resume_completed_keys(job)

    carried_path.write_bytes(b"corrupt")
    assert flow_worker._resume_completed_keys(job) == set()
    carried_path.write_bytes(b"before")
    job["_runtime_artifact_store_id"] = "store-b"
    assert flow_worker._resume_completed_keys(job) == set()


def test_incomplete_direct_bundle_publishes_nothing(tmp_path):
    target = tmp_path / "target"
    current_run = tmp_path / "current"
    target.mkdir()
    current_run.mkdir()
    only = current_run / "one.csv"
    only.write_bytes(b"one")
    job = {
        "downloads": {"output_mode": "direct_replace", "target_folder": str(target)},
        "_runtime_run_folder": str(current_run),
        "_runtime_bundle_count": 2,
        "_runtime_artifact_store_id": "store-a",
    }

    with pytest.raises(RuntimeError, match="1 of 2"):
        flow_worker._publish_direct_artifacts(
            job, [_artifact(only, index=1, count=2)], run_id=47,
            report_progress=lambda *_args, **_kwargs: None,
        )

    assert list(target.iterdir()) == []


def test_direct_publish_occurs_before_transformation_and_sql_in_worker_contract():
    source = Path(flow_worker.__file__).read_text(encoding="utf-8")
    assert 'execute_flow(page, run["job"]' in source[source.index("def run_worker("):]
    run_loop = source[source.index("def execute_flow("):source.index("def run_worker(")]
    publish = run_loop.index("artifacts = _publish_direct_artifacts(")
    transform = run_loop.index("sql_artifacts = _run_transformations(")
    sql = run_loop.index("sql_result = load_artifacts(")
    assert publish < transform < sql
