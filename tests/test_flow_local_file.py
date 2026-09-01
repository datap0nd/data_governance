import base64
import csv
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import database, flow_publish, flow_worker
from app.ai.operations_tools import _safe_text
from app.routers import flows, pipelines


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def local_file_db(tmp_path, monkeypatch):
    db_path = tmp_path / "local-file-flows.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return db_path


def _request(actor="Analyst"):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def _file_flow(path: Path, **overrides):
    values = {
        "name": "Filesystem input",
        "source_type": "file",
        "local_file_path": str(path),
        "schedule_type": "daily",
        "schedule_time": "08:00",
    }
    if path.suffix.casefold() != ".csv":
        values["local_file_worksheet"] = "Data"
    values.update(overrides)
    return flows.FlowWrite(**values)


def _worker_job(source: Path, profile: Path, *, worksheet=None, previous=None, force=False):
    storage_key = flow_publish.new_local_file_storage_key()
    return {
        "schema_version": 3,
        "execution": {"required_adapter": "local_file", "browser_mode": "headless"},
        "flow": {"id": 17, "name": "Filesystem input", "source_type": "file"},
        "site": {"adapter": "local_file"},
        "report": {"id": 9},
        "local_file": {
            "enabled": True,
            "path": str(source),
            "normalized_path": flow_publish.normalize_target_path(source),
            "worksheet": worksheet,
            "config_revision": 1,
            "previous_identity": previous,
            "force_reprocess": force,
            "private_store_key": storage_key,
        },
        "downloads": {
            "target_folder": storage_key,
            "filename_template": "{original}",
            "output_mode": "private_snapshot",
            "file_format": "auto",
        },
        "transformation": {"enabled": False},
        "sql_handoff": {"enabled": False},
    }


