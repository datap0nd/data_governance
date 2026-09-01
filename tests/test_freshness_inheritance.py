import inspect
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import database
from app import freshness
from app.freshness import evaluate_timed_rule, next_occurrence, occurrence_at, schedule_rule
from app.freshness_inheritance import (
    initialize_freshness_data,
    normalize_cron_schedule,
    reconcile_all_sources,
    reconcile_file_binding,
    reconcile_source,
    upsert_schedule_evidence,
)


@pytest.fixture()
def freshness_db(monkeypatch, request):
    root = Path(tempfile.mkdtemp(prefix="freshness-test-", dir=Path.cwd()))
    request.addfinalizer(lambda: shutil.rmtree(root, ignore_errors=True))
    path = root / "freshness.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.init_db()
    with database.get_db() as db:
        db.execute("INSERT INTO flow_sites(id,name,adapter) VALUES (100,'Site','web_export')")
        db.execute("INSERT INTO flow_reports(id,site_id,name,report_url) VALUES (100,100,'Report','https://example.test/report')")
    return path


def _source(db, source_id, name, *, source_type="postgresql", connection_info=None):
    db.execute(
        """INSERT INTO sources(id,name,type,connection_info,discovered_by,freshness_mode)
           VALUES (?,?,?,?, 'manual','inherit')""",
        (source_id, name, source_type, connection_info),
    )


