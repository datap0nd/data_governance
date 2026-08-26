from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import database
from app.models import FreshnessRuleRequest, SourceUpdate
from app.routers import sources


@pytest.fixture
def source_db(tmp_path):
    database.DB_PATH = str(tmp_path / "governance.db")
    database.init_db()
    return database.DB_PATH


def _request(actor="Test User"):
    return SimpleNamespace(state=SimpleNamespace(actor=actor))


def _insert_source(db, source_id, name, **fields):
    columns = ["id", "name", "type", *fields.keys()]
    values = [source_id, name, "excel", *fields.values()]
    placeholders = ", ".join("?" for _ in values)
    db.execute(
        f"INSERT INTO sources ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def test_manual_source_owner_update_persists(source_db):
    with database.get_db() as db:
        _insert_source(db, 1, "Source One")

    updated = sources.update_source(
        1,
        SourceUpdate(owner="Owner One"),
        _request(),
    )

    assert updated.owner == "Owner One"
    with database.get_db() as db:
        assert db.execute("SELECT owner FROM sources WHERE id = 1").fetchone()["owner"] == "Owner One"


def test_auto_assign_owners_uses_unique_majority_and_skips_ties(source_db):
    with database.get_db() as db:
        _insert_source(db, 1, "Majority Source")
        _insert_source(db, 2, "Tied Source")
        _insert_source(db, 3, "Existing Source", owner="Existing Owner")
        _insert_source(db, 4, "No Reports Source")
        db.executemany(
            "INSERT INTO reports (id, name, owner, archived) VALUES (?, ?, ?, 0)",
            [
                (1, "Report A", "Owner X"),
                (2, "Report B", "Owner X"),
                (3, "Report C", "Owner Y"),
                (4, "Report D", "Owner X"),
                (5, "Report E", "Owner Y"),
            ],
        )
        db.executemany(
            "INSERT INTO report_tables (report_id, table_name, source_id) VALUES (?, ?, ?)",
            [
                (1, "Table A", 1),
                (1, "Table A duplicate source", 1),
                (2, "Table B", 1),
                (3, "Table C", 1),
                (4, "Table D", 2),
                (5, "Table E", 2),
                (1, "Existing Table", 3),
            ],
        )

    result = sources.auto_assign_source_owners(_request())

    assert result["assigned"] == 1
    assert result["skipped_ties"] == 1
    assert result["skipped_no_report_owner"] == 1
    assert result["assignments"][0]["owner"] == "Owner X"
    assert result["assignments"][0]["owner_report_count"] == 2
    assert result["assignments"][0]["total_owned_reports"] == 3
    with database.get_db() as db:
        rows = db.execute("SELECT id, owner FROM sources ORDER BY id").fetchall()
        assert [(row["id"], row["owner"]) for row in rows] == [
            (1, "Owner X"),
            (2, None),
            (3, "Existing Owner"),
            (4, None),
        ]


def test_owner_suggestions_expose_evidence_and_review_states(source_db):
    with database.get_db() as db:
        _insert_source(db, 1, "Majority Source")
        _insert_source(db, 2, "Tied Source")
        _insert_source(db, 3, "No Evidence Source")
        db.executemany(
            "INSERT INTO reports (id, name, owner, archived) VALUES (?, ?, ?, 0)",
            [(1, "A", "Owner X"), (2, "B", "Owner X"), (3, "C", "Owner Y"), (4, "D", "Owner Y")],
        )
        db.executemany(
            "INSERT INTO report_tables (report_id, table_name, source_id) VALUES (?, ?, ?)",
            [(1, "A", 1), (2, "B", 1), (3, "C", 1), (1, "A2", 2), (4, "D", 2)],
        )

    queue = sources.get_source_owner_suggestions()
    by_id = {item["source_id"]: item for item in queue}

    assert by_id[1]["state"] == "suggested"
    assert by_id[1]["owner"] == "Owner X"
    assert by_id[1]["confidence"] == pytest.approx(2 / 3, abs=0.01)
    assert by_id[2]["state"] == "tie"
    assert by_id[2]["owner"] is None
    assert by_id[3]["state"] == "no_evidence"


def test_changing_freshness_rule_recalculates_latest_probe(source_db):
    old_data = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with database.get_db() as db:
        _insert_source(db, 1, "Delayed Source", freshness_rule_type="daily", custom_fresh_days=1)
        db.execute(
            """INSERT INTO source_probes (source_id, probed_at, last_data_at, status)
               VALUES (1, CURRENT_TIMESTAMP, ?, 'outdated')""",
            (old_data,),
        )

    result = sources.set_freshness_rule(
        1,
        FreshnessRuleRequest(rule_type="custom", fresh_days=30),
        _request(),
    )

    assert result["source_status"] == "fresh"
    with database.get_db() as db:
        assert db.execute(
            "SELECT status FROM source_probes WHERE source_id = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()["status"] == "fresh"


def test_auto_set_freshness_rules_uses_explicit_source_schedules_only(source_db):
    with database.get_db() as db:
        _insert_source(db, 1, "Weekly Source", refresh_schedule="Monday")
        _insert_source(db, 2, "Cron Source", refresh_schedule="0 6 * * MON-FRI")
        _insert_source(db, 3, "Daily Source", refresh_schedule="@daily")
        _insert_source(db, 4, "Disabled Source", refresh_schedule="0 6 * * 2 (disabled)")
        _insert_source(db, 5, "Unknown Source", refresh_schedule="whenever ready")
        _insert_source(
            db,
            6,
            "Existing Rule Source",
            refresh_schedule="Tuesday",
            freshness_rule_type="custom",
            custom_fresh_days=4,
        )

    result = sources.auto_set_source_freshness_rules(_request())

    assert result["configured"] == 3
    assert result["skipped_unsupported_schedule"] == 2
    with database.get_db() as db:
        rows = {
            row["id"]: dict(row)
            for row in db.execute(
                """SELECT id, freshness_rule_type, custom_fresh_days,
                          freshness_schedule_days
                   FROM sources ORDER BY id"""
            ).fetchall()
        }
    assert rows[1]["freshness_rule_type"] == "fixed"
    assert rows[1]["freshness_schedule_days"] == "Monday"
    assert rows[2]["freshness_rule_type"] == "fixed"
    assert rows[2]["freshness_schedule_days"] == "Monday,Tuesday,Wednesday,Thursday,Friday"
    assert rows[3]["freshness_rule_type"] == "daily"
    assert rows[3]["custom_fresh_days"] == 1
    assert rows[4]["freshness_rule_type"] is None
    assert rows[5]["freshness_rule_type"] is None
    assert rows[6]["freshness_rule_type"] == "custom"
    assert rows[6]["custom_fresh_days"] == 4


@pytest.mark.parametrize(
    ("schedule", "rule_type", "days"),
    [
        ("Weekly - Tuesday", "fixed", ["Tuesday"]),
        ("0 7 * * 1,3,5", "fixed", ["Monday", "Wednesday", "Friday"]),
        ("15 * * * *", "daily", []),
    ],
)
def test_freshness_schedule_parser(schedule, rule_type, days):
    rule = sources._freshness_rule_from_schedule(schedule)
    assert rule["rule_type"] == rule_type
    assert rule["refresh_days"] == days


def test_unreachable_local_user_paths_are_labelled_not_probeable(source_db):
    # Analysts register reports fed from their own Downloads/Desktop folders.
    # The server can never reach those, so the probe message should say what
    # the path is instead of raising a generic accessibility warning.
    from app.scanner import prober

    now = datetime.now(timezone.utc).isoformat()
    with database.get_db() as db:
        _insert_source(db, 1, "Analyst export")
        status = prober._probe_file_source(
            db, 1, r"C:\Users\nana.e\Desktop\Market Share\data.xlsx",
            now, {"type": None, "description": None},
        )
        message = db.execute(
            "SELECT message FROM source_probes WHERE source_id = 1"
        ).fetchone()["message"]

    assert status == "unknown"
    assert "local_user_path" in message
    assert "analyst's local profile" in message


def test_unreachable_shared_paths_keep_the_generic_accessibility_message(source_db):
    from app.scanner import prober

    now = datetime.now(timezone.utc).isoformat()
    with database.get_db() as db:
        _insert_source(db, 1, "Share export")
        prober._probe_file_source(
            db, 1, r"\\MX-SHARE\Users\METOMX\Desktop\gone.xlsx",
            now, {"type": None, "description": None},
        )
        message = db.execute(
            "SELECT message FROM source_probes WHERE source_id = 1"
        ).fetchone()["message"]

    assert message.startswith("File not accessible:")


def test_probe_history_pruning_keeps_latest_probe_and_latest_row_count(source_db):
    from app.scanner import prober

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=prober.SOURCE_PROBE_RETENTION_DAYS + 10)).isoformat()
    with database.get_db() as db:
        _insert_source(db, 1, "Long unreachable source")
        db.executemany(
            """INSERT INTO source_probes (id, source_id, probed_at, row_count, status)
               VALUES (?, 1, ?, ?, 'unknown')""",
            [
                (1, old, 500),   # latest probe with a row count: kept for alerts
                (2, old, None),  # old and redundant: pruned
                (3, old, None),  # latest probe overall: kept
            ],
        )
        pruned = prober._prune_probe_history(db, now.isoformat())
        remaining = [
            row["id"] for row in db.execute("SELECT id FROM source_probes ORDER BY id")
        ]

    assert pruned == 1
    assert remaining == [1, 3]