def test_file_flow_uses_hidden_anchor_private_store_and_v3_job(local_file_db, tmp_path):
    source = tmp_path / "daily.csv"
    source.write_text("Code,Units\nA,7\n", encoding="utf-8")

    saved = flows.create_flow(_file_flow(source), _request())

    assert saved["source_type"] == "file"
    assert saved["source_adapter"] == "local_file"
    assert saved["target_folder"] is None
    assert saved["filename_template"] == "{original}"
    assert saved["output_mode"] == "private_snapshot"
    assert saved["local_file_config_revision"] == 1
    assert all(site["adapter"] not in {"local_file", "outlook_attachment"} for site in flows.catalog()["sites"])

    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        storage_key = db.execute(
            "SELECT target_folder FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()["target_folder"]
        assert db.execute(
            "SELECT COUNT(*) AS count FROM flow_file_source_bindings WHERE flow_id=?",
            (saved["id"],),
        ).fetchone()["count"] == 0
    assert job["schema_version"] == 3
    assert job["execution"]["required_adapter"] == "local_file"
    assert job["local_file"]["path"] == str(source)
    assert storage_key.startswith(flow_publish.LOCAL_FILE_STORAGE_PREFIX)
    assert job["local_file"]["private_store_key"] == storage_key
    assert pipelines.flow_target_resource_key_from_job(job) is None
    assert pipelines.flow_publish_resource_key_from_job(job) is None


def test_file_flow_api_canonicalizes_csv_and_rejects_unsafe_paths(tmp_path):
    csv_flow = flows.FlowWrite(
        name="CSV", source_type="file", local_file_path=str(tmp_path / "input.csv"),
        local_file_worksheet="Must be discarded", target_folder=r"C:\client\target",
        filename_template="client.csv", output_mode="direct_replace",
    )
    assert csv_flow.local_file_worksheet is None
    assert csv_flow.target_folder is None
    assert csv_flow.filename_template == "{original}"
    assert csv_flow.output_mode == "private_snapshot"

    with pytest.raises(ValueError, match="absolute path"):
        flows.FlowWrite(name="Relative", source_type="file", local_file_path="input.csv")
    with pytest.raises(ValueError, match="without wildcards"):
        flows.FlowWrite(
            name="Wildcard", source_type="file", local_file_path=r"C:\data\*.csv",
        )
    with pytest.raises(ValueError, match="worksheet"):
        flows.FlowWrite(
            name="Workbook", source_type="file",
            local_file_path=r"C:\data\book.xlsx", local_file_worksheet="   ",
        )


def test_file_flow_edit_preserves_store_and_resets_identity(local_file_db, tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("Code\nA\n", encoding="utf-8")
    second.write_text("Code\nB\n", encoding="utf-8")
    saved = flows.create_flow(_file_flow(first), _request())
    with database.get_db() as db:
        storage_key = db.execute(
            "SELECT target_folder FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()["target_folder"]
        db.execute(
            "UPDATE flows SET local_file_last_identity=? WHERE id=?",
            ("a" * 64, saved["id"]),
        )

    updated = flows.update_flow(
        saved["id"], _file_flow(second, name="Filesystem input renamed"), _request(),
    )

    assert updated["target_folder"] is None
    with database.get_db() as db:
        assert db.execute(
            "SELECT target_folder FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()["target_folder"] == storage_key
    assert updated["local_file_config_revision"] == 2
    assert updated["local_file_last_identity"] is None


def test_private_storage_uri_is_redacted_from_user_run_payloads(
    local_file_db, tmp_path, monkeypatch,
):
    source = tmp_path / "daily.csv"
    source.write_text("Code\nA\n", encoding="utf-8")
    saved = flows.create_flow(_file_flow(source), _request())
    monkeypatch.setattr(
        flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode},
    )

    queued = flows.queue_run(saved["id"], _request())
    listed = next(item for item in flows.list_runs(limit=100) if item["id"] == queued["id"])
    detailed = flows.get_run(queued["id"])

    for payload in (queued, listed, detailed):
        assert "job_json" not in payload
        assert "target_folder" not in payload["job"]["downloads"]
        assert "private_store_key" not in payload["job"]["local_file"]
        assert "metronome-private://" not in json.dumps(payload)


def test_local_csv_is_never_modified_and_no_op_creates_no_folder(tmp_path):
    source = tmp_path / "input.csv"
    original = b"Code;Units\r\nA;7\r\n"
    source.write_bytes(original)
    profile = tmp_path / "profile"
    registered = []
    job = _worker_job(source, profile)

    artifacts, _timings, outcome = flow_worker.execute_local_file_job(
        job, lambda *_args, **_kwargs: None, profile,
        run_id=41, register_folder=lambda path: registered.append(path) or {"ops": []},
    )

    assert source.read_bytes() == original
    assert outcome["no_op"] is False
    assert [item["status"] for item in artifacts] == ["source_snapshot", "saved"]
    assert all(item["storage_scope"] == "worker_private" for item in artifacts)
    normalized = Path(outcome["sql_artifacts"][0]["file_path"])
    with normalized.open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.reader(handle)) == [["Code", "Units"], ["A", "7"]]
    assert len(registered) == 1

    no_op_job = _worker_job(
        source, profile, previous=outcome["source_receipt"]["identity"],
    )
    no_op_job["local_file"]["private_store_key"] = job["local_file"]["private_store_key"]
    no_op_job["downloads"]["target_folder"] = job["downloads"]["target_folder"]
    no_op_registered = []
    artifacts, _timings, no_op = flow_worker.execute_local_file_job(
        no_op_job, lambda *_args, **_kwargs: None, profile,
        run_id=42, register_folder=lambda path: no_op_registered.append(path) or {"ops": []},
    )
    assert artifacts == []
    assert no_op["no_op"] is True
    assert no_op_registered == []


def test_local_excel_uses_only_exact_selected_worksheet(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "book.xlsx"
    workbook = openpyxl.Workbook()
    data = workbook.active
    data.title = "Data"
    data.append(["Code", "Units"])
    data.append(["A", 7])
    other = workbook.create_sheet("Other")
    other.append(["Wrong", "Headers", "Here"])
    other.append([1, 2, 3])
    workbook.save(source)
    profile = tmp_path / "profile"

    _artifacts, _timings, outcome = flow_worker.execute_local_file_job(
        _worker_job(source, profile, worksheet="Data"),
        lambda *_args, **_kwargs: None, profile,
        run_id=51, register_folder=lambda _path: {"ops": []},
    )
    assert outcome["sql_artifacts"][0]["source_sheets"] == ["Data"]

    with pytest.raises(RuntimeError, match="was not found exactly once"):
        flow_worker.execute_local_file_job(
            _worker_job(source, tmp_path / "profile-2", worksheet="data"),
            lambda *_args, **_kwargs: None, tmp_path / "profile-2",
            run_id=52, register_folder=lambda _path: {"ops": []},
        )


@pytest.mark.parametrize(
    ("suffix", "fixture", "worksheet", "expected_format"),
    [
        (".xls", "xls", "Data", "xls"),
        (".xlt", "xls", "Data", "xls"),
        (".xlsb", "xlsb", "Sheet1", "xlsb"),
    ],
)
def test_local_file_executes_legacy_and_binary_excel_families(
    tmp_path, suffix, fixture, worksheet, expected_format,
):
    source = tmp_path / f"input{suffix}"
    source.write_bytes(base64.b64decode(
        FIXTURE_DIR.joinpath(f"minimal.{fixture}.b64").read_text(encoding="ascii")
    ))
    profile = tmp_path / "profile"

    artifacts, _timings, outcome = flow_worker.execute_local_file_job(
        _worker_job(source, profile, worksheet=worksheet),
        lambda *_args, **_kwargs: None, profile,
        run_id=53, register_folder=lambda _path: {"ops": []},
    )

    assert artifacts[0]["detected_format"] == expected_format
    assert outcome["sql_artifacts"][0]["source_sheets"] == [worksheet]


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm", ".xltx", ".xltm"])
def test_local_file_executes_every_ooxml_extension(tmp_path, suffix):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    workbook.active.title = " Exact sheet "
    workbook.active.append(["Code", "Units"])
    workbook.active.append(["A", 7])
    payload = BytesIO()
    workbook.save(payload)
    source = tmp_path / f"input{suffix}"
    source.write_bytes(payload.getvalue())
    profile = tmp_path / "profile"

    artifacts, _timings, outcome = flow_worker.execute_local_file_job(
        _worker_job(source, profile, worksheet=" Exact sheet "),
        lambda *_args, **_kwargs: None, profile,
        run_id=54, register_folder=lambda _path: {"ops": []},
    )

    assert artifacts[0]["detected_format"] == "xlsx"
    assert outcome["sql_artifacts"][0]["source_sheets"] == [" Exact sheet "]


def test_local_file_rejects_html_disguised_as_xls(tmp_path):
    source = tmp_path / "report.xls"
    source.write_text("<html><table><tr><td>Code</td></tr></table></html>", encoding="utf-8")
    profile = tmp_path / "profile"
    with pytest.raises(RuntimeError, match="declares xls but its content looks like html"):
        flow_worker.execute_local_file_job(
            _worker_job(source, profile, worksheet="Data"),
            lambda *_args, **_kwargs: None, profile,
            run_id=61, register_folder=lambda _path: {"ops": []},
        )


def test_local_file_receipt_allows_failed_sql_retry_but_blocks_rollback(local_file_db, tmp_path):
    source = tmp_path / "daily.csv"
    source.write_text("Code\nA\n", encoding="utf-8")
    saved = flows.create_flow(_file_flow(source), _request())
    normalized = flows.normalize_target_path(source)
    receipt = {
        "kind": "local_file",
        "identity": "b" * 64,
        "previous_identity": "a" * 64,
        "raw_checksum": "c" * 64,
        "normalized_path": normalized,
        "worksheet": None,
        "config_revision": 1,
        "file_size": 10,
        "modified_at_ns": 1,
    }
    with database.get_db() as db:
        db.execute(
            "UPDATE flows SET local_file_last_identity=? WHERE id=?",
            ("a" * 64, saved["id"]),
        )
        assert flows._local_file_receipt_is_current(db, saved["id"], receipt)
        db.execute(
            "UPDATE flows SET local_file_last_identity=? WHERE id=?",
            ("d" * 64, saved["id"]),
        )
        assert not flows._local_file_receipt_is_current(db, saved["id"], receipt)


def test_local_file_success_receipt_and_no_op_timestamps(local_file_db, tmp_path):
    source = tmp_path / "daily.csv"
    source.write_text("Code\nA\n", encoding="utf-8")
    saved = flows.create_flow(_file_flow(source), _request())
    receipt = flows.LocalFileReceipt(
        identity="b" * 64, previous_identity=None, raw_checksum="c" * 64,
        normalized_path=flows.normalize_target_path(source), worksheet=None,
        config_revision=1, file_size=7, modified_at_ns=1,
    )
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        producing_run = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, worker_id, job_json, created_at, started_at)
               VALUES (?, 'scheduled', 'running', 'worker', ?, ?, ?)""",
            (saved["id"], flows._json(job), flows._iso(flows._now()), flows._iso(flows._now())),
        ).lastrowid
    flows.update_run(
        "worker", producing_run,
        flows.WorkerProgress(
            status="succeeded",
            progress={"stage": "complete", "message": "saved", "no_op": False},
            source_receipt=receipt,
        ),
    )
    with database.get_db() as db:
        first = db.execute(
            """SELECT local_file_last_identity, last_success_at
               FROM flows WHERE id=?""", (saved["id"],),
        ).fetchone()
        db.execute("UPDATE flows SET last_execution_success_at=NULL WHERE id=?", (saved["id"],))
        no_op_run = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, worker_id, job_json, created_at, started_at)
               VALUES (?, 'scheduled', 'running', 'worker', ?, ?, ?)""",
            (saved["id"], flows._json(flows._build_job(db, saved["id"])),
             flows._iso(flows._now()), flows._iso(flows._now())),
        ).lastrowid
    assert first["local_file_last_identity"] == "b" * 64

    flows.update_run(
        "worker", no_op_run,
        flows.WorkerProgress(
            status="succeeded",
            progress={"stage": "complete", "message": "unchanged", "no_op": True},
        ),
    )
    with database.get_db() as db:
        current = db.execute(
            """SELECT local_file_last_identity, last_success_at, last_execution_success_at
               FROM flows WHERE id=?""", (saved["id"],),
        ).fetchone()
    assert current["local_file_last_identity"] == "b" * 64
    assert current["last_success_at"] == first["last_success_at"]
    assert current["last_execution_success_at"] is not None


def test_local_file_sql_retry_uses_only_csv_and_preserves_adapter(
    local_file_db, tmp_path, monkeypatch,
):
    source = tmp_path / "daily.csv"
    source.write_text("Code\nA\n", encoding="utf-8")
    profile = tmp_path / "profile"
    saved = flows.create_flow(_file_flow(source), _request())
    with database.get_db() as db:
        source_job = flows._build_job(db, saved["id"])
    artifacts, _timings, outcome = flow_worker.execute_local_file_job(
        _worker_job(source, profile), lambda *_args, **_kwargs: None, profile,
        run_id=71, register_folder=lambda _path: {"ops": []},
    )
    source_job["sql_handoff"] = {
        "enabled": True, "mode": "append", "uppercase": False,
        "server": "localhost:5432", "database": "warehouse",
        "schema": "reporting", "table": "daily",
    }
    with database.get_db() as db:
        run_id = db.execute(
            """INSERT INTO flow_runs
               (flow_id, trigger_type, status, job_json, artifact_json,
                error, created_at, finished_at)
               VALUES (?, 'scheduled', 'failed', ?, ?, 'SQL failed', ?, ?)""",
            (saved["id"], json.dumps(source_job), json.dumps(artifacts),
             flows._iso(flows._now()), flows._iso(flows._now())),
        ).lastrowid
    monkeypatch.setattr(
        flows, "launch_local_worker", lambda mode: {"status": "launched", "mode": mode},
    )

    retried = flows.retry_run_sql(run_id, _request())

    assert retried["job"]["execution"]["required_adapter"] == "local_file"
    assert retried["job"]["source_receipt"] == outcome["source_receipt"]
    assert [item["status"] for item in retried["job"]["sql_retry"]["artifacts"]] == ["saved"]
    assert retried["job"]["sql_retry"]["artifacts"][0]["filename"].endswith("_normalized.csv")
    assert retried["job"]["execution"]["required_artifact_store_id"] == (
        flow_publish.artifact_store_id(profile)
    )


def test_file_builder_exposes_conditional_sheet_and_portal_only_controls():
    source = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'id="flow-source-file"' in source
    assert 'id="flow-local-file-path"' in source
    assert 'id="flow-local-file-worksheet-field"' in source
    assert 'input.required = !csv' in source
    assert 'filter(item => item.source_type === "portal")' in source
    assert 'const resumable = sourceType === "portal"' in source
    assert "Private snapshots · latest 3" in source


def test_local_file_run_errors_are_redacted_from_ai_context():
    message = r"Configured source file does not exist: \\server\finance\secret.xlsx"
    redacted = _safe_text(message)
    assert r"\\server\finance\secret.xlsx" not in redacted
    assert "[local path]" in redacted


def test_worker_claim_requires_local_file_capability(local_file_db, tmp_path):
    source = tmp_path / "daily.csv"
    source.write_text("Code\nA\n", encoding="utf-8")
    saved = flows.create_flow(_file_flow(source), _request())
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json, created_at)
               VALUES (?, 'scheduled', 'queued', ?, ?)""",
            (saved["id"], json.dumps(job), flows._iso(flows._now())),
        )
    flows.register_worker(flows.WorkerRegister(
        worker_id="old-worker", display_name="Old worker",
        capabilities={"adapters": ["web_export"], "headed": False},
    ))
    assert flows.claim_run("old-worker")["run"] is None

    flows.register_worker(flows.WorkerRegister(
        worker_id="new-worker", display_name="New worker",
        capabilities={"adapters": ["web_export", "local_file"], "headed": False},
    ))
    assert flows.claim_run("new-worker")["run"]["flow_id"] == saved["id"]
