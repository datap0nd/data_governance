"""Python-script Flow source: API validation, worker execution and surfaces.

Synthetic only. Scripts run with the test interpreter inside tmp_path; no
PostgreSQL server or portal is contacted (SQL and view refresh are stubbed).
"""
import csv
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import database, flow_activity, flow_browser, flow_paths, flow_python, flow_sql, flow_worker
from app import flow_view_refresh
from app.routers import flow_groups, flows, pipelines
from test_flows import flow_db, _request


FETCH_SCRIPT = textwrap.dedent('''
    import argparse, os, sys
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    assert args.input is None and "METRONOME_FLOW_INPUT" not in os.environ
    assert os.environ["METRONOME_FLOW_OUTPUT"] == args.output
    assert os.environ["METRONOME_FLOW_STEP"] == "1"
    with open(args.output, "w", encoding="utf-8", newline="") as handle:
        handle.write(os.environ.get("FETCH_CONTENT", "Code,Units\\nA,7\\nB,3\\n"))
    print("fetched", os.environ["METRONOME_FLOW_NAME"], "run", os.environ["METRONOME_FLOW_RUN_ID"])
''')

ENRICH_SCRIPT = textwrap.dedent('''
    import argparse, csv, os
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    assert os.environ["METRONOME_FLOW_INPUT"] == args.input
    assert os.environ["METRONOME_FLOW_STEPS"] == "2"
    with open(args.input, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    with open(args.output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\\n")
        writer.writerow(rows[0] + ["Doubled"])
        for row in rows[1:]:
            writer.writerow(row + [str(int(row[1]) * 2)])
    with open(os.path.join(os.environ["METRONOME_FLOW_RESULTS_DIR"], "enrich.log"), "w") as handle:
        handle.write("extra file")
    print("enriched", len(rows) - 1, "rows")
''')