def _insert_open_alert(db, alert_id, source_id,
                       message="Source data is outside freshness rule (30 days)",
                       severity="critical"):
    db.execute(
        """INSERT INTO alerts (id, source_id, severity, message, acknowledged)
           VALUES (?, ?, ?, ?, 0)""",
        (alert_id, source_id, severity, message),
    )


def _insert_open_action(db, action_id, source_id, action_type="stale_source"):
    db.execute(
        "INSERT INTO actions (id, source_id, type, status) VALUES (?, ?, ?, 'open')",
        (action_id, source_id, action_type),
    )


def test_archiving_a_source_resolves_its_open_actions_and_alerts(source_db):
    from app.routers import archive

    with database.get_db() as db:
        _insert_source(db, 1, "Noisy source")
        _insert_open_action(db, 1, 1)
        _insert_open_alert(db, 1, 1)

    archive.toggle_archive("source", 1, _request())

    with database.get_db() as db:
        source = db.execute("SELECT archived FROM sources WHERE id = 1").fetchone()
        action = db.execute("SELECT status FROM actions WHERE id = 1").fetchone()
        alert = db.execute(
            "SELECT resolution_status, resolution_reason FROM alerts WHERE id = 1"
        ).fetchone()

    assert source["archived"] == 1
    assert action["status"] == "resolved"
    assert alert["resolution_status"] == "resolved"
    assert alert["resolution_reason"] == "Source archived"