def _flow(
    db, flow_id, name, *, source_id=None, schedule_type="daily",
    schedule_time="06:00", schedule_days="[]", schedule_day=None,
    enabled=1, output_mode="run_folders", target_folder=r"C:\output",
    filename_template="output.csv", sql_handoff_enabled=1,
):
    db.execute(
        """INSERT INTO flows
           (id,name,site_id,report_id,target_folder,filename_template,output_mode,
            enabled,schedule_type,schedule_time,schedule_days,schedule_day,
            sql_handoff_enabled,sql_target_source_id,freshness_effective_from_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            flow_id, name, 100, 100, target_folder, filename_template, output_mode,
            enabled, schedule_type, schedule_time, schedule_days, schedule_day,
            sql_handoff_enabled, source_id, "2026-01-01T00:00:00Z",
        ),
    )


def test_recurrence_uses_lisbon_dst_fold_gap_and_true_monthly(monkeypatch):
    monkeypatch.setattr(freshness, "FLOW_TIMEZONE", "Europe/Lisbon")
    gap = schedule_rule("daily", "01:30", timezone_name="Europe/Lisbon")
    # 01:30 does not exist on the spring-forward date, so dispatch moves to
    # the first valid local instant (02:00 WEST == 01:00 UTC).
    assert occurrence_at(gap, datetime(2026, 3, 29).date()) == datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)

    fold = schedule_rule("daily", "01:30", timezone_name="Europe/Lisbon")
    assert occurrence_at(fold, datetime(2026, 10, 25).date()) == datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)

    monthly = schedule_rule("monthly", "08:00", schedule_day=31, timezone_name="Europe/Lisbon")
    result = next_occurrence(monthly, after=datetime(2026, 4, 1, tzinfo=timezone.utc))
    assert result.astimezone(freshness.ZoneInfo("Europe/Lisbon")).date().isoformat() == "2026-05-31"


def test_timed_health_enforces_only_post_baseline_occurrences():
    rule = schedule_rule("daily", "06:00", timezone_name="UTC")
    baseline = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    assert evaluate_timed_rule(
        rule, evidence_at=None, baseline_at=baseline,
        as_of=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
    )["status"] == "pending"
    assert evaluate_timed_rule(
        rule, evidence_at=None, baseline_at=baseline,
        as_of=datetime(2026, 9, 2, 6, 0, 1, tzinfo=timezone.utc),
    )["status"] == "overdue"
    assert evaluate_timed_rule(
        rule, evidence_at=datetime(2026, 9, 1, 6, 1, tzinfo=timezone.utc),
        baseline_at=baseline,
        as_of=datetime(2026, 9, 2, 6, 0, 1, tzinfo=timezone.utc),
    )["status"] == "healthy"


def test_legacy_flow_and_source_naive_timestamps_use_distinct_conventions(monkeypatch):
    monkeypatch.setattr(freshness, "FLOW_TIMEZONE", "Europe/Lisbon")
    flow_value = freshness.parse_flow_timestamp("2026-07-01T08:00:00")
    source_value = freshness.parse_source_timestamp("2026-07-01T08:00:00")
    assert flow_value == datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)
    assert source_value == datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("expression", "kind"),
    [
        ("0 6 * * *", "daily"),
        ("15 7 * * MON-FRI", "weekly"),
        ("30 8 31 * *", "monthly"),
        ("0 * * * *", None),
        ("0 6 1 * MON", None),
        ("0 6 * 1 *", None),
        ("*/5 6 * * *", None),
    ],
)
def test_cron_normalization_is_exact(expression, kind):
    rule = normalize_cron_schedule(expression, "Europe/Lisbon")
    assert (rule or {}).get("type") == kind


def test_sql_inheritance_accepts_matching_producers_and_flags_conflicts(freshness_db):
    with database.get_db() as db:
        _source(db, 1, "Target")
        _flow(db, 1, "Daily A", source_id=1)
        _flow(db, 2, "Daily B", source_id=1)
        first = reconcile_source(db, 1)
        row = db.execute("SELECT * FROM sources WHERE id=1").fetchone()
        assert first["status"] == "mapped"
        assert row["freshness_rule_origin"] == "flow_schedule"
        assert row["freshness_producer_flow_ids"] == "[1,2]"

        db.execute("UPDATE flows SET schedule_time='07:00' WHERE id=2")
        conflict = reconcile_source(db, 1)
        row = db.execute("SELECT * FROM sources WHERE id=1").fetchone()
        assert conflict["status"] == "conflict"
        assert "flow_schedules_disagree" in row["freshness_conflicts_json"]
        assert row["freshness_rule_type"] is None


def test_authoritative_source_schedule_wins_with_visible_flow_collision(freshness_db):
    with database.get_db() as db:
        _source(db, 1, "Target")
        _flow(db, 1, "Flow", source_id=1, schedule_time="06:00")
        upsert_schedule_evidence(
            db, source_id=1, origin="pg_cron", external_id="44",
            expression="0 7 * * *", timezone_name="UTC", active=True,
            authoritative=True,
        )
        result = reconcile_source(db, 1)
        row = db.execute("SELECT * FROM sources WHERE id=1").fetchone()
        assert result["status"] == "mapped"
        assert row["freshness_rule_origin"] == "source_schedule"
        assert row["freshness_schedule_time"] == "07:00"
        assert "schedule_collision" in row["freshness_warnings_json"]


def test_successful_reconciliation_never_overwrites_manual_or_disabled(freshness_db):
    with database.get_db() as db:
        _source(db, 1, "Manual")
        _source(db, 2, "Disabled")
        db.execute(
            """UPDATE sources SET freshness_mode='manual', freshness_rule_type='custom',
               custom_fresh_days=9, freshness_rule_origin='manual', freshness_rule_status='manual'
               WHERE id=1"""
        )
        db.execute("UPDATE sources SET freshness_mode='disabled' WHERE id=2")
        _flow(db, 1, "Flow 1", source_id=1)
        _flow(db, 2, "Flow 2", source_id=2)
        counts = reconcile_all_sources(db, recalculate_probes=True)
        manual = db.execute("SELECT * FROM sources WHERE id=1").fetchone()
        disabled = db.execute("SELECT * FROM sources WHERE id=2").fetchone()
        assert counts["skipped"] == 2
        assert manual["custom_fresh_days"] == 9
        assert manual["freshness_rule_type"] == "custom"
        assert disabled["freshness_rule_type"] is None


def test_exact_direct_file_binding_rejects_dynamic_and_ambiguous_targets(freshness_db):
    with database.get_db() as db:
        _source(db, 1, "File", source_type="csv", connection_info=r"C:\output\result.csv")
        _flow(
            db, 1, "File Flow", sql_handoff_enabled=0, output_mode="direct_replace",
            target_folder=r"C:\output", filename_template="result.csv",
        )
        confirmed = reconcile_file_binding(db, 1)
        assert confirmed["status"] == "confirmed"
        assert db.execute(
            "SELECT source_id FROM flow_file_source_bindings WHERE flow_id=1 AND active=1"
        ).fetchone()["source_id"] == 1
        _source(db, 3, "SQL target")
        db.execute(
            "UPDATE flows SET sql_handoff_enabled=1, sql_target_source_id=3 WHERE id=1"
        )
        assert reconcile_source(db, 1)["status"] == "mapped"
        assert reconcile_source(db, 3)["status"] == "mapped"

        db.execute("UPDATE flows SET filename_template='{date}.csv' WHERE id=1")
        assert reconcile_file_binding(db, 1)["status"] == "unresolved"
        assert db.execute(
            "SELECT COUNT(*) AS n FROM flow_file_source_bindings WHERE flow_id=1 AND active=1"
        ).fetchone()["n"] == 0

        db.execute("UPDATE flows SET filename_template='result.csv' WHERE id=1")
        _source(db, 2, "Duplicate", source_type="csv", connection_info=r"c:/OUTPUT/result.csv")
        assert reconcile_file_binding(db, 1)["status"] == "unresolved"


def test_freshness_file_bindings_are_not_pipeline_execution_inputs():
    from app.routers import pipelines
    source = inspect.getsource(pipelines)
    assert "flow_file_source_bindings" not in source


def test_data_migration_preserves_manual_reset_and_auto_set_intent(freshness_db):
    with database.get_db() as db:
        db.execute("DELETE FROM app_settings WHERE key='freshness_inheritance_migration_v1'")
        _source(db, 1, "Blank")
        _source(db, 2, "Reset")
        _source(db, 3, "Legacy custom")
        _source(db, 4, "Auto")
        _source(db, 5, "Stale threshold only")
        _source(db, 6, "Invalid legacy rule")
        _source(db, 7, "Mismatched auto-set audit")
        db.execute("UPDATE sources SET custom_fresh_days=5, freshness_rule_type=NULL WHERE id=3")
        db.execute("UPDATE sources SET freshness_rule_type='daily', custom_fresh_days=1, refresh_schedule='@daily' WHERE id=4")
        db.execute("UPDATE sources SET custom_stale_days=45 WHERE id=5")
        db.execute("UPDATE sources SET freshness_rule_type='fixed', freshness_schedule_days='Noday' WHERE id=6")
        db.execute("UPDATE sources SET freshness_rule_type='daily', custom_fresh_days=1, refresh_schedule='@daily' WHERE id=7")
        db.execute("INSERT INTO event_log(entity_type,entity_id,action) VALUES ('source',2,'freshness_rule_reset')")
        db.execute(
            """INSERT INTO event_log(entity_type,entity_id,action,detail)
               VALUES ('source',4,'freshness_rule_auto_set',
                       'set daily from source refresh schedule: @daily; requested_by=test')"""
        )
        db.execute(
            """INSERT INTO event_log(entity_type,entity_id,action,detail)
               VALUES ('source',7,'freshness_rule_auto_set',
                       'set daily from source refresh schedule: every day; requested_by=test')"""
        )
        initialize_freshness_data(db)
        rows = {row["id"]: row for row in db.execute("SELECT * FROM sources WHERE id<=7").fetchall()}
        assert rows[1]["freshness_mode"] == "inherit"
        assert rows[2]["freshness_mode"] == "disabled"
        assert rows[3]["freshness_mode"] == "manual"
        assert rows[3]["custom_fresh_days"] == 5
        assert rows[4]["freshness_mode"] == "inherit"
        assert rows[4]["freshness_rule_origin"] in {"source_schedule", "legacy_source_schedule"}
        assert rows[5]["freshness_mode"] == "inherit"
        assert rows[6]["freshness_mode"] == "manual"
        assert rows[6]["freshness_rule_status"] == "manual_invalid"
        assert rows[7]["freshness_mode"] == "manual"
        # Replaying startup keeps the classified modes intact.
        initialize_freshness_data(db)
        assert db.execute("SELECT freshness_mode FROM sources WHERE id=3").fetchone()[0] == "manual"