def _write_scripts(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    fetch = folder / "fetch_orders.py"
    enrich = folder / "enrich_orders.py"
    fetch.write_text(FETCH_SCRIPT, encoding="utf-8")
    enrich.write_text(ENRICH_SCRIPT, encoding="utf-8")
    return [fetch, enrich]


def _python_flow(scripts, **overrides):
    values = {
        "name": "Python orders",
        "source_type": "python",
        "python_scripts": [str(item) for item in scripts],
        "schedule_type": "daily",
        "schedule_time": "08:00",
    }
    values.update(overrides)
    return flows.FlowWrite(**values)


def _sql_fields():
    return {
        "sql_handoff_enabled": True, "sql_mode": "append", "sql_database": "warehouse",
        "sql_schema": "reporting", "sql_table": "orders",
    }


def _worker_job(scripts, target: Path, *, output_format="csv", sql=False,
                output_mode="run_folders", filename_template=None):
    return {
        "schema_version": 3,
        "execution": {"required_adapter": "python_script", "browser_mode": "headless"},
        "flow": {"id": 23, "name": "Python orders", "source_type": "python"},
        "site": {"adapter": "python_script"},
        "report": {"id": 9, "name": "Python scripts"},
        "python_source": {
            "enabled": True, "scripts": [str(item) for item in scripts],
            "output_format": output_format, "destination": "sql" if sql else "file",
            "timeout_seconds": 3600,
        },
        "downloads": {
            "target_folder": str(target),
            "filename_template": filename_template or f"{{flow}}.{output_format}",
            "output_mode": output_mode, "file_format": output_format, "periods": [None],
        },
        "transformation": {"enabled": False},
        "sql_handoff": {
            "enabled": sql, "mode": "append", "uppercase": False, "server": "localhost:5432",
            "database": "warehouse", "schema": "reporting", "table": "orders",
        },
        "post_sql_refresh": {"mode": "off", "views": []},
    }


def _run(job, target: Path, profile: Path, *, run_id=31):
    target.mkdir(parents=True, exist_ok=True)
    events, registered = [], []
    outcome = flow_worker.execute_python_job(
        job, lambda status, detail: events.append((status, detail)), profile,
        run_id=run_id, register_folder=lambda path: registered.append(path) or {"ops": []},
    )
    return outcome, events, registered


# --- API validation ---------------------------------------------------------

def test_python_flow_write_validates_scripts_and_forces_shared_defaults():
    with pytest.raises(ValueError, match="Add at least one Python script."):
        flows.FlowWrite(name="Empty", source_type="python")
    with pytest.raises(ValueError, match="absolute paths"):
        flows.FlowWrite(name="Relative", source_type="python", python_scripts=["scripts/a.py"])
    with pytest.raises(ValueError, match=r"must be \.py files"):
        flows.FlowWrite(name="Text", source_type="python", python_scripts=["/scripts/a.txt"])
    with pytest.raises(ValueError, match="without wildcards"):
        flows.FlowWrite(name="Wild", source_type="python", python_scripts=[r"C:\scripts\*.py"])
    with pytest.raises(ValueError, match="at most 20"):
        flows.FlowWrite(name="Many", source_type="python",
                        python_scripts=[f"/scripts/step{i}.py" for i in range(21)])
    with pytest.raises(ValueError, match="CSV or Excel"):
        flows.FlowWrite(name="Html", source_type="python", python_scripts=["/scripts/a.py"], file_format="html")
    with pytest.raises(ValueError, match="reserved for file-source"):
        flows.FlowWrite(name="Snap", source_type="python", python_scripts=["/scripts/a.py"],
                        output_mode="private_snapshot")
    with pytest.raises(ValueError, match="Python scripts"):
        flows.FlowWrite(name="Unknown", source_type="ftp", python_scripts=["/scripts/a.py"])

    body = flows.FlowWrite(
        name="Ordered", source_type="python",
        python_scripts=['"/scripts/a.py"', "  /scripts/b.py  ", "/scripts/A.PY", "", "/scripts/b.py"],
        transform_enabled=True, transform_script_path="/scripts/t.py",
        site_id=3, report_id=4, export_views=["x"], selections={"k": "v"}, browser_mode="headed",
        period_strategy="latest", download_mode="one_per_period", window_weeks=2,
        outlook_subject_contains="Report", local_file_path="/data/in.csv",
    )
    assert body.python_scripts == ["/scripts/a.py", "/scripts/b.py"]
    assert body.file_format == "csv" and body.filename_template == "{flow}.csv"
    assert body.transform_enabled is False and body.transform_script_path is None
    assert body.site_id is None and body.report_id is None
    assert body.export_views == [] and body.selections == {}
    assert body.browser_mode == "headless" and body.download_parallelism == 1
    assert body.period_strategy == "none" and body.download_mode == "single" and body.window_weeks is None
    assert body.outlook_subject_contains is None and body.local_file_path is None
    assert body.output_mode == "run_folders"

    excel = flows.FlowWrite(name="Excel", source_type="python", python_scripts=["/scripts/a.py"],
                            file_format="xlsx", output_mode="direct_replace")
    assert excel.file_format == "xlsx" and excel.filename_template == "{flow}.xlsx"
    assert excel.output_mode == "direct_replace"

    sql = flows.FlowWrite(name="SQL", source_type="python", python_scripts=["/scripts/a.py"],
                          file_format="xlsx", **_sql_fields())
    assert sql.file_format == "csv" and sql.filename_template == "{flow}.csv"
    assert sql.sql_handoff_enabled is True

    named = flows.FlowWrite(name="Named", source_type="python", python_scripts=["/scripts/a.py"],
                            filename_template="orders.csv")
    assert named.filename_template == "orders.csv"
    with pytest.raises(ValueError, match="must end in .xlsx"):
        flows.FlowWrite(name="Mismatch", source_type="python", python_scripts=["/scripts/a.py"],
                        file_format="xlsx", filename_template="orders.csv")

    outlook = flows.FlowWrite(name="Mail", source_type="outlook", outlook_subject_contains="Report",
                              python_scripts=["/scripts/a.py"])
    local = flows.FlowWrite(name="Local", source_type="file", local_file_path="/data/in.csv",
                            python_scripts=["/scripts/a.py"])
    assert outlook.python_scripts == [] and local.python_scripts == []


def test_flow_python_helpers_describe_and_normalize(tmp_path):
    assert flow_python.describe([r"C:\scripts\fetch.py", "/opt/enrich.py"]) == "fetch.py \u2192 enrich.py"
    assert flow_python.normalize_scripts([r"C:\Scripts\a.py", r"c:/scripts/A.py"]) == [r"C:\Scripts\a.py"]
    with pytest.raises(ValueError, match="Choose at most 20 Python scripts."):
        flow_python.normalize_scripts([f"/s/{i}.py" for i in range(21)])
    section = flow_python.job_section({"source_type": "python", "python_scripts_json": '["/s/a.py"]',
                                       "file_format": "xlsx", "sql_handoff_enabled": True})
    assert section == {"enabled": True, "scripts": ["/s/a.py"], "output_format": "csv",
                       "destination": "sql", "timeout_seconds": 3600}
    assert flow_python.job_section({"source_type": "portal", "python_scripts": ["/s/a.py"]})["enabled"] is False
    assert flow_python.job_section({"source_type": "portal", "python_scripts": ["/s/a.py"]})["scripts"] == []
    # str(Path) renders with the platform separator, so compare against str() rather than
    # forward-slash literals (the Windows CI shard runs this file too).
    script, previous, out = tmp_path / "a.py", tmp_path / "step-1-a.csv", tmp_path / "a.csv"
    assert flow_python.script_command(script, None, out) == [sys.executable, str(script), "--output", str(out)]
    assert flow_python.script_command(script, previous, out) == [
        sys.executable, str(script), "--input", str(previous), "--output", str(out)]
    environment = flow_python.step_environment(
        {"PATH": "x", "METRONOME_FLOW_INPUT": "stale"}, input_path=None, output_path="/o.csv",
        results_dir="/steps", step=1, steps=2, output_format="csv", flow_name="F", run_id=5,
    )
    assert "METRONOME_FLOW_INPUT" not in environment and environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["METRONOME_FLOW_STEPS"] == "2" and environment["PATH"] == "x"


# --- Flow persistence -------------------------------------------------------

def test_python_flow_uses_hidden_anchor_managed_folder_and_v3_job(flow_db, tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    saved = flows.create_flow(_python_flow(scripts, filename_template="orders.csv"), _request())

    assert saved["source_type"] == "python"
    assert saved["source_adapter"] == "python_script"
    assert saved["python_scripts"] == [str(item) for item in scripts]
    assert saved["file_format"] == "csv" and saved["transform_enabled"] is False
    folder = Path(saved["flow_folder"])
    assert saved["folder_relative"] == str(Path("Python") / folder.name)
    assert saved["target_folder"] == str(folder / "Downloads")
    assert all(site["adapter"] != "python_script" for site in flows.catalog()["sites"])
    assert all(report["site_id"] != saved["site_id"] for report in flows.catalog()["reports"])

    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
    assert job["schema_version"] == 3
    assert job["execution"]["required_adapter"] == "python_script"
    assert job["execution"]["browser_mode"] == "headless"
    assert job["python_source"] == {
        "enabled": True, "scripts": [str(item) for item in scripts], "output_format": "csv",
        "destination": "file", "timeout_seconds": 3600,
    }
    assert job["transformation"]["enabled"] is False
    assert job["local_file"]["enabled"] is False and job["outlook_source"]["enabled"] is False
    assert job["downloads"]["filename_template"] == "orders.csv"
    key = pipelines.flow_target_resource_key_from_job(job)
    assert key is not None and key.startswith("file|")
    flow_paths.assert_job_paths(job)


def test_python_flow_update_changes_scripts_and_source_category_is_fixed(flow_db, tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    saved = flows.create_flow(_python_flow(scripts[:1]), _request())
    updated = flows.update_flow(saved["id"], _python_flow(scripts, file_format="xlsx"), _request())
    assert updated["python_scripts"] == [str(item) for item in scripts]
    assert updated["file_format"] == "xlsx" and updated["filename_template"] == "{flow}.xlsx"
    with database.get_db() as db:
        assert json.loads(db.execute(
            "SELECT python_scripts_json FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()[0]) == [str(item) for item in scripts]

    source = tmp_path / "input.csv"
    source.write_text("Code\nA\n", encoding="utf-8")
    local = flows.create_flow(flows.FlowWrite(name="Local", source_type="file", local_file_path=str(source)), _request())
    with pytest.raises(HTTPException) as failure:
        flows.update_flow(local["id"], _python_flow(scripts, name="Local"), _request())
    assert failure.value.status_code == 409


# --- Worker execution -------------------------------------------------------

def test_scripts_run_in_order_with_the_worker_interpreter(tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    target = tmp_path / "Downloads"
    job = _worker_job(scripts, target)
    (artifacts, timings, outcome), events, registered = _run(job, target, tmp_path / "profile")

    assert len(registered) == 1 and outcome["no_op"] is False
    artifact = artifacts[0]
    assert artifact["status"] == "saved" and artifact["storage_scope"] == "target_run_folder"
    assert artifact["detected_format"] == "csv" and artifact["row_count"] == 2
    final = Path(artifact["file_path"])
    assert final.name == "Python_orders.csv" and final.parent == Path(registered[0])
    with final.open(encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle)) == [["Code", "Units", "Doubled"], ["A", "7", "14"], ["B", "3", "6"]]
    steps_folder = final.parent / "steps"
    assert (steps_folder / "step-1-fetch_orders.csv").read_text(encoding="utf-8") == "Code,Units\nA,7\nB,3\n"
    assert (steps_folder / "enrich.log").read_text() == "extra file"
    assert outcome["sql_artifacts"] == [artifact]

    records = artifact["python_steps"]
    assert [item["script_name"] for item in records] == ["fetch_orders.py", "enrich_orders.py"]
    assert [item["index"] for item in records] == [1, 2] and all(item["steps"] == 2 for item in records)
    assert all(len(item["script_checksum"]) == 64 and item["exit_code"] == 0 for item in records)
    assert records[0]["stdout"] == "fetched Python orders run 31"
    assert records[1]["stdout"] == "enriched 2 rows" and records[1]["output_path"] == str(final)
    assert all(item["duration_ms"] >= 0 for item in records)
    # Scripts run in place: the sources are untouched.
    assert scripts[0].read_text(encoding="utf-8") == FETCH_SCRIPT

    stages = [detail["stage"] for _, detail in events]
    assert stages[0] == "python_scripts"
    assert "Running 2 Python script(s) in order: fetch_orders.py \u2192 enrich_orders.py." == events[0][1]["message"]
    assert stages.count("python_step") == 2
    assert [detail["script"] for _, detail in events if detail["stage"] == "python_step"] == ["fetch_orders.py", "enrich_orders.py"]
    assert stages[-1] == "python_complete"
    assert events[-1][1]["message"].startswith("Ran 2 script(s); final CSV Python_orders.csv is ")
    assert [item["step"] for item in events[-1][1]["results"]] == [1, 2]
    assert [item["phase"] for item in timings] == ["python_scripts", "file_normalization", "total"]


def test_failing_script_names_step_and_stderr(tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    failing = tmp_path / "scripts" / "clean_orders.py"
    failing.write_text("import sys\nprint('partial output')\nsys.stderr.write(\"KeyError: 'region'\\n\")\nsys.exit(3)\n")
    target = tmp_path / "Downloads"
    with pytest.raises(RuntimeError, match=r"Python script clean_orders\.py \(step 2 of 2\) failed with exit code 3: KeyError: 'region'"):
        _run(_worker_job([scripts[0], failing], target), target, tmp_path / "profile")
    assert (tmp_path / "Downloads").is_dir()


def test_script_that_writes_nothing_fails_on_missing_output(tmp_path):
    silent = tmp_path / "scripts" / "silent.py"
    silent.parent.mkdir()
    silent.write_text("print('done without writing')\n")
    target = tmp_path / "Downloads"
    with pytest.raises(RuntimeError, match=r"silent\.py \(step 1 of 1\) completed but did not create --output") as failure:
        _run(_worker_job([silent], target), target, tmp_path / "profile")
    assert "done without writing" in str(failure.value)


def test_missing_or_non_py_script_fails_before_any_run_folder(tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    target = tmp_path / "Downloads"
    target.mkdir()
    for bad in (tmp_path / "scripts" / "missing.py", tmp_path / "scripts" / "fetch_orders.txt"):
        registered, events = [], []
        with pytest.raises(RuntimeError, match=r"missing\.py|not a \.py file"):
            flow_worker.execute_python_job(
                _worker_job([scripts[0], bad], target), lambda status, detail: events.append(detail),
                tmp_path / "profile", run_id=32,
                register_folder=lambda path: registered.append(path) or {"ops": []},
            )
        assert registered == [] and events == []
        assert list(target.iterdir()) == []
    with pytest.raises(RuntimeError, match="no scripts"):
        flow_worker.execute_python_job(
            _worker_job([], target), lambda *_a: None, tmp_path / "profile", run_id=33,
            register_folder=lambda path: pytest.fail("no folder expected"),
        )


def test_script_timeout_fails_the_run(tmp_path):
    slow = tmp_path / "scripts" / "slow.py"
    slow.parent.mkdir()
    slow.write_text("import time\ntime.sleep(30)\n")
    target = tmp_path / "Downloads"
    job = _worker_job([slow], target)
    job["python_source"]["timeout_seconds"] = 1
    with pytest.raises(RuntimeError, match=r"slow\.py \(step 1 of 1\) timed out after 1 seconds"):
        _run(job, target, tmp_path / "profile")


def test_xlsx_final_file_is_validated_as_a_workbook(tmp_path):
    pytest.importorskip("openpyxl")
    book = tmp_path / "scripts" / "book.py"
    book.parent.mkdir()
    book.write_text(textwrap.dedent('''
        import argparse, os, openpyxl
        parser = argparse.ArgumentParser(); parser.add_argument("--output", required=True)
        args = parser.parse_args()
        assert os.environ["METRONOME_FLOW_OUTPUT_FORMAT"] == "xlsx"
        workbook = openpyxl.Workbook(); sheet = workbook.active
        sheet.append(["Code", "Units"]); sheet.append(["A", 7])
        workbook.save(args.output)
    '''))
    target = tmp_path / "Downloads"
    (artifacts, _timings, _outcome), events, _registered = _run(
        _worker_job([book], target, output_format="xlsx"), target, tmp_path / "profile",
    )
    assert artifacts[0]["detected_format"] == "xlsx"
    assert artifacts[0]["filename"] == "Python_orders.xlsx" and artifacts[0]["file_size"] > 0
    assert len(artifacts[0]["checksum"]) == 64
    assert events[-1][1]["message"].startswith("Ran 1 script(s); final XLSX Python_orders.xlsx is ")

    text = tmp_path / "scripts" / "text.py"
    text.write_text("import argparse\np = argparse.ArgumentParser(); p.add_argument('--output')\n"
                    "open(p.parse_args().output, 'w').write('Code,Units\\nA,7\\n')\n")
    with pytest.raises(RuntimeError, match="should be XLSX but its content looks like csv"):
        _run(_worker_job([text], target, output_format="xlsx"), target, tmp_path / "profile-2", run_id=35)


def test_sql_destination_normalizes_csv_then_loads_and_refreshes(tmp_path, monkeypatch):
    scripts = _write_scripts(tmp_path / "scripts")
    monkeypatch.setenv("FETCH_CONTENT", "\ufeffCode;Units\nA;7\nB;3\n")
    target = tmp_path / "Downloads"
    target.mkdir()
    job = _worker_job(scripts[:1], target, sql=True)
    events, details = [], []

    def load(items, sql_target, **kwargs):
        events.append("sql")
        assert [item["status"] for item in items] == ["saved"]
        with Path(items[0]["file_path"]).open(encoding="utf-8-sig", newline="") as handle:
            assert list(csv.reader(handle)) == [["Code", "Units"], ["A", "7"], ["B", "3"]]
        assert sql_target["table"] == "orders"
        return {"rows_written": 2, "files_loaded": 1, "target": "warehouse.reporting.orders"}

    def refresh(frozen_job, progress, artifacts, timings, state, **kwargs):
        events.append("refresh")
        return None

    monkeypatch.setattr(flow_sql, "load_artifacts", load)
    monkeypatch.setattr(flow_view_refresh, "execute_after_sql", refresh)
    flow_worker.execute_flow(
        None, job, lambda status, detail, *args, **kwargs: details.append((status, detail)),
        tmp_path / "profile", run_id=41, register_folder=lambda path: {"ops": []},
    )
    assert events == ["sql", "refresh"]
    assert details[-1][0] == "succeeded"
    assert details[-1][1]["message"] == (
        "Ran 1 Python script(s) and saved Python_orders.csv. Committed 2 row(s) to warehouse.reporting.orders."
    )
    stages = [detail["stage"] for _, detail in details]
    assert stages.index("python_complete") < stages.index("sql_insertion") < stages.index("sql_insertion_complete")
    saved = next(detail for _, detail in details if detail["stage"] == "python_complete")
    assert saved["results"][0]["script"] == "fetch_orders.py"


def test_headerless_csv_bound_for_sql_fails(tmp_path, monkeypatch):
    scripts = _write_scripts(tmp_path / "scripts")
    monkeypatch.setenv("FETCH_CONTENT", ",Units\nA,7\n")
    target = tmp_path / "Downloads"
    with pytest.raises(RuntimeError, match="blank column header"):
        _run(_worker_job(scripts[:1], target, sql=True), target, tmp_path / "profile")
    # The same file is delivered as written when the destination is a file.
    (artifacts, _t, _o), _events, _registered = _run(
        _worker_job(scripts[:1], target), target, tmp_path / "profile-2", run_id=36,
    )
    assert Path(artifacts[0]["file_path"]).read_text(encoding="utf-8") == ",Units\nA,7\n"


def test_malformed_python_job_is_rejected_by_execute_flow(tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    job = _worker_job(scripts[:1], tmp_path / "Downloads")
    job["schema_version"] = 2
    with pytest.raises(RuntimeError, match="Python-script job payload is malformed"):
        flow_worker.execute_flow(None, job, lambda *a, **k: None, tmp_path / "profile", run_id=42,
                                 register_folder=lambda path: {"ops": []})


def test_direct_replace_publishes_the_final_file_into_the_target(tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    target = tmp_path / "Downloads"
    target.mkdir()
    job = _worker_job(scripts, target, output_mode="direct_replace")
    details = []
    state = flow_worker.execute_flow(
        None, job, lambda status, detail, *args, **kwargs: details.append((status, detail)),
        tmp_path / "profile", run_id=43, register_folder=lambda path: {"ops": []},
    )
    published = target / "Python_orders.csv"
    with published.open(encoding="utf-8", newline="") as handle:
        assert list(csv.reader(handle))[0] == ["Code", "Units", "Doubled"]
    assert state["artifacts"][0]["publish_status"] == "published"
    assert state["artifacts"][0]["published_file_path"] == str(published)
    assert state["artifacts"][0]["storage_scope"] == "worker_private"
    assert details[-1][0] == "succeeded"
    assert details[-1][1]["message"].startswith("Ran 2 Python script(s) and saved Python_orders.csv.")
    assert "publish_complete" in [detail["stage"] for _, detail in details]
    assert [item.name for item in target.iterdir()] == ["Python_orders.csv"]


# --- Claim, surfaces and standalone ----------------------------------------

def test_worker_claim_requires_python_script_capability(flow_db, tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    saved = flows.create_flow(_python_flow(scripts), _request())
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json, created_at)
               VALUES (?, 'scheduled', 'queued', ?, ?)""",
            (saved["id"], json.dumps(job), flows._iso(flows._now())),
        )
    flows.register_worker(flows.WorkerRegister(
        worker_id="old-worker", display_name="Old worker",
        capabilities={"adapters": ["web_export", "local_file"], "headed": False, "shared_flow_artifacts": True},
    ))
    assert flows.claim_run("old-worker")["run"] is None

    flows.register_worker(flows.WorkerRegister(
        worker_id="new-worker", display_name="New worker",
        capabilities={"adapters": ["web_export", "python_script"], "headed": False, "shared_flow_artifacts": True},
    ))
    assert flows.claim_run("new-worker")["run"]["flow_id"] == saved["id"]
    assert flow_browser.can_claim(job, {}) is True


def test_python_surfaces_activity_groups_resume_and_paths(flow_db, tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    saved = flows.create_flow(_python_flow(scripts), _request())
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        now = flows._iso(flows._now())
        run_id = db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, worker_id, job_json, progress_json, created_at, started_at)
               VALUES (?, 'manual', 'running', 'worker-1', ?, ?, ?, ?)""",
            (saved["id"], json.dumps(job), json.dumps({"stage": "python_step", "message": "Running script 1 of 2"}), now, now),
        ).lastrowid
        db.execute("INSERT INTO flow_run_events(run_id, status, stage, message, created_at) VALUES (?, 'running', 'python_scripts', 'm', ?)", (run_id, now))
        progress = flow_activity.row_progress(db, db.execute("SELECT * FROM flow_runs WHERE id=?", (run_id,)).fetchone())
        labels = [phase["label"] for phase in progress["phases"]]
        assert labels == ["Prepare run", "Run scripts", "Prepare files", "Finish"]
        assert progress["phases"][0]["completed"] == 1 and progress["total"] == 4
        assert progress["runners"][0]["label"] == "Run progress"
        assert [(phase["label"], phase["completed"]) for phase in progress["phases"]] == [
            ("Prepare run", 1), ("Run scripts", 0), ("Prepare files", 0), ("Finish", 0)]
        # python_complete counts the scripts as acquired and the file as prepared.
        for stage in ("python_step", "python_complete"):
            db.execute("INSERT INTO flow_run_events(run_id, status, stage, message, created_at) VALUES (?, 'running', ?, 'm', ?)", (run_id, stage, now))
        db.execute("UPDATE flow_runs SET progress_json=? WHERE id=?",
                   (json.dumps({"stage": "python_complete", "message": "Ran 2 script(s)"}), run_id))
        progress = flow_activity.row_progress(db, db.execute("SELECT * FROM flow_runs WHERE id=?", (run_id,)).fetchone())
        assert [(phase["label"], phase["completed"]) for phase in progress["phases"]] == [
            ("Prepare run", 1), ("Run scripts", 1), ("Prepare files", 1), ("Finish", 0)]

        db.execute("UPDATE flow_runs SET status='failed', error='boom', finished_at=? WHERE id=?", (now, run_id))
        eligibility = flows.inspect_resume_eligibility(db, run_id)
        assert eligibility["reason_code"] == "source_no_resume"
        assert "Python-script runs cannot be resumed" in eligibility["message"]
        # The failure alert names the scripts in order (the fixture Flow has no owner, so supply one).
        context = dict(flows._flow_failure_context(db, run_id))
        context.update(owner_name="Owner", owner_email="owner@example.test")
        message = flows._flow_failure_message(context)
        assert "Python scripts: fetch_orders.py \u2192 enrich_orders.py" in message["html_body"]
        assert message["to"] == "owner@example.test"

    assert flow_groups.module_of({"source_type": "python", "source_adapter": "python_script"}) == "Python"
    assert flow_groups.GroupWrite(name="Nightly", module="Python", flow_ids=[saved["id"]]).module == "Python"

    root = tmp_path / "root"
    inside = root / "Python" / "shared" / "fetch.py"
    rules = {"flows_root": str(root), "source_folder": "Python", "enforced": True, "version": 1}
    flow = {"source_type": "python", "target_folder": str(root / "Python" / "Flow" / "Downloads"),
            "python_scripts": [str(inside)]}
    flow_paths.validate_flow(flow, rules, resolve=False)
    with pytest.raises(flow_paths.PathOutsideRoot, match="Python script must be inside"):
        flow_paths.validate_flow({**flow, "python_scripts": [str(scripts[0])]}, rules, resolve=False)
    with pytest.raises(flow_paths.PathOutsideRoot, match="Python script must be inside"):
        flow_paths.validate_flow({**flow, "python_scripts": None,
                                  "python_scripts_json": json.dumps([str(scripts[0])])}, rules, resolve=False)
    # The frozen job carries the scripts too: enforcing the saved policy on a
    # job whose scripts live outside <root>/Python fails before any run.
    flow_paths.assert_job_paths(job)
    with pytest.raises(flow_paths.PathOutsideRoot, match="Python script must be inside"):
        flow_paths.assert_job_paths({**job, "paths": {**job["paths"], "enforced": True}})


def test_standalone_dry_run_reports_python_source_and_creates_nothing(flow_db, tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    saved = flows.create_flow(_python_flow(scripts), _request())
    assert saved["standalone"]["state"] == "current"
    before = set(tmp_path.rglob("*"))
    result = subprocess.run(
        [sys.executable, saved["standalone"]["launcher"], "--dry-run"],
        capture_output=True, text=True, check=True,
    )
    assert set(tmp_path.rglob("*")) == before
    data = json.loads(result.stdout)
    assert data["source"] == "python" and data["flow_id"] == saved["id"] and data["sql"] is False
    assert str(tmp_path) not in result.stdout