def test_active_alert_list_hides_alerts_on_already_archived_sources(source_db):
    # Rows archived before archive-time cleanup existed can still hold open
    # alerts; the active view must not show them, the history view must.
    from app.routers import alerts

    with database.get_db() as db:
        _insert_source(db, 1, "Legacy archived source", archived=1)
        _insert_source(db, 2, "Active source")
        _insert_open_alert(db, 1, 1)
        _insert_open_alert(db, 2, 2)

    active_ids = {a.id for a in alerts.list_alerts(active_only=True)}
    all_ids = {a.id for a in alerts.list_alerts(active_only=False)}

    assert active_ids == {2}
    assert all_ids == {1, 2}


def test_run_probe_skips_archived_sources(source_db, tmp_path, monkeypatch):
    from app.checks import data_quality
    from app.scanner import prober

    data_file = tmp_path / "active.xlsx"
    data_file.write_text("data")
    monkeypatch.setattr(data_quality, "run_quality_checks", lambda: {})
    with database.get_db() as db:
        _insert_source(db, 1, "Active file", connection_info=str(data_file))
        _insert_source(db, 2, "Archived file", connection_info=str(data_file), archived=1)

    prober.run_probe()

    with database.get_db() as db:
        probed_ids = {
            row["source_id"]
            for row in db.execute("SELECT DISTINCT source_id FROM source_probes")
        }
    assert probed_ids == {1}


