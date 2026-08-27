import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app import database
from app.routers import dashboard


def test_dashboard_uses_latest_successful_scan_including_warnings(monkeypatch):
    with TemporaryDirectory(prefix="metronome-dashboard-") as folder:
        monkeypatch.setattr(database, "DB_PATH", str(Path(folder) / "dashboard.db"))
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
                   (started_at, finished_at, reports_scanned, sources_found, status, components_json)
                   VALUES ('2026-08-16T18:30:00Z', '2026-08-16T18:35:00Z',
                           37, 132, 'completed_with_warnings', ?)""",
                (json.dumps({
                    "core": {"status": "completed"},
                    "postgres_dependencies": {
                        "status": "completed_with_warnings",
                        "databases": {"staging": {"status": "failed", "error": "hidden"}},
                    },
                }),),
            )
            db.execute(
                """INSERT INTO scan_runs
                   (started_at, finished_at, status)
                   VALUES ('2026-08-16T19:00:00Z', '2026-08-16T19:01:00Z',
                           'stopped')"""
            )

        result = dashboard.get_dashboard()

        assert result.last_scan is not None
        assert result.last_scan.status == "completed_with_warnings"
        assert result.last_scan.reports_scanned == 37
        assert result.last_scan.sources_found == 132
        assert result.last_scan.components["postgres_dependencies"]["databases"]["staging"]["status"] == "failed"
