import sqlite3

import pytest

from app import database
from app.routers import flows, pipelines
from app.scanner import pbi_sync
from app.scanner.findings import sync_managed_actions


@pytest.fixture()
def alert_db(tmp_path, monkeypatch):
    path = str(tmp_path / "alerts.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


def _occurrence(focus_id, *, error="stage failed"):
    return {
        "focus_type": "flow_run",
        "focus_id": focus_id,
        "observed_at": "2026-08-27T12:00:00+00:00",
        "summary": f"Flow run #{focus_id} failed.",
        "evidence": {"status": "failed", "stage": "download", "error": error},
    }


def test_occurrences_are_immutable_revisioned_and_supersede_old_agent_runs(alert_db):
    finding = {
        "fingerprint": "flow_failed:9",
        "flow_id": None,
        "notes": "first failure",
        "occurrence": _occurrence(101, error=r"token=top-secret C:\\private\\run.log"),
    }
    with database.get_db() as db:
        first = sync_managed_actions(
            db, "flow_failed", [finding], "2026-08-27T12:00:00+00:00"
        )
        action_id = first["action_ids"]["flow_failed:9"]
        action = db.execute(
            "SELECT evidence_revision, evidence_hash FROM actions WHERE id=?", (action_id,)
        ).fetchone()
        assert action["evidence_revision"] == 1
        assert len(action["evidence_hash"]) == 64
        occurrence = db.execute(
            "SELECT * FROM action_occurrences WHERE action_id=?", (action_id,)
        ).fetchone()
        assert occurrence["focus_type"] == "flow_run"
        assert occurrence["focus_id"] == "101"
        assert "top-secret" not in occurrence["evidence_json"]
        assert "private" not in occurrence["evidence_json"]

        db.execute(
            """INSERT INTO agent_runs
                   (mode, question, focus_type, focus_id, status, model, provider_mode,
                    prompt_version, action_id, action_evidence_revision)
               VALUES ('incident', 'why', 'flow_run', '101', 'completed', 'test',
                       'mock', 'test-v1', ?, 1)""",
            (action_id,),
        )

        # Re-reading the same exact run is idempotent even if a caller supplies
        # different text later; the immutable occurrence is not rewritten.
        duplicate = dict(finding)
        duplicate["occurrence"] = _occurrence(101, error="different later text")
        repeated = sync_managed_actions(
            db, "flow_failed", [duplicate], "2026-08-27T12:05:00+00:00"
        )
        assert repeated["occurrences_created"] == 0
        assert db.execute(
            "SELECT evidence_revision FROM actions WHERE id=?", (action_id,)
        ).fetchone()[0] == 1

        finding["occurrence"] = _occurrence(102)
        advanced = sync_managed_actions(
            db, "flow_failed", [finding], "2026-08-27T12:10:00+00:00"
        )
        assert advanced["occurrences_created"] == 1
        assert db.execute(
            "SELECT evidence_revision FROM actions WHERE id=?", (action_id,)
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT COUNT(*) FROM action_occurrences WHERE action_id=?", (action_id,)
        ).fetchone()[0] == 2
        old_agent = db.execute(
            "SELECT superseded_at, superseded_reason FROM agent_runs WHERE action_id=?",
            (action_id,),
        ).fetchone()
        assert old_agent["superseded_at"]
        assert old_agent["superseded_reason"] == "alert_evidence_changed"

        db.execute(
            """INSERT INTO agent_runs
                   (mode, question, focus_type, focus_id, status, model, provider_mode,
                    prompt_version, action_id, action_evidence_revision)
               VALUES ('incident', 'why now', 'flow_run', '102', 'completed', 'test',
                       'mock', 'test-v1', ?, 2)""",
            (action_id,),
        )
        db.execute("UPDATE actions SET status='expected' WHERE id=?", (action_id,))
        current_agent = db.execute(
            """SELECT superseded_reason FROM agent_runs
               WHERE action_id=? AND action_evidence_revision=2""",
            (action_id,),
        ).fetchone()
        assert current_agent["superseded_reason"] == "alert_expected"


def test_migration_repairs_duplicate_active_fingerprints_and_sync_reuses_survivor(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "legacy-duplicates.db")
    monkeypatch.setattr(database, "DB_PATH", path)
    connection = sqlite3.connect(path)
    connection.executescript(database.SCHEMA)
    connection.execute(
        """INSERT INTO actions(type, status, fingerprint, notes)
           VALUES ('flow_failed', 'open', 'flow_failed:1', 'oldest')"""
    )
    connection.execute(
        """INSERT INTO actions
               (type, status, fingerprint, notes, assigned_to, resolved_at)
           VALUES ('flow_failed', 'expected', 'flow_failed:1', 'triaged duplicate',
                   'Incident Lead', '2026-08-27T12:00:00+00:00')"""
    )
    connection.commit()
    connection.close()

    database.init_db()
    with database.get_db() as db:
        before = db.execute(
            """SELECT id, status, assigned_to, notes FROM actions
               WHERE fingerprint='flow_failed:1' ORDER BY id"""
        ).fetchall()
        assert [row["status"] for row in before] == ["expected", "resolved"]
        assert before[0]["assigned_to"] == "Incident Lead"
        assert before[0]["notes"] == "triaged duplicate"
        result = sync_managed_actions(
            db,
            "flow_failed",
            [{"fingerprint": "flow_failed:1", "notes": "current"}],
            "2026-08-27T13:00:00+00:00",
        )
        assert result["created"] == 0
        active = db.execute(
            """SELECT id FROM actions WHERE fingerprint='flow_failed:1'
               AND status IN ('open','acknowledged','investigating','expected')"""
        ).fetchall()
        assert [row["id"] for row in active] == [before[0]["id"]]
        assert db.execute(
            "SELECT assigned_to FROM actions WHERE id=?", (before[0]["id"],)
        ).fetchone()[0] == "Incident Lead"


def test_flow_failure_alert_tracks_exact_runs_owner_and_resolves_on_success(alert_db):
    with database.get_db() as db:
        db.execute(
            "INSERT INTO people(id, name, role) VALUES (1, 'Flow Owner', 'BI')"
        )
        db.execute(
            "INSERT INTO flow_sites(id, name, adapter) VALUES (101, 'Portal', 'web_export')"
        )
        db.execute(
            """INSERT INTO flow_reports(id, site_id, name, report_url)
               VALUES (101, 101, 'Export', 'https://example.test/export')"""
        )
        db.execute(
            """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    last_status, last_error, owner_person_id)
               VALUES (101, 'Owned Flow', 101, 101, 'C:\\target', 'file.csv',
                       'failed', 'download failed', 1)"""
        )
        db.execute(
            """INSERT INTO flow_runs
                   (id, flow_id, trigger_type, status, job_json, error, finished_at)
               VALUES (111, 101, 'manual', 'failed', '{}', 'download failed',
                       '2026-08-27T10:00:00+00:00')"""
        )
        db.execute(
            """INSERT INTO flow_run_events(run_id, status, stage)
               VALUES (111, 'failed', 'download')"""
        )
        first = flows._sync_flow_failure_actions(db, "2026-08-27T10:00:00+00:00")
        assert first["created"] == 1
        action = db.execute(
            """SELECT id, assigned_to, evidence_revision FROM actions
               WHERE fingerprint='flow_failed:101'"""
        ).fetchone()
        assert action["assigned_to"] == "Flow Owner"
        assert action["evidence_revision"] == 1
        assert db.execute(
            "SELECT focus_type, focus_id FROM action_occurrences WHERE action_id=?",
            (action["id"],),
        ).fetchone()[:] == ("flow_run", "111")

        # The detector supplies the initial owner, but subsequent detector
        # passes must not undo a manual incident assignment.
        db.execute(
            "UPDATE actions SET assigned_to='Incident Lead' WHERE id=?", (action["id"],)
        )

        db.execute(
            """INSERT INTO flow_runs
                   (id, flow_id, trigger_type, status, job_json, error, finished_at)
               VALUES (112, 101, 'scheduled', 'failed', '{}', 'second failure',
                       '2026-08-27T11:00:00+00:00')"""
        )
        db.execute(
            "UPDATE flows SET last_error='second failure' WHERE id=101"
        )
        flows._sync_flow_failure_actions(db, "2026-08-27T11:00:00+00:00")
        refreshed = db.execute(
            "SELECT evidence_revision, assigned_to FROM actions WHERE id=?", (action["id"],)
        ).fetchone()
        assert refreshed["evidence_revision"] == 2
        assert refreshed["assigned_to"] == "Incident Lead"

        db.execute("UPDATE actions SET assigned_to=NULL WHERE id=?", (action["id"],))
        flows._sync_flow_failure_actions(db, "2026-08-27T11:05:00+00:00")
        assert db.execute(
            "SELECT assigned_to FROM actions WHERE id=?", (action["id"],)
        ).fetchone()[0] is None

        # A cancelled retry is not evidence that the prior failure recovered.
        db.execute(
            """INSERT INTO flow_runs
                   (id, flow_id, trigger_type, status, job_json, error, finished_at)
               VALUES (113, 101, 'manual', 'cancelled', '{}', 'cancelled by user',
                       '2026-08-27T11:30:00+00:00')"""
        )
        db.execute(
            "UPDATE flows SET last_status='cancelled', last_error='cancelled by user' WHERE id=101"
        )
        flows._sync_flow_failure_actions(db, "2026-08-27T11:30:00+00:00")
        assert db.execute(
            "SELECT status FROM actions WHERE id=?", (action["id"],)
        ).fetchone()[0] == "open"

        db.execute(
            """INSERT INTO flow_runs
                   (id, flow_id, trigger_type, status, job_json, finished_at)
               VALUES (114, 101, 'manual', 'succeeded', '{}',
                       '2026-08-27T12:00:00+00:00')"""
        )
        db.execute("UPDATE flows SET last_status='succeeded', last_error=NULL WHERE id=101")
        flows._sync_flow_failure_actions(db, "2026-08-27T12:00:00+00:00")
        assert db.execute(
            "SELECT status FROM actions WHERE id=?", (action["id"],)
        ).fetchone()[0] == "resolved"


def test_pipeline_failure_alert_persists_during_retry_then_resolves_on_success(alert_db):
    with database.get_db() as db:
        db.execute(
            "INSERT INTO reports(id, name, owner) VALUES (1, 'Sales', 'Report Owner')"
        )
        first_run = db.execute(
            """INSERT INTO pipeline_runs(report_id, plan_hash, plan_json)
               VALUES (1, 'hash-1', '{}')"""
        ).lastrowid
    pipelines._fail_pipeline(first_run, "Flow stage failed")

    with database.get_db() as db:
        action = db.execute(
            """SELECT id, status, assigned_to, evidence_revision FROM actions
               WHERE fingerprint='pipeline_failed:1'"""
        ).fetchone()
        assert dict(action) == {
            "id": action["id"],
            "status": "open",
            "assigned_to": "Report Owner",
            "evidence_revision": 1,
        }
        assert db.execute(
            "SELECT focus_type, focus_id FROM action_occurrences WHERE action_id=?",
            (action["id"],),
        ).fetchone()[:] == ("pipeline_run", str(first_run))
        retry_run = db.execute(
            """INSERT INTO pipeline_runs(report_id, plan_hash, plan_json)
               VALUES (1, 'hash-2', '{}')"""
        ).lastrowid
        pipelines._sync_pipeline_failure_actions(db, "2026-08-27T13:00:00+00:00")
        assert db.execute(
            "SELECT status FROM actions WHERE id=?", (action["id"],)
        ).fetchone()[0] == "open"

    pipelines._succeed_pipeline(retry_run)
    with database.get_db() as db:
        assert db.execute(
            "SELECT status FROM actions WHERE id=?", (action["id"],)
        ).fetchone()[0] == "resolved"


def test_powerbi_reconnect_is_canonical_action_and_legacy_read_row(alert_db):
    pbi_sync._create_reconnect_alert(
        "Power BI sign-in expired or was revoked: reconnect required.", 77
    )
    with database.get_db() as db:
        action = db.execute(
            """SELECT id, status, evidence_revision FROM actions
               WHERE type='pbi_reconnect'"""
        ).fetchone()
        assert action["status"] == "open"
        assert action["evidence_revision"] == 1
        occurrence = db.execute(
            "SELECT focus_type, focus_id FROM action_occurrences WHERE action_id=?",
            (action["id"],),
        ).fetchone()
        assert occurrence[:] == ("pbi_sync", "77")
        assert db.execute(
            """SELECT COUNT(*) FROM alerts
               WHERE message LIKE 'Power BI sign-in%' AND resolution_status IS NULL"""
        ).fetchone()[0] == 1

        pbi_sync._resolve_reconnect_alerts(db, "2026-08-27T14:00:00+00:00")
        assert db.execute(
            "SELECT status FROM actions WHERE id=?", (action["id"],)
        ).fetchone()[0] == "resolved"
        assert db.execute(
            """SELECT resolution_status FROM alerts
               WHERE message LIKE 'Power BI sign-in%'"""
        ).fetchone()[0] == "resolved"

    pbi_sync._create_reconnect_alert(
        "Power BI sign-in expired during usage sync: reconnect required.", 78
    )
    pbi_sync.import_pbi_usage_data({"entries": [], "days_synced": []})
    with database.get_db() as db:
        assert db.execute(
            "SELECT status FROM actions WHERE type='pbi_reconnect' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0] == "resolved"
        assert db.execute(
            """SELECT resolution_status FROM alerts
               WHERE message LIKE 'Power BI sign-in%' ORDER BY id DESC LIMIT 1"""
        ).fetchone()[0] == "resolved"
