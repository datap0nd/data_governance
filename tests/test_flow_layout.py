import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import database, flow_layout, flow_paths
from app.routers import flows
from test_flows import flow_db, _request, _seed_catalog, _flow


@pytest.mark.parametrize("source", ["portal", "outlook", "file"])
def test_new_flow_without_target_creates_owned_layout(flow_db, tmp_path, source):
    if source == "portal":
        site, report = _seed_catalog()
        body = _flow(site["id"], report["id"], target_folder=None)
    else:
        body = flows.FlowWrite(name="New flow", source_type=source,
            local_file_path=str(tmp_path / "input.csv"), outlook_subject_contains="Report")
    saved = flows.create_flow(body, _request())
    folder = Path(saved["flow_folder"])
    assert saved["folder_state"] == "managed"
    assert (folder / "Downloads").is_dir() and (folder / "Scripts").is_dir()
    assert flow_layout.read_manifest(folder, saved["id"])["flow_name"] == saved["name"]
    assert saved["target_folder"] == (None if source == "file" else str(folder / "Downloads"))
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
    flow_paths.assert_job_paths(job)
    assert job["flow"]["folder"] == str(folder)
    if source == "file":
        assert job["downloads"]["target_folder"].startswith("metronome-private://")


def test_managed_rename_preserves_folder_and_delete_preserves_files(flow_db):
    site, report = _seed_catalog()
    saved = flows.create_flow(_flow(site["id"], report["id"], target_folder=None, enabled=False), _request())
    original = Path(saved["flow_folder"])
    (original / "Downloads" / "user.csv").write_text("preserve")
    edited = flows.update_flow(saved["id"], _flow(site["id"], report["id"], name="Renamed", enabled=False, target_folder="C:\\ignored"), _request())
    assert edited["flow_folder"] == saved["flow_folder"]
    assert edited["target_folder"] == str(original / "Downloads")
    assert flow_layout.read_manifest(original, saved["id"])["flow_name"] == "Renamed"
    flows.delete_flow(saved["id"], flows.FlowDeleteWrite(confirmation="Renamed"), _request())
    assert (original / "Downloads" / "user.csv").read_text() == "preserve"
    assert flow_layout.read_manifest(original, saved["id"])["deleted_at"]


def test_adoption_keeps_historic_target_and_is_idempotent(flow_db, tmp_path):
    site, report = _seed_catalog()
    old = tmp_path / "old"
    old.mkdir(); (old / "history.csv").write_text("history")
    saved = flows.create_flow(_flow(site["id"], report["id"], target_folder=str(old)), _request())
    # Simulate a pre-managed installation; new API creations always allocate.
    with database.get_db() as db:
        db.execute("UPDATE flows SET name=?, flow_folder=NULL, target_folder=? WHERE id=?", ("Legacy flow", str(old), saved["id"]))
    adopted = flows.adopt_flow_folder(saved["id"], _request())
    assert adopted["previous_target_folder"] == str(old)
    assert (old / "history.csv").read_text() == "history"
    assert flows.adopt_flow_folder(saved["id"], _request())["flow_folder"] == adopted["flow_folder"]


def test_adoption_refuses_active_run(flow_db):
    site, report = _seed_catalog()
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    with database.get_db() as db:
        flows.queue_flow_run_service(db, saved["id"], requested_by=None, trigger_type="manual")
    with pytest.raises(HTTPException, match="active run"):
        flows.adopt_flow_folder(saved["id"], _request())


def test_creation_failure_rolls_back_database_and_only_empty_owned_files(flow_db, tmp_path, monkeypatch):
    site, report = _seed_catalog()
    real_mkdir = Path.mkdir
    def fail_scripts(path, *args, **kwargs):
        if path.name == "Scripts":
            raise PermissionError("simulated")
        return real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", fail_scripts)
    with pytest.raises(HTTPException, match="simulated"):
        flows.create_flow(_flow(site["id"], report["id"], target_folder=None), _request())
    with database.get_db() as db:
        assert not db.execute("SELECT 1 FROM flows").fetchone()
        source = Path(flow_paths.get_flows_root(db)) / "Web"
    assert not list(source.iterdir())


def test_layout_refuses_foreign_marker_and_cleanup_preserves_user_content(tmp_path):
    folder = flow_layout.create_flow_folder(str(tmp_path / "root"), "web_export", "CON.py", 1)
    assert folder.name == "Flow CON.py (id 1)"
    assert "/" not in flow_layout.flow_folder_slug("bad/name", 2)
    with pytest.raises(ValueError, match="another flow"):
        flow_layout.read_manifest(folder, 2)
    (folder / "Scripts" / "mine.py").write_text("keep")
    flow_layout.cleanup_empty_creation(folder, 1)
    assert (folder / "Scripts" / "mine.py").read_text() == "keep"
    with pytest.raises(FileExistsError):
        flow_layout.create_flow_folder(str(tmp_path / "root"), "web_export", "CON.py", 1)


def test_database_failure_after_allocation_compensates_empty_folder(flow_db, monkeypatch):
    from contextlib import contextmanager
    site, report = _seed_catalog()
    with database.get_db() as db:
        source = Path(flow_paths.get_flows_root(db)) / "Web"
    @contextmanager
    def fail_commit():
        with database.get_db() as db:
            yield db
            raise OSError("simulated commit failure")
    monkeypatch.setattr(flows, "get_db", fail_commit)
    with pytest.raises(HTTPException, match="commit failure"):
        flows.create_flow(_flow(site["id"], report["id"], target_folder=None), _request())
    with database.get_db() as db:
        assert not db.execute("SELECT 1 FROM flows").fetchone()
    assert not list(source.iterdir())
