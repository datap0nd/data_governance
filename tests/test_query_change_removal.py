import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import app.database as database


def test_startup_backs_up_then_removes_only_legacy_query_change_data(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="metronome-query-removal-") as folder:
        db_path = Path(folder) / "governance.db"
        monkeypatch.setattr(database, "DB_PATH", str(db_path))
        database.init_db()

        with closing(sqlite3.connect(db_path)) as db:
            db.execute("ALTER TABLE scan_runs ADD COLUMN changed_queries INTEGER DEFAULT 0")
            db.execute(
                """CREATE TABLE query_versions (
                       id INTEGER PRIMARY KEY,
                       artifact_name TEXT,
                       query_text TEXT
                   )"""
            )
            db.execute(
                "INSERT INTO query_versions(id, artifact_name, query_text) VALUES (1, 'mv', 'select 1')"
            )
            db.execute("INSERT INTO sources(id, name, type) VALUES (99, 'Kept source', 'manual')")
            db.execute(
                "INSERT INTO actions(id, source_id, type, notes) VALUES (101, 99, 'changed_query', 'remove me')"
            )
            db.execute(
                "INSERT INTO actions(id, source_id, type, notes) VALUES (102, 99, 'best_practice', 'keep me')"
            )
            db.execute(
                "INSERT INTO scan_runs(id, status, changed_queries) VALUES (103, 'completed', 1)"
            )
            db.commit()

        database.init_db()

        backups = list(
            (Path(folder) / "backups").glob(
                "governance.pre-query-history-removal.*.db"
            )
        )
        assert len(backups) == 1
        with closing(sqlite3.connect(backups[0])) as backup:
            assert backup.execute("SELECT COUNT(*) FROM query_versions").fetchone()[0] == 1
            assert backup.execute(
                "SELECT COUNT(*) FROM actions WHERE type='changed_query'"
            ).fetchone()[0] == 1
            assert "changed_queries" in {
                row[1] for row in backup.execute("PRAGMA table_info(scan_runs)")
            }

        with closing(sqlite3.connect(db_path)) as live:
            assert live.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='query_versions'"
            ).fetchone() is None
            assert live.execute(
                "SELECT COUNT(*) FROM actions WHERE type='changed_query'"
            ).fetchone()[0] == 0
            kept = live.execute(
                "SELECT type, notes FROM actions WHERE id=102"
            ).fetchone()
            assert kept[0] == "best_practice"
            assert kept[1].startswith("keep me")
            assert live.execute("SELECT name FROM sources WHERE id=99").fetchone()[0] == "Kept source"
            assert "changed_queries" not in {
                row[1] for row in live.execute("PRAGMA table_info(scan_runs)")
            }


def test_startup_on_old_sqlite_keeps_only_inert_scan_counter(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="metronome-old-sqlite-removal-") as folder:
        db_path = Path(folder) / "governance.db"
        monkeypatch.setattr(database, "DB_PATH", str(db_path))
        database.init_db()

        with closing(sqlite3.connect(db_path)) as db:
            db.execute("ALTER TABLE scan_runs ADD COLUMN changed_queries INTEGER DEFAULT 0")
            db.execute(
                "CREATE TABLE query_versions (id INTEGER PRIMARY KEY, query_text TEXT)"
            )
            db.execute("INSERT INTO query_versions(id, query_text) VALUES (1, 'select 1')")
            db.execute(
                "INSERT INTO sources(id, name, type) VALUES (99, 'Kept source', 'manual')"
            )
            db.execute(
                """INSERT INTO actions(id, source_id, type, status, notes)
                   VALUES (101, 99, 'changed_query', 'resolved', 'resolved history')"""
            )
            db.commit()

        monkeypatch.setattr(database.sqlite3, "sqlite_version_info", (3, 34, 0))
        database.init_db()

        with closing(sqlite3.connect(db_path)) as live:
            assert live.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='query_versions'"
            ).fetchone() is None
            assert live.execute(
                "SELECT COUNT(*) FROM actions WHERE type='changed_query'"
            ).fetchone()[0] == 0
            assert "changed_queries" in {
                row[1] for row in live.execute("PRAGMA table_info(scan_runs)")
            }


def test_failed_legacy_cleanup_preserves_data_but_does_not_block_startup(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="metronome-cleanup-failure-") as folder:
        db_path = Path(folder) / "governance.db"
        monkeypatch.setattr(database, "DB_PATH", str(db_path))

        def fail_cleanup(_connection):
            raise sqlite3.OperationalError("unexpected legacy constraint")

        monkeypatch.setattr(database, "_remove_query_change_feature", fail_cleanup)

        database.init_db()

        with closing(sqlite3.connect(db_path)) as live:
            assert live.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sources'"
            ).fetchone() is not None


def test_legacy_report_tables_get_columns_before_dependent_schema(monkeypatch):
    """Reproduce the production upgrade that failed on source_candidate_id."""
    with tempfile.TemporaryDirectory(prefix="metronome-legacy-report-tables-") as folder:
        db_path = Path(folder) / "governance.db"
        monkeypatch.setattr(database, "DB_PATH", str(db_path))
        with closing(sqlite3.connect(db_path)) as legacy:
            legacy.executescript(
                """
                CREATE TABLE sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    type TEXT NOT NULL,
                    connection_info TEXT,
                    source_query TEXT,
                    owner TEXT,
                    refresh_schedule TEXT,
                    tags TEXT,
                    discovered_by TEXT DEFAULT 'manual',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    tmdl_path TEXT,
                    owner TEXT,
                    recipients TEXT,
                    frequency TEXT,
                    last_published DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE report_tables (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER REFERENCES reports(id),
                    table_name TEXT NOT NULL,
                    source_id INTEGER REFERENCES sources(id),
                    source_expression TEXT,
                    last_scanned DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(report_id, table_name)
                );
                INSERT INTO sources(id, name, type) VALUES (1, 'legacy_table', 'postgres');
                INSERT INTO reports(id, name) VALUES (1, 'Legacy report');
                INSERT INTO report_tables(id, report_id, table_name, source_id)
                    VALUES (1, 1, 'LegacyModel', 1);
                """
            )
            legacy.commit()

        database.init_db()
        database.init_db()

        with closing(sqlite3.connect(db_path)) as upgraded:
            columns = {
                row[1] for row in upgraded.execute("PRAGMA table_info(report_tables)")
            }
            assert {
                "source_candidate_id",
                "source_resolution_status",
                "source_resolution_reason",
            }.issubset(columns)
            assert upgraded.execute(
                "SELECT table_name, source_id FROM report_tables WHERE id=1"
            ).fetchone() == ("LegacyModel", 1)
            indexes = {
                row[1] for row in upgraded.execute("PRAGMA index_list(report_tables)")
            }
            assert "idx_report_tables_candidate_id" in indexes
