import json
import subprocess
import sys
from pathlib import Path

from app import database
from test_flow_recordings import definition as valid_definition, draft_job
from test_flows import flow_db  # noqa: F401 - pytest fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import diagnose_run  # noqa: E402


PRIVATE = {
    "url": "https://asap.private.example/report?token=hidden",
    "share": r"\\fileserver\bi\FOTA\weekly.xlsx",
    "entered": "PrivateCustomerName",
    "cookie": "Cookie: session=abcd1234; sso=efgh",
    "email": "owner@example.com",
}


def _seed_failed_recorded_run():
    saved, job = draft_job()
    definition = job["recording"]["definition"]
    definition["parameters"] = {}
    definition["steps"] = [
        {"id": "open", "action": "goto", "page": "page", "args": [PRIVATE["url"]]},
        {"id": "secret", "action": "fill", "page": "page", "args": [PRIVATE["entered"]],
         "locator": [{"method": "get_by_label", "args": ["Search"]}]},
        {"id": "export", "action": "click", "page": "page",
         "locator": [{"method": "get_by_role", "args": ["button"], "kwargs": {"name": "Export"}}],
         "output": {"format": "xlsx", "completion": "staging", "min_rows": 2}},
    ]
    job["flow"]["execution_method"] = "recorded"
    with database.get_db() as db:
        revision = db.execute(
            """INSERT INTO flow_recording_revisions(flow_id, definition_json, status, created_at)
               VALUES (?, ?, 'validated', '2026-09-09T00:00:00Z')""",
            (saved["id"], json.dumps(valid_definition())),
        ).lastrowid
        db.execute("UPDATE flows SET recording_revision_id=? WHERE id=?", (revision, saved["id"]))
        db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json, created_at, started_at, finished_at)
               VALUES (?, 'manual', 'succeeded', ?, '2026-09-10T08:00:00+00:00',
                       '2026-09-10T08:00:01+00:00', '2026-09-10T08:03:00+00:00')""",
            (saved["id"], json.dumps(job)),
        )
        run_id = db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, requested_by, job_json, progress_json,
                                     error, created_at, started_at, finished_at)
               VALUES (?, 'manual', 'failed', 'Analyst', ?, ?, ?, '2026-09-11T08:00:00+00:00',
                       '2026-09-11T08:00:01+00:00', '2026-09-11T08:02:00+00:00')""",
            (
                saved["id"], json.dumps(job),
                json.dumps({"stage": "failed", "message": f"Copy to {PRIVATE['share']} failed"}),
                f"Locator.fill: Timeout 120000ms exceeded.\nCall log:\n{PRIVATE['entered']}",
            ),
        ).lastrowid
        events = [
            ("running", "recorded_action", "Sending action.", {
                "stage": "recorded_action", "step_id": "secret", "revision": 1, "action": "fill",
                "step_outcomes": {"open": {"outcome": "completed", "message": "Action completed."}},
                "diagnostic": {"version": 1, "phase": "running", "call": {"action": "fill", "page": "page"}},
            }, None, None),
            ("running", "recorded_action", "Target unavailable.", {
                "stage": "recorded_action", "step_id": "secret", "revision": 1, "action": "fill",
                "step_outcomes": {
                    "open": {"outcome": "completed", "message": "Action completed."},
                    "secret": {"outcome": "failed", "failure_reason": "recorded_action_failed",
                               "message": "Browser action timed out after 120000 ms."},
                },
                "diagnostic": {"version": 1, "phase": "action_failed", "error_type": "TimeoutError",
                               "exception": {"type": "TimeoutError", "signals": ["timeout"],
                                             "raw_message": "excluded: may contain entered values or page content"},
                               "target": {"match_count": 0}},
                "screenshot": f"C:\\profiles\\headless\\diagnostics\\failure-03.png (fill, {PRIVATE['email']})",
            }, None, None),
            ("failed", "failed", f"Copy to {PRIVATE['share']} failed", {"stage": "failed"},
             f"{PRIVATE['cookie']}\n{PRIVATE['url']}",
             f"Traceback (most recent call last):\n  File \"{PRIVATE['share']}\", line 1\n{PRIVATE['entered']}\n{PRIVATE['url']}"),
        ]
        for status, stage, message, details, error, traceback in events:
            db.execute(
                """INSERT INTO flow_run_events(run_id, status, stage, message, details_json, error, traceback, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '2026-09-11T08:01:00+00:00')""",
                (run_id, status, stage, message, json.dumps(details), error, traceback),
            )
    return saved, run_id


