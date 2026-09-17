"""Python-script Flow source: API validation, worker execution and surfaces.

Synthetic only. Scripts run with the test interpreter inside tmp_path; no
PostgreSQL server or portal is contacted (SQL and view refresh are stubbed).
"""
import csv
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import database, flow_activity, flow_browser, flow_paths, flow_python, flow_sql, flow_worker
from app import flow_view_refresh
from app.flow_clock import dubai_today
from app.routers import flow_groups, flows, pipelines, system_paths
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
                output_mode="run_folders", filename_template=None, arguments=None, values=None):
    return {
        "schema_version": 3,
        "execution": {"required_adapter": "python_script", "browser_mode": "headless"},
        "flow": {"id": 23, "name": "Python orders", "source_type": "python"},
        "site": {"adapter": "python_script"},
        "report": {"id": 9, "name": "Python scripts"},
        "python_source": {
            "enabled": True, "scripts": [str(item) for item in scripts],
            "arguments": list(arguments or []), "values": [list(row) for row in values] if values else [],
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
                          file_format="xlsx", output_mode="direct_replace", **_sql_fields())
    assert sql.file_format == "csv" and sql.filename_template == "{flow}.csv"
    assert sql.sql_handoff_enabled is True
    # The hidden file-output controls never publish a SQL-bound CSV: it stays
    # in the run folder, which Retry SQL reads.
    assert sql.output_mode == "run_folders"

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
    assert section == {"enabled": True, "scripts": ["/s/a.py"], "arguments": [""], "values": [[]],
                       "output_format": "csv", "destination": "sql", "timeout_seconds": 3600}
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
        "enabled": True, "scripts": [str(item) for item in scripts], "arguments": ["", ""],
        "values": [[], []], "output_format": "csv", "destination": "file", "timeout_seconds": 3600,
    }
    assert job["transformation"]["enabled"] is False
    assert job["local_file"]["enabled"] is False and job["outlook_source"]["enabled"] is False
    assert job["downloads"]["filename_template"] == "orders.csv"
    key = pipelines.flow_target_resource_key_from_job(job)
    assert key is not None and key.startswith("file|")
    flow_paths.assert_job_paths(job)


def test_python_site_migration_steps_aside_from_a_user_site_named_python(flow_db, tmp_path):
    with database.get_db() as db:
        db.execute("DELETE FROM flow_reports WHERE source_kind='system' AND site_id IN "
                   "(SELECT id FROM flow_sites WHERE adapter='python_script')")
        db.execute("DELETE FROM flow_sites WHERE adapter='python_script'")
        db.execute(
            """INSERT INTO flow_sites (name, adapter, base_url, enabled, created_at, updated_at)
               VALUES ('Python', 'asap_portal', 'https://asap.example.test/', 1,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"""
        )
    # Migrations replay on every start; the UNIQUE name must not break the upgrade.
    database.init_db()
    with database.get_db() as db:
        sites = [dict(row) for row in db.execute(
            "SELECT id, name FROM flow_sites WHERE adapter='python_script'")]
        user = db.execute("SELECT adapter FROM flow_sites WHERE name='Python'").fetchone()
    assert len(sites) == 1 and sites[0]["name"] != "Python"
    assert sites[0]["name"].startswith("Python scripts (internal ")
    assert user["adapter"] == "asap_portal"

    scripts = _write_scripts(tmp_path / "scripts")
    saved = flows.create_flow(_python_flow(scripts), _request())
    assert saved["source_type"] == "python" and saved["site_id"] == sites[0]["id"]
    assert saved["source_adapter"] == "python_script"
    assert all(site["adapter"] != "python_script" for site in flows.catalog()["sites"])


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