def test_scan_archives_local_user_path_sources_and_resolves_their_entries(source_db):
    from app.scanner import runner
    from app.scanner.tmdl_parser import SourceInfo

    now = datetime.now(timezone.utc).isoformat()
    local_only = SourceInfo(source_type="excel", file_path=r"C:\Users\ana\Desktop\solo.xlsx")
    all_sources = {local_only.connection_key: local_only}
    log_lines = []
    with database.get_db() as db:
        _insert_source(
            db, 1, "solo.xlsx",
            connection_info=r"C:\Users\ana\Desktop\solo.xlsx", discovered_by="scan",
        )
        _insert_source(
            db, 2, "share.xlsx",
            connection_info=r"\\MX-SHARE\exports\share.xlsx", discovered_by="scan",
        )
        _insert_source(
            db, 3, "manual.xlsx",
            connection_info=r"C:\Users\ana\Desktop\manual.xlsx", discovered_by="manual",
        )
        _insert_open_action(db, 1, 1)
        _insert_open_alert(db, 1, 1)

        runner._archive_local_user_path_sources(db, all_sources, now, log_lines)

        archived = {
            row["id"]: row["archived"]
            for row in db.execute("SELECT id, COALESCE(archived, 0) AS archived FROM sources")
        }
        action = db.execute("SELECT status FROM actions WHERE id = 1").fetchone()
        alert = db.execute("SELECT resolution_status FROM alerts WHERE id = 1").fetchone()

    assert archived == {1: 1, 2: 0, 3: 0}
    assert action["status"] == "resolved"
    assert alert["resolution_status"] == "resolved"
    assert any(line.startswith("ARCHIVED: solo.xlsx") for line in log_lines)


def test_scan_keeps_local_path_source_whose_basename_also_has_a_shared_path(source_db):
    # sources.name is UNIQUE and file rows are keyed by basename, so one row
    # can serve both C:\Users\... and \\share\... lineages. Archiving it would
    # hide the legitimate shared one - the pass must skip contested basenames.
    from app.scanner import runner
    from app.scanner.tmdl_parser import SourceInfo

    now = datetime.now(timezone.utc).isoformat()
    local = SourceInfo(source_type="excel", file_path=r"C:\Users\ana\Desktop\data.xlsx")
    shared = SourceInfo(source_type="excel", file_path=r"\\MX-SHARE\exports\data.xlsx")
    all_sources = {local.connection_key: local, shared.connection_key: shared}
    log_lines = []
    with database.get_db() as db:
        # connection_info is last-writer-wins; here the local path won.
        _insert_source(
            db, 1, "data.xlsx",
            connection_info=r"C:\Users\ana\Desktop\data.xlsx", discovered_by="scan",
        )

        runner._archive_local_user_path_sources(db, all_sources, now, log_lines)

        archived = db.execute(
            "SELECT COALESCE(archived, 0) AS archived FROM sources WHERE id = 1"
        ).fetchone()

    assert archived["archived"] == 0
    assert any(line.startswith("SKIPPED (shared basename): data.xlsx") for line in log_lines)


def test_auto_close_resolves_orphaned_alert_when_latest_probe_is_fresh(source_db):
    # An alert whose action was closed out-of-band used to stay open forever;
    # the sweep closes it as soon as a probe proves the source compliant.
    from app.scanner import prober

    now = datetime.now(timezone.utc).isoformat()
    with database.get_db() as db:
        _insert_source(db, 1, "Recovered source")
        _insert_open_alert(db, 1, 1)
        db.execute(
            "INSERT INTO source_probes (source_id, probed_at, status) VALUES (1, ?, 'fresh')",
            (now,),
        )

        actions_closed, alerts_closed = prober._auto_close_stale_entries(db, now)
        alert = db.execute(
            "SELECT resolution_status, resolution_reason FROM alerts WHERE id = 1"
        ).fetchone()

    assert (actions_closed, alerts_closed) == (0, 1)
    assert alert["resolution_status"] == "resolved"
    assert alert["resolution_reason"] == "Source no longer outdated"


