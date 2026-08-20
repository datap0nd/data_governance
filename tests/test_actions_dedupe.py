from app import database
from app.routers.actions import list_actions


def test_alert_list_hides_best_practices_and_collapses_redundant_families(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "actions.db"))
    database.init_db()

    with database.get_db() as db:
        db.execute(
            """INSERT INTO sources (id, name, type, archived)
               VALUES (1, 'reporting.target_table', 'postgresql', 0)"""
        )
        db.execute(
            """INSERT INTO source_probes
               (source_id, probed_at, last_data_at, row_count, status)
               VALUES (1, '2026-08-16T10:00:00+00:00',
                       '2026-08-10T10:00:00+00:00', 100, 'outdated')"""
        )
        db.execute(
            "INSERT INTO reports (id, name, archived) VALUES (1, 'Report A', 0)"
        )
        db.execute(
            """INSERT INTO flow_sites (id, name, adapter, base_url)
               VALUES (90, 'Portal', 'web_export', 'https://example.test')"""
        )
        db.execute(
            """INSERT INTO flow_reports (id, site_id, name, report_url)
               VALUES (1, 90, 'Export', 'https://example.test/export')"""
        )
        db.execute(
            """INSERT INTO flows
               (id, name, site_id, report_id, target_folder, filename_template)
               VALUES (1, 'Target Flow', 90, 1, '/tmp', 'target.csv')"""
        )
        db.executemany(
            """INSERT INTO actions
               (source_id, report_id, flow_id, type, status, fingerprint, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'open', ?, ?, ?)""",
            [
                (1, None, None, "stale_source", "stale:1", "2026-08-10", "2026-08-10"),
                (1, None, None, "outdated_source", "outdated:1", "2026-08-11", "2026-08-11"),
                (1, None, None, "changed_query", "changed:old", "2026-08-12", "2026-08-12"),
                (1, None, None, "changed_query", "changed:new", "2026-08-13", "2026-08-13"),
                (None, 1, None, "best_practice", "best:1", "2026-08-14", "2026-08-14"),
                (None, None, 1, "flow_failed", "flow_failed:1", "2026-08-15", "2026-08-15"),
            ],
        )

    # Re-running startup migrations must clean legacy operational clutter.
    database.init_db()
    with database.get_db() as db:
        active = db.execute(
            """SELECT type, COUNT(*) AS count FROM actions
               WHERE status IN ('open','acknowledged','investigating')
               GROUP BY type"""
        ).fetchall()
    counts = {row["type"]: row["count"] for row in active}
    assert counts.get("best_practice", 0) == 0
    assert sum(counts.get(name, 0) for name in ("stale_source", "outdated_source", "error_source")) == 1
    assert counts.get("changed_query", 0) == 1

    actions = list_actions(status="open")
    types = [action.type for action in actions]

    assert "best_practice" not in types
    assert sum(action.type in {"stale_source", "outdated_source", "error_source"} for action in actions) == 1
    assert types.count("changed_query") == 1
    flow = next(action for action in actions if action.type == "flow_failed")
    assert flow.asset_type == "flow"
    assert flow.asset_name == "Target Flow"
