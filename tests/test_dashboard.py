from app import database
from app.routers import dashboard


def test_dashboard_uses_latest_completed_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "dashboard.db"))
    database.init_db()

    monkeypatch.setattr(dashboard, "list_sources", lambda include_archived=False: [])
    monkeypatch.setattr(dashboard, "list_reports", lambda include_archived=False: [])
    monkeypatch.setattr(dashboard, "list_actions", lambda status=None: [])

    with database.get_db() as db:
        db.execute(
            """INSERT INTO scan_runs
               (started_at, finished_at, reports_scanned, sources_found, status)
               VALUES ('2026-08-16T18:00:00Z', '2026-08-16T18:05:00Z',
                       36, 130, 'completed')"""
        )
        db.execute(
            """INSERT INTO scan_runs
               (started_at, finished_at, status)
               VALUES ('2026-08-16T19:00:00Z', '2026-08-16T19:01:00Z',
                       'stopped')"""
        )

    result = dashboard.get_dashboard()

    assert result.last_scan is not None
    assert result.last_scan.status == "completed"
    assert result.last_scan.reports_scanned == 36
    assert result.last_scan.sources_found == 130