def test_auto_close_leaves_alert_open_when_latest_probe_is_outdated(source_db):
    from app.scanner import prober

    now = datetime.now(timezone.utc).isoformat()
    with database.get_db() as db:
        _insert_source(db, 1, "Still stale source")
        _insert_open_action(db, 1, 1)
        _insert_open_alert(db, 1, 1)
        db.execute(
            "INSERT INTO source_probes (source_id, probed_at, status) VALUES (1, ?, 'outdated')",
            (now,),
        )

        actions_closed, alerts_closed = prober._auto_close_stale_entries(db, now)
        action = db.execute("SELECT status FROM actions WHERE id = 1").fetchone()
        alert = db.execute("SELECT resolution_status FROM alerts WHERE id = 1").fetchone()

    assert (actions_closed, alerts_closed) == (0, 0)
    assert action["status"] == "open"
    assert alert["resolution_status"] is None


def test_auto_close_leaves_entries_open_when_latest_probe_is_unknown(source_db):
    # 'unknown' means the probe failed or the source is unreachable - that
    # does not contradict the alert, so nothing may auto-close on it.
    from app.scanner import prober

    now = datetime.now(timezone.utc).isoformat()
    with database.get_db() as db:
        _insert_source(db, 1, "Unreachable source")
        _insert_open_action(db, 1, 1)
        _insert_open_alert(db, 1, 1)
        db.execute(
            "INSERT INTO source_probes (source_id, probed_at, status) VALUES (1, ?, 'unknown')",
            (now,),
        )

        actions_closed, alerts_closed = prober._auto_close_stale_entries(db, now)
        action = db.execute("SELECT status FROM actions WHERE id = 1").fetchone()
        alert = db.execute("SELECT resolution_status FROM alerts WHERE id = 1").fetchone()

    assert (actions_closed, alerts_closed) == (0, 0)
    assert action["status"] == "open"
    assert alert["resolution_status"] is None


def test_auto_close_breaks_probe_timestamp_ties_by_id(source_db):
    from app.scanner import prober

    now = datetime.now(timezone.utc).isoformat()
    with database.get_db() as db:
        _insert_source(db, 1, "Tied probes source")
        _insert_open_action(db, 1, 1)
        _insert_open_alert(db, 1, 1)
        db.executemany(
            "INSERT INTO source_probes (id, source_id, probed_at, status) VALUES (?, 1, ?, ?)",
            [(1, now, "outdated"), (2, now, "fresh")],
        )

        actions_closed, alerts_closed = prober._auto_close_stale_entries(db, now)

    assert (actions_closed, alerts_closed) == (1, 1)


def test_resolving_an_action_resolves_its_linked_alert_one_way(source_db):
    from app.models import ActionUpdate
    from app.routers import actions

    with database.get_db() as db:
        _insert_source(db, 1, "Stale source")
        _insert_source(db, 2, "Other source")
        _insert_open_action(db, 1, 1)
        _insert_open_alert(db, 1, 1)
        _insert_open_alert(db, 2, 2)

    actions.update_action(1, ActionUpdate(status="resolved"), _request())

    with database.get_db() as db:
        linked = db.execute(
            "SELECT resolution_status, resolution_reason, acknowledged_by FROM alerts WHERE id = 1"
        ).fetchone()
        unrelated = db.execute("SELECT resolution_status FROM alerts WHERE id = 2").fetchone()

    assert linked["resolution_status"] == "resolved"
    assert linked["resolution_reason"] == "Linked action resolved"
    assert linked["acknowledged_by"] == "Test User"
    assert unrelated["resolution_status"] is None

    # Deliberately one-way: reopening the action does not reopen the alert -
    # if the source is still outdated the next probe run recreates it.
    actions.update_action(1, ActionUpdate(status="open"), _request())
    with database.get_db() as db:
        linked = db.execute("SELECT resolution_status FROM alerts WHERE id = 1").fetchone()
    assert linked["resolution_status"] == "resolved"


def test_find_file_has_no_basename_fallback(source_db, tmp_path):
    # Matching an unreachable path to a same-named file elsewhere would report
    # the wrong file's mtime as the source's freshness (a false "fresh").
    from app.scanner import prober

    real = tmp_path / "data.xlsx"
    real.write_text("data")

    assert prober._find_file(str(real)) == real
    assert prober._find_file(r"C:\Users\ana\Desktop\data.xlsx") is None
    assert prober._find_file(r"\\MX-SHARE\exports\data.xlsx") is None
