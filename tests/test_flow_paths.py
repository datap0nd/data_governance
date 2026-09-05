import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import database, flow_paths, flow_worker
from app.routers import flows, system_paths
from test_flows import flow_db, _request, _seed_catalog, _flow


def test_containment_rejects_ambiguous_paths_and_sibling_prefix(tmp_path):
    root = tmp_path / "root"
    assert flow_paths.is_inside(str(root / "Web" / "new"), str(root))
    for bad in [root, tmp_path / "root-other" / "a", str(root / "a") + "/../b", "C:relative", r"\\.\PhysicalDrive0"]:
        assert not flow_paths.is_inside(str(bad), str(root))
    with pytest.raises(ValueError):
        flow_paths.validate_root(Path(tmp_path.anchor).as_posix())
    with pytest.raises(ValueError):
        flow_paths.validate_root(str(Path(flow_paths.__file__).parent / "flows"))


def test_containment_resolves_linked_existing_ancestor(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    assert not flow_paths.is_inside(str(root / "escape" / "missing"), str(root))
    with pytest.raises(ValueError, match="Source folder"):
        flow_paths.validate_flow({"target_folder": str(root / "escape" / "target")},
            {"flows_root": str(root), "source_folder": "escape"})


@pytest.mark.skipif(os.name != "nt", reason="Native Windows path handling")
def test_windows_extended_paths_and_alternate_streams(tmp_path):
    assert flow_paths.is_inside("\\\\?\\" + str(tmp_path / "child"), str(tmp_path))
    assert not flow_paths.is_inside(str(tmp_path / "file:stream"), str(tmp_path))
    assert flow_paths.clean_absolute(r"\\?\UNC\server\share\child") == r"\\server\share\child"


def test_setting_root_does_not_enable_enforcement_and_diagnostics_do_not_probe(flow_db, tmp_path, monkeypatch):
    site, report = _seed_catalog()
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    root = str(tmp_path / "managed")
    state = system_paths.put_paths(system_paths.PathsWrite(flows_root=root, create=True), _request())
    assert not state["enforced"] and len(state["source_folders"]) == 5
    assert (Path(root) / "Web").is_dir()
    assert state["flows_outside_root"][0]["id"] == saved["id"]
    with database.get_db() as db:
        assert "paths" not in flows._build_job(db, saved["id"])
    monkeypatch.setattr(os.path, "realpath", lambda *a, **k: (_ for _ in ()).throw(AssertionError("disk probe")))
    assert system_paths.get_paths()["flows_root"] == root


def test_enforcement_rejects_save_and_queue_and_freezes_valid_policy(flow_db, tmp_path):
    site, report = _seed_catalog()
    saved = flows.create_flow(_flow(site["id"], report["id"]), _request())
    root = tmp_path / "managed"
    system_paths.put_paths(system_paths.PathsWrite(flows_root=str(root), create=True, enforced=True), _request())
    with pytest.raises(HTTPException, match="Target folder"):
        flows.create_flow(_flow(site["id"], report["id"], name="Outside"), _request())
    with database.get_db() as db, pytest.raises(HTTPException, match="Target folder"):
        flows.queue_flow_run_service(db, saved["id"], requested_by=None, trigger_type="pipeline")
    valid = flows.create_flow(_flow(site["id"], report["id"], name="Inside", target_folder=str(root / "Web" / "target")), _request())
    with database.get_db() as db:
        run_id, job = flows.queue_flow_run_service(db, valid["id"], requested_by=None, trigger_type="manual")
    assert job["paths"]["flows_root"] == str(root)
    with pytest.raises(HTTPException) as exc:
        system_paths.put_paths(system_paths.PathsWrite(flows_root=str(tmp_path / "other")), _request())
    assert exc.value.status_code == 409
    assert system_paths.get_paths()["flows_root"] == str(root)


def test_worker_checks_policy_before_source_access(tmp_path):
    job = {"paths": {"version": 1, "flows_root": str(tmp_path / "root"), "source_folder": "Local", "enforced": True},
           "flow": {"source_type": "file"}, "local_file": {"path": str(tmp_path / "external.csv")}}
    with pytest.raises(ValueError, match="Source file"):
        flow_worker.execute_local_file_job(job, lambda *a: None, tmp_path, run_id=1, register_folder=lambda *a: {})
    # Legacy jobs do not inherit a new policy merely because code was updated.
    flow_paths.assert_job_paths({"flow": {"source_type": "file"}})


def test_scheduler_skips_outside_flow_without_blocking_other_schedules(flow_db, tmp_path, monkeypatch):
    monkeypatch.setattr(flows, "launch_local_worker", lambda *a, **k: {})
    site, report = _seed_catalog()
    bad = flows.create_flow(_flow(site["id"], report["id"]), _request())
    root = tmp_path / "managed"
    good = flows.create_flow(_flow(site["id"], report["id"], name="Valid schedule", target_folder=str(root / "Web" / "target")), _request())
    system_paths.put_paths(system_paths.PathsWrite(flows_root=str(root), enforced=True), _request())
    with database.get_db() as db:
        db.execute("UPDATE flows SET next_run_at='2000-01-01T00:00:00'")
    flows.queue_due_flows()
    with database.get_db() as db:
        rows = db.execute("SELECT flow_id FROM flow_runs").fetchall()
        assert [row[0] for row in rows] == [good["id"]]
        assert "Target folder" in db.execute("SELECT last_error FROM flows WHERE id=?", (bad["id"],)).fetchone()[0]


def test_transform_upload_stays_in_unique_root_staging(flow_db, tmp_path):
    import asyncio
    from io import BytesIO
    from fastapi import UploadFile
    root = tmp_path / "managed"
    system_paths.put_paths(system_paths.PathsWrite(flows_root=str(root)), _request())
    def upload(name):
        return asyncio.run(flows.add_transform_script(_request(), UploadFile(filename=name, file=BytesIO(b"print('test')"))))
    first, second = upload(r"C:\fakepath\clean.py"), upload("clean.py")
    assert first["script_path"] != second["script_path"]
    assert flow_paths.is_inside(first["script_path"], str(root / ".metronome" / "uploads"))
    assert Path(first["script_path"]).read_bytes() == b"print('test')"
    with pytest.raises(HTTPException):
        upload("CON.py")