def test_renaming_a_python_flow_relocates_scripts_kept_in_its_folder(flow_db, tmp_path):
    root = tmp_path / "managed"
    system_paths.put_paths(system_paths.PathsWrite(flows_root=str(root), create=True, enforced=True), _request())
    saved = flows.create_flow(_python_flow(_write_scripts(root / "Python" / "seed")[:1]), _request())
    # The Flow's own Scripts folder is the natural, enforcement-accepted home for its scripts.
    own = _write_scripts(Path(saved["flow_folder"]) / "Scripts")
    saved = flows.update_flow(saved["id"], _python_flow(own), _request())
    assert saved["python_scripts"] == [str(item) for item in own]
    renamed = flows.update_flow(saved["id"], _python_flow(own, name="Python orders renamed"), _request())
    folder = Path(renamed["flow_folder"])
    assert folder == Path(saved["flow_folder"]).with_name("Python orders renamed") and not Path(saved["flow_folder"]).exists()
    assert renamed["python_scripts"] == [str(folder / "Scripts" / item.name) for item in own]
    assert all(Path(item).is_file() for item in renamed["python_scripts"])
    with database.get_db() as db:
        assert json.loads(db.execute(
            "SELECT python_scripts_json FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()[0]) == renamed["python_scripts"]
    # A rename never silently breaks another Flow whose scripts live in this folder.
    flows.create_flow(_python_flow(renamed["python_scripts"], name="Python reuse"), _request())
    with pytest.raises(HTTPException) as failure:
        flows.update_flow(saved["id"], _python_flow(renamed["python_scripts"], name="Python orders again"), _request())
    assert failure.value.status_code == 409 and "Another Flow uses files in this folder" in failure.value.detail
    assert folder.is_dir() and all(Path(item).is_file() for item in renamed["python_scripts"])


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
    # Every step event and the completion summary carry the SHA-256 of the script that ran.
    expected = [(script.name, hashlib.sha256(script.read_bytes()).hexdigest()) for script in scripts]
    assert [(detail["script"], detail["checksum"]) for _, detail in events if detail["stage"] == "python_step"] == expected
    assert [(item["script_name"], item["script_checksum"]) for item in records] == expected
    # Each finished step is reported on its own, before the completion summary.
    finished = [detail for _, detail in events if detail["stage"] == "python_step_complete"]
    assert [(item["step"], item["script"], item["checksum"]) for item in finished] == [
        (1, "fetch_orders.py", expected[0][1]), (2, "enrich_orders.py", expected[1][1]),
    ]
    assert all(item["exit_code"] == 0 and item["error"] is None for item in finished)
    assert [item["stdout"] for item in finished] == ["fetched Python orders run 31", "enriched 2 rows"]
    assert finished[1]["output"] == str(final) and finished[1]["message"].startswith(
        "Script 2 of 2: enrich_orders.py finished in "
    )
    assert stages.index("python_step_complete") < stages.index("python_complete")
    assert not any(detail["stage"] == "python_step_failed" for _, detail in events)
    assert stages[-1] == "python_complete"
    assert events[-1][1]["message"].startswith("Ran 2 script run(s); 1 final CSV file(s): Python_orders.csv (")
    assert events[-1][1]["runs"] == 2 and events[-1][1]["deliverables"] == 1
    assert [item["step"] for item in events[-1][1]["results"]] == [1, 2]
    assert [(item["script"], item["checksum"]) for item in events[-1][1]["results"]] == expected
    assert [item["phase"] for item in timings] == ["python_scripts", "file_normalization", "total"]


def test_failing_script_names_step_and_stderr(tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    failing = tmp_path / "scripts" / "clean_orders.py"
    failing.write_text("import sys\nprint('partial output')\nsys.stderr.write(\"KeyError: 'region'\\n\")\nsys.exit(3)\n")
    target = tmp_path / "Downloads"
    target.mkdir()
    events = []
    with pytest.raises(RuntimeError, match=r"Python script clean_orders\.py \(step 2 of 2\) failed with exit code 3: KeyError: 'region'"):
        flow_worker.execute_python_job(
            _worker_job([scripts[0], failing], target), lambda _status, detail: events.append(detail),
            tmp_path / "profile", run_id=31, register_folder=lambda path: {"ops": []},
        )
    assert (tmp_path / "Downloads").is_dir()
    # The failed step was announced with its checksum before it ran, so the audit trail keeps it.
    failed = [detail for detail in events if detail["stage"] == "python_step"][-1]
    assert failed["step"] == 2 and failed["script"] == "clean_orders.py"
    assert failed["checksum"] == hashlib.sha256(failing.read_bytes()).hexdigest()
    assert not any(detail["stage"] == "python_complete" for detail in events)
    # The earlier step keeps its structured record and the failed step gets one too.
    complete = [detail for detail in events if detail["stage"] == "python_step_complete"]
    assert [(item["step"], item["script"], item["exit_code"]) for item in complete] == [(1, "fetch_orders.py", 0)]
    broken = [detail for detail in events if detail["stage"] == "python_step_failed"]
    assert len(broken) == 1 and broken[0]["step"] == 2 and broken[0]["script"] == "clean_orders.py"
    assert broken[0]["exit_code"] == 3 and broken[0]["stderr"] == "KeyError: 'region'"
    assert broken[0]["stdout"] == "partial output"
    assert broken[0]["checksum"] == hashlib.sha256(failing.read_bytes()).hexdigest()
    assert broken[0]["error"].startswith("Python script clean_orders.py (step 2 of 2) failed with exit code 3")
    assert broken[0]["message"] == "Script 2 of 2: clean_orders.py failed: " + broken[0]["error"]
    assert broken[0]["duration_ms"] >= 0


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
    target.mkdir()
    events = []
    with pytest.raises(RuntimeError, match=r"slow\.py \(step 1 of 1\) timed out after 1 seconds"):
        flow_worker.execute_python_job(
            job, lambda _status, detail: events.append(detail), tmp_path / "profile",
            run_id=31, register_folder=lambda path: {"ops": []},
        )
    events = [detail for detail in events if detail["stage"] == "python_step_failed"]
    assert len(events) == 1 and events[0]["step"] == 1 and events[0]["script"] == "slow.py"
    assert events[0]["exit_code"] is None
    assert events[0]["error"] == "Python script slow.py (step 1 of 1) timed out after 1 seconds and was stopped."
    assert events[0]["checksum"] == hashlib.sha256(slow.read_bytes()).hexdigest()


def test_interpreter_that_cannot_start_leaves_a_failed_step_record(tmp_path, monkeypatch):
    scripts = _write_scripts(tmp_path / "scripts")
    target = tmp_path / "Downloads"
    target.mkdir()

    def refuse(*_args, **_kwargs):
        raise OSError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(flow_python.subprocess, "run", refuse)
    events = []
    with pytest.raises(RuntimeError, match=r"fetch_orders\.py \(step 1 of 2\) could not be started: .*Resource temporarily unavailable") as failure:
        flow_worker.execute_python_job(
            _worker_job(scripts, target), lambda _status, detail: events.append(detail), tmp_path / "profile",
            run_id=34, register_folder=lambda path: {"ops": []},
        )
    assert isinstance(failure.value.__cause__, OSError)
    assert not any(detail["stage"] in ("python_step_complete", "python_complete") for detail in events)
    failed = [detail for detail in events if detail["stage"] == "python_step_failed"]
    assert len(failed) == 1 and failed[0]["step"] == 1 and failed[0]["script"] == "fetch_orders.py"
    assert failed[0]["exit_code"] is None and failed[0]["stdout"] == "" and failed[0]["stderr"] == ""
    assert failed[0]["error"].startswith("Python script fetch_orders.py (step 1 of 2) could not be started: ")
    assert failed[0]["checksum"] == hashlib.sha256(scripts[0].read_bytes()).hexdigest()


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
    assert events[-1][1]["message"].startswith("Ran 1 script run(s); 1 final XLSX file(s): Python_orders.xlsx (")

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
        "Ran 1 Python script run(s) and saved 1 file(s): Python_orders.csv. "
        "Committed 2 row(s) to warehouse.reporting.orders."
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
    assert details[-1][1]["message"].startswith("Ran 2 Python script run(s) and saved 1 file(s): Python_orders.csv.")
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


def test_browse_upload_stages_python_scripts_inside_the_enforced_folder(flow_db, tmp_path):
    root = tmp_path / "managed"
    system_paths.put_paths(system_paths.PathsWrite(flows_root=str(root), create=True, enforced=True), _request())
    app = FastAPI()
    app.include_router(flows.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/flows/transform-script", params={"target": "python"},
            files={"file": ("fetch_orders.py", FETCH_SCRIPT.encode("utf-8"), "text/x-python")},
        )
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["filename"] == "fetch_orders.py" and saved["file_size"] == len(FETCH_SCRIPT.encode("utf-8"))
        staged = Path(saved["script_path"])
        assert staged.is_file() and staged.read_text(encoding="utf-8") == FETCH_SCRIPT
        assert staged.parent.parent == root / "Python" / ".uploads"
        assert flow_paths.is_inside(str(staged), str(root / "Python"))
        # Only Python files may be staged as Python-source scripts.
        rejected = client.post(
            "/api/flows/transform-script", params={"target": "python"},
            files={"file": ("clean.ps1", b"Write-Output 'x'", "text/plain")},
        )
        assert rejected.status_code == 400 and "Choose a .py Python script." in rejected.text
        empty = client.post(
            "/api/flows/transform-script", params={"target": "python"},
            files={"file": ("empty.py", b"", "text/x-python")},
        )
        assert empty.status_code == 400 and "The selected Python script is empty." in empty.text
        # Transformation uploads keep their own staging area.
        transform = client.post(
            "/api/flows/transform-script",
            files={"file": ("transform.py", b"print('x')\n", "text/x-python")},
        ).json()
        assert Path(transform["script_path"]).parent.parent == root / ".metronome" / "uploads"
    with database.get_db() as db:
        entities = [row[0] for row in db.execute(
            "SELECT entity_type FROM event_log WHERE action='added' AND entity_name IN ('fetch_orders.py','transform.py') ORDER BY id",
        ).fetchall()]
    assert entities == ["flow_python_script", "flow_transform_script"]
    # The hidden staging area can never be allocated as a Flow's managed folder.
    hidden = flows.create_flow(_python_flow([staged], name=".uploads"), _request())
    assert Path(hidden["flow_folder"]) == root / "Python" / "uploads"
    # The enforced path policy accepts the staged script for a Python Flow.
    rules = {"flows_root": str(root), "source_folder": "Python", "enforced": True, "version": 1}
    flow = {"source_type": "python", "target_folder": str(root / "Python" / "Flow" / "Downloads"),
            "python_scripts": [str(staged)]}
    flow_paths.validate_flow(flow, rules)
    with pytest.raises(flow_paths.PathOutsideRoot, match="Python script must be inside"):
        flow_paths.validate_flow({**flow, "python_scripts": [transform["script_path"]]}, rules)
    created = flows.create_flow(_python_flow([staged]), _request())
    assert created["python_scripts"] == [str(staged)]
    assert flow_paths.is_inside(created["target_folder"], str(root / "Python"))


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


# --- Arguments and values ---------------------------------------------------

ARGV_SCRIPT = textwrap.dedent('''
    import csv, os, sys
    output = sys.argv[sys.argv.index("--output") + 1]
    with open(output, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\\n")
        writer.writerow(["kind", "value"])
        for item in sys.argv[1:]:
            writer.writerow(["argv", item])
        for name in ("METRONOME_FLOW_VALUE", "METRONOME_FLOW_INPUTS", "METRONOME_FLOW_INPUT",
                     "METRONOME_FLOW_STEP", "METRONOME_FLOW_STEPS"):
            writer.writerow(["env:" + name, os.environ.get(name, "<unset>")])
''')

VALUE_SCRIPT = textwrap.dedent('''
    import argparse, os
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--output", required=True)
    parser.add_argument("-sheet", required=True)
    args = parser.parse_args()
    assert os.environ["METRONOME_FLOW_VALUE"] == args.sheet
    content = os.environ.get("VALUE_CONTENT", "Sheet,Units\\n{sheet},1\\n").replace("{sheet}", args.sheet)
    with open(args.output, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
''')


def _argv_output(path) -> tuple[list[str], dict[str, str]]:
    """What an ARGV_SCRIPT run received: its argv and the Metronome variables."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))[1:]
    argv = [value for kind, value in rows if kind == "argv"]
    environment = {kind[4:]: value for kind, value in rows if kind.startswith("env:")}
    return argv, environment


def test_argument_helpers_split_render_quote_and_describe(tmp_path):
    assert flow_python.split_arguments("-sheet T") == ["-sheet", "T"]
    assert flow_python.split_arguments(r'-in "C:\data\my file.xlsx" -x') == ["-in", r"C:\data\my file.xlsx", "-x"]
    assert flow_python.split_arguments('"say ""hi"""') == ['say "hi"']
    assert flow_python.split_arguments('a "" b') == ["a", "", "b"]
    assert flow_python.split_arguments("   ") == []
    with pytest.raises(ValueError, match="Script arguments have an unclosed quote."):
        flow_python.split_arguments('-x "abc')
    assert flow_python.render_arguments(
        '-f "{flow}" -r {run_id} -d {date} -v {value} -u {unknown}',
        flow_name="Orders A", run_id=7, date="2026-09-17", value="T",
    ) == '-f "Orders A" -r 7 -d 2026-09-17 -v T -u {unknown}'
    # Without a date string the token stays as typed: this module never invents a calendar date.
    assert flow_python.render_arguments("-d {date} -v {value}", flow_name="x", run_id=1, date=None) == "-d {date} -v "
    assert flow_python.quote_argument("ab") == "ab" and flow_python.quote_argument("a b") == '"a b"'
    assert flow_python.quote_argument('a"b') == '"a""b"' and flow_python.quote_argument("") == '""'
    assert flow_python.split_arguments(flow_python.quote_argument('say "hi" now')) == ['say "hi" now']
    script, previous, out = tmp_path / "a.py", tmp_path / "step-1-a.csv", tmp_path / "a.csv"
    assert flow_python.script_command(script, previous, out, ["-sheet", "T U"]) == [
        sys.executable, str(script), "-sheet", "T U", "--input", str(previous), "--output", str(out)]
    environment = flow_python.step_environment(
        {"METRONOME_FLOW_INPUTS": "stale", "METRONOME_FLOW_VALUE": "stale"}, input_path="/p.csv",
        output_path="/o.csv", results_dir="/steps", step=3, steps=3, output_format="csv", flow_name="F",
        run_id=5, inputs=["/a.csv", "/b.csv"], value="U",
    )
    assert environment["METRONOME_FLOW_INPUTS"] == os.pathsep.join(["/a.csv", "/b.csv"])
    assert environment["METRONOME_FLOW_VALUE"] == "U" and environment["METRONOME_FLOW_INPUT"] == "/p.csv"
    environment = flow_python.step_environment(
        {"METRONOME_FLOW_INPUTS": "stale"}, input_path=None, output_path="/o.csv", results_dir="/steps",
        step=1, steps=1, output_format="csv", flow_name="F", run_id=5,
    )
    assert "METRONOME_FLOW_INPUTS" not in environment and environment["METRONOME_FLOW_VALUE"] == ""
    assert flow_python.describe(["/s/run_download.py", "/s/clean.py"], ["-sheet T", ""]) == "run_download.py -sheet T \u2192 clean.py"
    assert flow_python.describe([r"C:\s\run_download.py"], ["-sheet"], [["T", "U", "V"]]) == "run_download.py -sheet (3 values)"
    assert flow_python.describe(["/s/a.py"], None, [["T"]]) == "a.py (1 value)"
    assert flow_python.aligned_arguments(["-a", None], 3) == ["-a", "", ""]
    assert flow_python.aligned_arguments(["-a", "-b", "-c"], 2) == ["-a", "-b"]
    assert flow_python.aligned_values([["T", 1], "bad"], 3) == [["T", "1"], [], []]
    plan = flow_python.run_plan(["/a.py", "/b.py"], ["-sheet", ""], [["T", "U"], []])
    assert [(r["index"], r["steps"], r["row"], r["value"], r["position"], r["row_runs"]) for r in plan] == [
        (1, 3, 1, "T", 1, 2), (2, 3, 1, "U", 2, 2), (3, 3, 2, None, 1, 1)]
    section = {"scripts": ["/a.py", "/b.py"], "arguments": ["-sheet", ""], "values": [["T", "U"], ["x", "y", "z"]]}
    assert flow_python.run_count(section) == 5 and flow_python.deliverable_count(section) == 3
    assert flow_python.deliverable_count({"scripts": ["/a.py"]}) == 1 and flow_python.deliverable_count({}) == 0
    assert flow_python.filename_token("Sheet A/1:x") == "Sheet_A_1_x" and flow_python.filename_token(None) == ""


def test_python_flow_write_normalizes_arguments_and_values():
    body = flows.FlowWrite(
        name="Args", source_type="python",
        python_scripts=["/s/a.py", "/s/a.py", "", "/s/b.py"],
        python_script_arguments=["  -sheet T ", "-sheet U", "-dropped", ""],
        python_script_values=[["T", " U ", "", "T"], [], ["x"], []],
    )
    # The blank row is dropped with its arguments and values; the same script
    # runs twice with different arguments; duplicate values stay in order.
    assert body.python_scripts == ["/s/a.py", "/s/a.py", "/s/b.py"]
    assert body.python_script_arguments == ["-sheet T", "-sheet U", ""]
    assert body.python_script_values == [["T", "U", "T"], [], []]
    padded = flows.FlowWrite(name="Pad", source_type="python", python_scripts=["/s/a.py", "/s/b.py"],
                             python_script_arguments=["-a"], python_script_values=[["T"]])
    assert padded.python_script_arguments == ["-a", ""] and padded.python_script_values == [["T"], []]
    truncated = flows.FlowWrite(name="Cut", source_type="python", python_scripts=["/s/a.py"],
                                python_script_arguments=["-a", "-b"], python_script_values=[[], ["z"]])
    assert truncated.python_script_arguments == ["-a"] and truncated.python_script_values == [[]]
    same = flows.FlowWrite(name="Same", source_type="python", python_scripts=["/s/a.py", "/s/A.py"],
                           python_script_arguments=["-x", "-x"])
    assert same.python_scripts == ["/s/a.py"] and same.python_script_arguments == ["-x"]
    by_values = flows.FlowWrite(name="Values", source_type="python", python_scripts=["/s/a.py", "/s/a.py"],
                                python_script_arguments=["-s {value}", "-s {value}"],
                                python_script_values=[["T"], ["U"]])
    assert by_values.python_scripts == ["/s/a.py", "/s/a.py"] and by_values.python_script_values == [["T"], ["U"]]
    quoted = flows.FlowWrite(name="Quoted", source_type="python", python_scripts=["/s/a.py"],
                             python_script_arguments=['-in "C:\\data\\my file.xlsx"'])
    assert quoted.python_script_arguments == ['-in "C:\\data\\my file.xlsx"']

    with pytest.raises(ValueError, match="Script arguments must be a single line."):
        flows.FlowWrite(name="Lines", source_type="python", python_scripts=["/s/a.py"],
                        python_script_arguments=["-a\nb"])
    with pytest.raises(ValueError, match="Script arguments must be a single line."):
        flows.FlowWrite(name="Tab", source_type="python", python_scripts=["/s/a.py"],
                        python_script_arguments=["-a\tb"])
    with pytest.raises(ValueError, match="Script arguments must be 2000 characters or fewer."):
        flows.FlowWrite(name="Long", source_type="python", python_scripts=["/s/a.py"],
                        python_script_arguments=["x" * 2001])
    with pytest.raises(ValueError, match="Script arguments have an unclosed quote."):
        flows.FlowWrite(name="Quote", source_type="python", python_scripts=["/s/a.py"],
                        python_script_arguments=['-in "C:\\data\\open.xlsx'])
    with pytest.raises(ValueError, match="Choose at most 200 values per script."):
        flows.FlowWrite(name="Many", source_type="python", python_scripts=["/s/a.py"],
                        python_script_values=[[str(i) for i in range(201)]])
    with pytest.raises(ValueError, match="Each value must be a single line of 500 characters or fewer."):
        flows.FlowWrite(name="Line", source_type="python", python_scripts=["/s/a.py"],
                        python_script_values=[["a\nb"]])
    with pytest.raises(ValueError, match="Each value must be a single line of 500 characters or fewer."):
        flows.FlowWrite(name="Big", source_type="python", python_scripts=["/s/a.py"],
                        python_script_values=[["v" * 501]])
    with pytest.raises(ValueError, match="at most 20"):
        flows.FlowWrite(name="Lists", source_type="python", python_scripts=["/s/a.py"],
                        python_script_arguments=["-a"] * 21)

    # Several values on the last script produce several files: their names must differ.
    message = r"Several values produce several files: add \{value\} or \{index\} to the filename template\."
    with pytest.raises(ValueError, match=message):
        flows.FlowWrite(name="Bundle", source_type="python", python_scripts=["/s/a.py"],
                        python_script_values=[["T", "U"]])
    with pytest.raises(ValueError, match=message):
        flows.FlowWrite(name="Bundle", source_type="python", python_scripts=["/s/a.py"],
                        python_script_values=[["T", "U"]], filename_template="orders.csv")
    for template in ("{flow}_{value}.csv", "{flow}_{index}.csv", "{date}-{value}.xlsx"):
        saved = flows.FlowWrite(name="Bundle", source_type="python", python_scripts=["/s/a.py"],
                                python_script_values=[["T", "U"]], filename_template=template,
                                file_format="xlsx" if template.endswith(".xlsx") else "csv")
        assert saved.filename_template == template
    one = flows.FlowWrite(name="One", source_type="python", python_scripts=["/s/a.py"], python_script_values=[["T"]])
    assert one.filename_template == "{flow}.csv"
    earlier = flows.FlowWrite(name="Earlier", source_type="python", python_scripts=["/s/a.py", "/s/b.py"],
                              python_script_values=[["T", "U"], []])
    assert earlier.filename_template == "{flow}.csv"
    sql = flows.FlowWrite(name="SQL", source_type="python", python_scripts=["/s/a.py"],
                          python_script_values=[["T", "U"]], **_sql_fields())
    assert sql.filename_template == "{flow}.csv" and sql.python_script_values == [["T", "U"]]

    outlook = flows.FlowWrite(name="Mail", source_type="outlook", outlook_subject_contains="Report",
                              python_scripts=["/s/a.py"], python_script_arguments=["-a"],
                              python_script_values=[["T"]])
    local = flows.FlowWrite(name="Local", source_type="file", local_file_path="/data/in.csv",
                            python_script_arguments=["-a"], python_script_values=[["T"]])
    assert outlook.python_script_arguments == [] and outlook.python_script_values == []
    assert local.python_script_arguments == [] and local.python_script_values == []


def test_arguments_and_values_round_trip_and_label_the_run(flow_db, tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    saved = flows.create_flow(_python_flow(
        scripts, python_script_arguments=["-sheet {value}", "-x"], python_script_values=[["T", "U", "V"], []],
    ), _request())
    assert saved["python_script_arguments"] == ["-sheet {value}", "-x"]
    assert saved["python_script_values"] == [["T", "U", "V"], []]
    with database.get_db() as db:
        row = db.execute(
            "SELECT python_script_arguments_json, python_script_values_json FROM flows WHERE id=?", (saved["id"],),
        ).fetchone()
        assert json.loads(row[0]) == ["-sheet {value}", "-x"] and json.loads(row[1]) == [["T", "U", "V"], []]
        job = flows._build_job(db, saved["id"])
    assert job["python_source"]["arguments"] == ["-sheet {value}", "-x"]
    assert job["python_source"]["values"] == [["T", "U", "V"], []]
    assert flow_python.run_count(job["python_source"]) == 4
    assert flow_python.deliverable_count(job["python_source"]) == 1
    flow_paths.assert_job_paths(job)
    # A Flow saved before arguments existed reads back as empty, aligned lists.
    with database.get_db() as db:
        db.execute("UPDATE flows SET python_script_arguments_json=NULL, python_script_values_json=NULL WHERE id=?",
                   (saved["id"],))
        legacy = flows._flow_out(db, saved["id"])
        assert legacy["python_script_arguments"] == ["", ""] and legacy["python_script_values"] == [[], []]
        assert flows._build_job(db, saved["id"])["python_source"]["values"] == [[], []]

    updated = flows.update_flow(saved["id"], _python_flow(
        scripts, filename_template="{flow}_{value}.csv",
        python_script_arguments=["-a", "-sheet"], python_script_values=[[], ["T", "U", "V"]],
    ), _request())
    assert updated["python_script_arguments"] == ["-a", "-sheet"]
    assert updated["python_script_values"] == [[], ["T", "U", "V"]]
    with database.get_db() as db:
        job = flows._build_job(db, saved["id"])
        assert flow_python.deliverable_count(job["python_source"]) == 3
        now = flows._iso(flows._now())
        run_id = db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, worker_id, job_json, progress_json, created_at, started_at)
               VALUES (?, 'manual', 'running', 'worker-1', ?, ?, ?, ?)""",
            (saved["id"], json.dumps(job), json.dumps({"stage": "python_step", "message": "Running script 2 of 4"}), now, now),
        ).lastrowid
        # The progress bar counts the last row's runs: one deliverable per value.
        progress = flow_activity.row_progress(db, db.execute("SELECT * FROM flow_runs WHERE id=?", (run_id,)).fetchone())
        assert [(phase["label"], phase["total"]) for phase in progress["phases"]] == [
            ("Prepare run", 1), ("Run scripts", 3), ("Prepare files", 1), ("Finish", 1)]
        assert progress["total"] == 6
        db.execute("UPDATE flow_runs SET status='failed', error='boom', finished_at=? WHERE id=?", (now, run_id))
        context = dict(flows._flow_failure_context(db, run_id))
        context.update(owner_name="Owner", owner_email="owner@example.test")
        message = flows._flow_failure_message(context)
    assert "Python scripts: fetch_orders.py -a \u2192 enrich_orders.py -sheet (3 values)" in message["html_body"]


def test_arguments_are_rendered_split_and_passed_before_metronome_flags(tmp_path):
    folder = tmp_path / "scripts"
    folder.mkdir()
    first, second = folder / "run_download.py", folder / "clean.py"
    first.write_text(ARGV_SCRIPT, encoding="utf-8")
    second.write_text(ARGV_SCRIPT, encoding="utf-8")
    target = tmp_path / "Downloads"
    raw_second = '-sheet "T U" -d {date} -n "{flow}" -r {run_id} -k {keep}'
    job = _worker_job([first, second], target, arguments=["-sheet T", raw_second])
    (artifacts, _timings, _outcome), events, _registered = _run(job, target, tmp_path / "profile")
    final = Path(artifacts[0]["file_path"])
    step1 = final.parent / "steps" / "step-1-run_download.csv"
    today = dubai_today().isoformat()

    argv1, env1 = _argv_output(step1)
    assert argv1 == ["-sheet", "T", "--output", str(step1)]
    assert env1["METRONOME_FLOW_VALUE"] == "" and env1["METRONOME_FLOW_INPUTS"] == "<unset>"
    assert env1["METRONOME_FLOW_INPUT"] == "<unset>"
    argv2, env2 = _argv_output(final)
    rendered = ["-sheet", "T U", "-d", today, "-n", "Python orders", "-r", "31", "-k", "{keep}"]
    assert argv2 == [*rendered, "--input", str(step1), "--output", str(final)]
    assert env2["METRONOME_FLOW_INPUT"] == str(step1) and env2["METRONOME_FLOW_INPUTS"] == str(step1)

    records = artifacts[0]["python_steps"]
    assert [item["arguments"] for item in records] == [["-sheet", "T"], rendered]
    assert records[1]["arguments_text"] == f'-sheet "T U" -d {today} -n "Python orders" -r 31 -k {{keep}}'
    assert [item["value"] for item in records] == [None, None] and [item["row"] for item in records] == [1, 2]
    started = [detail for _, detail in events if detail["stage"] == "python_step"]
    assert [item["arguments"] for item in started] == [["-sheet", "T"], rendered]
    assert started[0]["message"] == "Running script 1 of 2: run_download.py -sheet T."
    assert started[1]["arguments_text"] == records[1]["arguments_text"] and started[1]["row"] == 2
    finished = [detail for _, detail in events if detail["stage"] == "python_step_complete"]
    assert [item["arguments"] for item in finished] == [["-sheet", "T"], rendered]
    assert finished[0]["message"].startswith("Script 1 of 2: run_download.py -sheet T finished in ")
    assert [item["arguments"] for item in events[-1][1]["results"]] == [["-sheet", "T"], rendered]
    assert events[0][1]["message"] == (
        "Running 2 Python script(s) in order: run_download.py -sheet T \u2192 clean.py " + raw_second + "."
    )
    assert events[0][1]["runs"] == 2 and events[0][1]["deliverables"] == 1


def test_values_expand_into_one_run_each_with_chained_inputs(tmp_path):
    folder = tmp_path / "scripts"
    folder.mkdir()
    fetch, merge = folder / "fetch.py", folder / "merge.py"
    fetch.write_text(ARGV_SCRIPT, encoding="utf-8")
    merge.write_text(ARGV_SCRIPT, encoding="utf-8")
    target = tmp_path / "Downloads"
    job = _worker_job([fetch, merge], target, arguments=["-sheet {value}", "-mode"], values=[["T", "U"], ["a b"]])
    (artifacts, _timings, _outcome), events, _registered = _run(job, target, tmp_path / "profile")
    final = Path(artifacts[0]["file_path"])
    steps_folder = final.parent / "steps"
    step1, step2 = steps_folder / "step-1-fetch.csv", steps_folder / "step-2-fetch.csv"
    argv1, env1 = _argv_output(step1)
    argv2, env2 = _argv_output(step2)
    argv3, env3 = _argv_output(final)
    # {value} is replaced in the arguments; a value without the token is appended, quoted when needed.
    assert argv1 == ["-sheet", "T", "--output", str(step1)]
    assert argv2 == ["-sheet", "U", "--input", str(step1), "--output", str(step2)]
    assert argv3 == ["-mode", "a b", "--input", str(step2), "--output", str(final)]
    assert [env["METRONOME_FLOW_VALUE"] for env in (env1, env2, env3)] == ["T", "U", "a b"]
    assert env1["METRONOME_FLOW_INPUTS"] == "<unset>" and env2["METRONOME_FLOW_INPUTS"] == "<unset>"
    assert env3["METRONOME_FLOW_INPUTS"] == os.pathsep.join([str(step1), str(step2)])
    assert [env["METRONOME_FLOW_STEP"] for env in (env1, env2, env3)] == ["1", "2", "3"]
    assert [env["METRONOME_FLOW_STEPS"] for env in (env1, env2, env3)] == ["3", "3", "3"]

    records = artifacts[0]["python_steps"]
    assert [(r["index"], r["row"], r["value"], r["arguments"]) for r in records] == [
        (1, 1, "T", ["-sheet", "T"]), (2, 1, "U", ["-sheet", "U"]), (3, 2, "a b", ["-mode", "a b"])]
    assert records[2]["arguments_text"] == '-mode "a b"'
    started = [detail for _, detail in events if detail["stage"] == "python_step"]
    assert [detail["message"] for detail in started] == [
        "Running script 1 of 3: fetch.py -sheet T.", "Running script 2 of 3: fetch.py -sheet U.",
        'Running script 3 of 3: merge.py -mode "a b".']
    assert [(detail["value"], detail["row"]) for detail in started] == [("T", 1), ("U", 1), ("a b", 2)]
    finished = [detail for _, detail in events if detail["stage"] == "python_step_complete"]
    assert [detail["value"] for detail in finished] == ["T", "U", "a b"]
    assert events[0][1]["message"] == (
        "Running 2 Python script(s) in order, 3 run(s) in total: fetch.py -sheet {value} (2 values) "
        "\u2192 merge.py -mode (1 value)."
    )
    assert events[0][1]["runs"] == 3 and events[0][1]["deliverables"] == 1
    assert events[-1][1]["message"].startswith("Ran 3 script run(s); 1 final CSV file(s): Python_orders.csv (")
    assert [(item["step"], item["value"]) for item in events[-1][1]["results"]] == [(1, "T"), (2, "U"), (3, "a b")]
    assert len(artifacts) == 1 and artifacts[0]["bundle_count"] == 1 and artifacts[0]["export_view"] is None
    assert artifacts[0]["python_value"] == "a b"


def test_last_row_values_publish_a_bundle_of_final_files(tmp_path):
    script = tmp_path / "scripts" / "export.py"
    script.parent.mkdir()
    script.write_text(VALUE_SCRIPT, encoding="utf-8")
    target = tmp_path / "Downloads"
    target.mkdir()
    job = _worker_job([script], target, output_mode="direct_replace", filename_template="{flow}_{value}.csv",
                      arguments=["-sheet {value}"], values=[["T", "U"]])
    details = []
    state = flow_worker.execute_flow(
        None, job, lambda status, detail, *args, **kwargs: details.append((status, detail)),
        tmp_path / "profile", run_id=51, register_folder=lambda path: {"ops": []},
    )
    assert sorted(item.name for item in target.iterdir()) == ["Python_orders_T.csv", "Python_orders_U.csv"]
    assert (target / "Python_orders_T.csv").read_text(encoding="utf-8") == "Sheet,Units\nT,1\n"
    assert (target / "Python_orders_U.csv").read_text(encoding="utf-8") == "Sheet,Units\nU,1\n"
    bundle = state["artifacts"]
    assert [(a["bundle_index"], a["bundle_count"], a["export_view"], a["python_value"], a["publish_status"])
            for a in bundle] == [(1, 2, "value:1", "T", "published"), (2, 2, "value:2", "U", "published")]
    assert [a["published_file_path"] for a in bundle] == [
        str(target / "Python_orders_T.csv"), str(target / "Python_orders_U.csv")]
    assert all(a["storage_scope"] == "worker_private" and a["row_count"] == 1 for a in bundle)
    assert [[step["value"] for step in a["python_steps"]] for a in bundle] == [["T"], ["U"]]
    assert details[-1][0] == "succeeded"
    assert details[-1][1]["message"] == (
        "Ran 2 Python script run(s) and saved 2 file(s): Python_orders_T.csv, Python_orders_U.csv."
    )
    complete = next(detail for _, detail in details if detail["stage"] == "python_complete")
    assert complete["deliverables"] == 2 and complete["runs"] == 2
    assert complete["message"].startswith("Ran 2 script run(s); 2 final CSV file(s): Python_orders_T.csv (")
    stages = [detail["stage"] for _, detail in details]
    assert stages.index("python_complete") < stages.index("direct_publish") < stages.index("publish_complete")

    # {index} is the run's position within the last row; a repeated name gets a numbered suffix.
    job = _worker_job([script], target, filename_template="{flow}_{index}.csv",
                      arguments=['-sheet "{value}"'], values=[["a b", "a_b", "x"]])
    (artifacts, timings, outcome), _events, _registered = _run(job, target, tmp_path / "profile-2", run_id=52)
    assert [a["filename"] for a in artifacts] == ["Python_orders_1.csv", "Python_orders_2.csv", "Python_orders_3.csv"]
    assert [a["python_value"] for a in artifacts] == ["a b", "a_b", "x"]
    assert outcome["sql_artifacts"] == artifacts and timings[-1]["item_count"] == 3
    job = _worker_job([script], target, filename_template="{flow}_{value}.csv",
                      arguments=['-sheet "{value}"'], values=[["a b", "a_b"]])
    (artifacts, _timings, _outcome), _events, _registered = _run(job, target, tmp_path / "profile-3", run_id=53)
    assert [a["filename"] for a in artifacts] == ["Python_orders_a_b.csv", "Python_orders_a_b (2).csv"]
    assert all(Path(a["file_path"]).is_file() for a in artifacts)


def test_bundle_of_final_files_is_normalized_and_loaded_into_sql(tmp_path, monkeypatch):
    script = tmp_path / "scripts" / "export.py"
    script.parent.mkdir()
    script.write_text(VALUE_SCRIPT, encoding="utf-8")
    monkeypatch.setenv("VALUE_CONTENT", "\ufeffSheet;Units\n{sheet};1\n")
    target = tmp_path / "Downloads"
    target.mkdir()
    job = _worker_job([script], target, sql=True, arguments=["-sheet {value}"], values=[["T", "U"]])
    loaded, events, details = [], [], []

    def load(items, sql_target, **kwargs):
        events.append("sql")
        assert [item["status"] for item in items] == ["saved", "saved"]
        assert [item["bundle_index"] for item in items] == [1, 2]
        for item in items:
            with Path(item["file_path"]).open(encoding="utf-8-sig", newline="") as handle:
                loaded.append(list(csv.reader(handle)))
        assert sql_target["table"] == "orders"
        return {"rows_written": 2, "files_loaded": len(items), "target": "warehouse.reporting.orders"}

    monkeypatch.setattr(flow_sql, "load_artifacts", load)
    monkeypatch.setattr(flow_view_refresh, "execute_after_sql", lambda *args, **kwargs: events.append("refresh"))
    flow_worker.execute_flow(
        None, job, lambda status, detail, *args, **kwargs: details.append((status, detail)),
        tmp_path / "profile", run_id=61, register_folder=lambda path: {"ops": []},
    )
    assert events == ["sql", "refresh"]
    # Both final CSV files were normalized (BOM and ';' removed) before the load.
    assert loaded == [[["Sheet", "Units"], ["T", "1"]], [["Sheet", "Units"], ["U", "1"]]]
    assert details[-1][0] == "succeeded"
    assert details[-1][1]["message"] == (
        "Ran 2 Python script run(s) and saved 2 file(s): Python_orders.csv, Python_orders (2).csv. "
        "Committed 2 row(s) to warehouse.reporting.orders."
    )
    inserted = next(detail for _, detail in details if detail["stage"] == "sql_insertion_complete")
    assert inserted["files_loaded"] == 2


def test_invalid_rendered_arguments_fail_the_step_with_a_record(tmp_path):
    folder = tmp_path / "scripts"
    folder.mkdir()
    script = folder / "run.py"
    script.write_text(ARGV_SCRIPT, encoding="utf-8")
    target = tmp_path / "Downloads"
    target.mkdir()
    # A value that carries a quote breaks a quoted {value} span only at run time; the step fails closed.
    job = _worker_job([script], target, arguments=['-n "{value}"'], values=[['a"b']])
    events = []
    with pytest.raises(RuntimeError, match=r"run\.py \(step 1 of 1\) has invalid arguments: Script arguments have an unclosed quote\."):
        flow_worker.execute_python_job(
            job, lambda _status, detail: events.append(detail), tmp_path / "profile",
            run_id=71, register_folder=lambda path: {"ops": []},
        )
    failed = [detail for detail in events if detail["stage"] == "python_step_failed"]
    assert len(failed) == 1 and failed[0]["value"] == 'a"b' and failed[0]["arguments"] == []
    assert not any(detail["stage"] in ("python_step", "python_complete") for detail in events)


def test_worker_claim_requires_the_arguments_capability_only_when_arguments_or_values_are_used(flow_db, tmp_path):
    scripts = _write_scripts(tmp_path / "scripts")
    plain = flows.create_flow(_python_flow(scripts, name="Plain chain"), _request())
    with_values = flows.create_flow(_python_flow(
        scripts, name="Per sheet", python_script_arguments=["-sheet", ""],
        python_script_values=[["T", "U"], []],
    ), _request())
    with database.get_db() as db:
        for saved in (plain, with_values):
            job = flows._build_job(db, saved["id"])
            db.execute(
                """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json, created_at)
                   VALUES (?, 'scheduled', 'queued', ?, ?)""",
                (saved["id"], json.dumps(job), flows._iso(flows._now())),
            )
        plain_job = flows._build_job(db, plain["id"])
        values_job = flows._build_job(db, with_values["id"])
    assert flow_python.requires_arguments_capability(plain_job["python_source"]) is False
    assert flow_python.requires_arguments_capability(values_job["python_source"]) is True

    # A worker from the previous release knows the adapter but not the
    # arguments contract: it may take the plain chain, never the one with values.
    flows.register_worker(flows.WorkerRegister(
        worker_id="adapter-only", display_name="Adapter only",
        capabilities={"adapters": ["web_export", "python_script"], "headed": False, "shared_flow_artifacts": True},
    ))
    first = flows.claim_run("adapter-only")["run"]
    assert first["flow_id"] == plain["id"]
    flows.update_run("adapter-only", first["id"], flows.WorkerProgress(status="cancelled", progress={"stage": "cancelled"}))
    assert flows.claim_run("adapter-only")["run"] is None

    flows.register_worker(flows.WorkerRegister(
        worker_id="current", display_name="Current worker",
        capabilities={"adapters": ["web_export", "python_script"], flow_python.ARGUMENTS_CAPABILITY: True,
                      "headed": False, "shared_flow_artifacts": True},
    ))
    assert flows.claim_run("current")["run"]["flow_id"] == with_values["id"]