def test_bundle_keeps_structure_and_excludes_private_values(flow_db, tmp_path):
    saved, run_id = _seed_failed_recorded_run()
    folder = diagnose_run.write_bundle(run_id, tmp_path / "diagnosis")
    assert folder == tmp_path / "diagnosis" / f"run-{run_id}"
    names = sorted(item.name for item in folder.iterdir())
    assert names == ["README.md", "bundle.json", "findings.md", "steps.json"]

    bundle = json.loads((folder / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["bundle_version"] == diagnose_run.BUNDLE_VERSION
    assert bundle["run"]["data"]["run"]["status"] == "failed"
    assert bundle["run"]["data"]["run"]["flow_id"] == saved["id"]
    assert bundle["run"]["data"]["recovery_preflight"]["resume"]["status"]
    assert bundle["events"]["data"]["returned"] == 3
    failed_event = bundle["events"]["data"]["events"][-1]
    assert failed_event["stage"] == "failed"
    assert "[local path]" in failed_event["message"]
    assert bundle["comparison"]["data"]["baseline"]["status"] == "succeeded"
    assert bundle["run"]["evidence"][0]["deep_link"] == f"/flow-runs/{run_id}"

    recording = bundle["recording"]
    assert recording["context"]["failing_step_id"] == "secret"
    assert recording["context"]["screenshot_files"] == ["failure-03.png"]
    assert recording["context"]["adapter"] == "asap_portal"
    assert recording["failing_step"]["execution_contract"]["arguments"] == "omitted"
    assert recording["failing_step"]["outcome"]["failure_reason"] == "recorded_action_failed"

    steps = json.loads((folder / "steps.json").read_text(encoding="utf-8"))
    assert [step["step_id"] for step in steps] == ["open", "secret", "export"]
    assert steps[2]["output_contract"] == {"format": "xlsx", "completion": "staging", "min_rows": 2}
    assert steps[0]["outcome"]["outcome"] == "completed"

    everything = "\n".join(path.read_text(encoding="utf-8") for path in folder.iterdir())
    for forbidden in (*PRIVATE.values(), "asap.private.example", "token=hidden", "fileserver",
                      "session=abcd1234", "C:\\profiles", "Call log:", "raw_message"):
        assert forbidden not in everything, forbidden
    assert "[entered value]" not in (folder / "README.md").read_text(encoding="utf-8")


def test_bundle_for_catalog_run_has_no_recording_section(flow_db, tmp_path):
    saved, job = draft_job()
    job.pop("recording", None)
    with database.get_db() as db:
        run_id = db.execute(
            """INSERT INTO flow_runs(flow_id, trigger_type, status, job_json, error, created_at)
               VALUES (?, 'scheduled', 'failed', ?, 'Sign-in required.', '2026-09-11T08:00:00+00:00')""",
            (saved["id"], json.dumps(job)),
        ).lastrowid
    folder = diagnose_run.write_bundle(run_id, tmp_path)
    bundle = json.loads((folder / "bundle.json").read_text(encoding="utf-8"))
    assert bundle["recording"] is None
    assert bundle["comparison"] == {"unavailable": "No earlier successful run is available for comparison."}
    assert not (folder / "steps.json").exists()


def test_findings_template_survives_regeneration(flow_db, tmp_path):
    saved, run_id = _seed_failed_recorded_run()
    folder = diagnose_run.write_bundle(run_id, tmp_path)
    (folder / "findings.md").write_text("# kept\n", encoding="utf-8")
    diagnose_run.write_bundle(run_id, tmp_path)
    assert (folder / "findings.md").read_text(encoding="utf-8") == "# kept\n"


def test_unknown_run_exits_with_code_two(flow_db, tmp_path, capsys):
    output = tmp_path / "out"
    assert diagnose_run.main(["--run-id", "424242", "--output", str(output)]) == 2
    assert "Flow run not found" in capsys.readouterr().err
    assert not output.exists()


def test_command_line_help_runs_without_a_database():
    completed = subprocess.run(
        [sys.executable, str(Path(diagnose_run.__file__)), "--help"],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert completed.returncode == 0
    assert "--run-id" in completed.stdout
