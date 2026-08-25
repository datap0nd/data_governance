"""Per-artifact query change attribution and diff history tests."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import database
from app.scanner.query_history import (
    normalize_query_text,
    query_hash,
    sync_mv_query_version,
    sync_report_query_versions,
)

NOW = "2026-08-20T10:00:00+00:00"
LATER = "2026-08-21T10:00:00+00:00"
LATEST = "2026-08-22T10:00:00+00:00"

M_A = 'let\n    Source = PostgreSQL.Database("h", "db"),\n    t = Source{[Schema="s",Item="a"]}[Data]\nin\n    t'
M_A_EDITED = M_A.replace('Item="a"', 'Item="a_v2"')
M_B = 'let\n    Source = Excel.Workbook(File.Contents("C:\\\\data.xlsx"))\nin\n    Source'


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "governance.db"))
    database.init_db()
    return tmp_path


def _seed_reports(db):
    db.execute("INSERT INTO reports (id, name, owner) VALUES (1, 'Report One', 'Alice')")
    db.execute("INSERT INTO reports (id, name, owner) VALUES (2, 'Report Two', 'Bob')")


def _active_actions(db, action_type="changed_query"):
    return db.execute(
        """SELECT * FROM actions WHERE type = ?
           AND status IN ('open','acknowledged','investigating') ORDER BY id""",
        (action_type,),
    ).fetchall()


def _versions(db, **filters):
    where = " AND ".join(f"{key} = ?" for key in filters)
    sql = "SELECT * FROM query_versions"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY id"
    return db.execute(sql, tuple(filters.values())).fetchall()


# ── Normalization ──

def test_normalization_ignores_line_endings_and_trailing_whitespace():
    assert normalize_query_text("a \r\n b\r\nc  ") == normalize_query_text("a\n b\nc")
    assert query_hash("  \nlet x\n") == query_hash("let x")
    # Comments and query tokens remain meaningful
    assert query_hash("let x // comment") != query_hash("let x")
    assert query_hash('Item="a"') != query_hash('Item="b"')


# ── Report table versioning ──

def test_first_scan_baselines_without_alert(db_env):
    with database.get_db() as db:
        _seed_reports(db)
        result = sync_report_query_versions(db, 1, "Report One", {"T1": M_A, "T2": M_B}, None, NOW)
        assert result["changes"] == []
        assert result["baselined"] == 2
        assert result["action_id"] is None
        rows = _versions(db, report_id=1)
        assert {r["artifact_name"] for r in rows} == {"T1", "T2"}
        assert all(r["change_kind"] == "baseline" for r in rows)
        assert _active_actions(db) == []


def test_shared_source_change_alerts_only_the_changed_report(db_env):
    with database.get_db() as db:
        _seed_reports(db)
        # Both reports use the same M expression (a shared, deduplicated source)
        sync_report_query_versions(db, 1, "Report One", {"Shared": M_A}, None, NOW)
        sync_report_query_versions(db, 2, "Report Two", {"Shared": M_A}, None, NOW)

        r1 = sync_report_query_versions(db, 1, "Report One", {"Shared": M_A_EDITED}, None, LATER)
        r2 = sync_report_query_versions(db, 2, "Report Two", {"Shared": M_A}, None, LATER)

        assert len(r1["changes"]) == 1
        assert r2["changes"] == []
        actions = _active_actions(db)
        assert len(actions) == 1
        assert actions[0]["report_id"] == 1
        assert actions[0]["source_id"] is None
        assert actions[0]["assigned_to"] == "Alice"
        # Report Two's history is untouched
        assert len(_versions(db, report_id=2)) == 1


def test_multiple_changes_grouped_into_one_action_with_per_table_links(db_env):
    with database.get_db() as db:
        _seed_reports(db)
        sync_report_query_versions(
            db, 1, "Report One", {"T1": M_A, "T2": M_B, "T3": M_A}, None, NOW
        )
        result = sync_report_query_versions(
            db, 1, "Report One",
            {"T1": M_A_EDITED, "T2": M_B + "\n// note", "T4": M_A},  # T3 removed, T4 added
            None, LATER,
        )
        assert len(result["changes"]) == 4
        actions = _active_actions(db)
        assert len(actions) == 1
        action_id = actions[0]["id"]
        linked = _versions(db, action_id=action_id)
        kinds = {r["artifact_name"]: r["change_kind"] for r in linked}
        assert kinds == {"T1": "changed", "T2": "changed", "T3": "removed", "T4": "added"}
        removed = next(r for r in linked if r["artifact_name"] == "T3")
        assert removed["query_text"] is None
        assert all(r["language"] == "m" for r in linked)


def test_whitespace_only_rescan_is_silent_but_token_edits_alert(db_env):
    with database.get_db() as db:
        _seed_reports(db)
        sync_report_query_versions(db, 1, "Report One", {"T1": M_A}, None, NOW)

        noisy = M_A.replace("\n", "\r\n") + "   \r\n"
        result = sync_report_query_versions(db, 1, "Report One", {"T1": noisy}, None, LATER)
        assert result["changes"] == []
        assert len(_versions(db, report_id=1)) == 1
        assert _active_actions(db) == []

        commented = M_A + "\n// reviewed by BI"
        result = sync_report_query_versions(db, 1, "Report One", {"T1": commented}, None, LATER)
        assert len(result["changes"]) == 1
        assert len(_active_actions(db)) == 1


def test_removal_restoration_and_repeat_changes_keep_ordered_history(db_env):
    with database.get_db() as db:
        _seed_reports(db)
        sync_report_query_versions(db, 1, "Report One", {"T1": M_A, "Keep": M_B}, None, NOW)
        sync_report_query_versions(db, 1, "Report One", {"Keep": M_B}, None, LATER)      # removed
        sync_report_query_versions(db, 1, "Report One", {"T1": M_A, "Keep": M_B}, None, LATEST)  # restored

        rows = _versions(db, report_id=1, artifact_name="T1")
        assert [r["change_kind"] for r in rows] == ["baseline", "removed", "restored"]
        assert rows[1]["prev_version_id"] == rows[0]["id"]
        assert rows[2]["prev_version_id"] == rows[1]["id"]
        # Reverting to earlier text creates a new version instead of reusing it
        assert rows[2]["normalized_hash"] == rows[0]["normalized_hash"]
        assert rows[2]["id"] > rows[1]["id"] > rows[0]["id"]


def test_rescan_with_no_changes_is_idempotent(db_env):
    with database.get_db() as db:
        _seed_reports(db)
        sync_report_query_versions(db, 1, "Report One", {"T1": M_A}, None, NOW)
        sync_report_query_versions(db, 1, "Report One", {"T1": M_A_EDITED}, None, LATER)
        before_versions = len(_versions(db))
        before_actions = [dict(a) for a in _active_actions(db)]

        result = sync_report_query_versions(db, 1, "Report One", {"T1": M_A_EDITED}, None, LATEST)
        assert result["changes"] == []
        assert len(_versions(db)) == before_versions
        after_actions = [dict(a) for a in _active_actions(db)]
        assert after_actions == before_actions


def test_newer_change_set_supersedes_prior_action_but_keeps_history(db_env):
    with database.get_db() as db:
        _seed_reports(db)
        sync_report_query_versions(db, 1, "Report One", {"T1": M_A}, None, NOW)
        first = sync_report_query_versions(db, 1, "Report One", {"T1": M_A_EDITED}, None, LATER)
        second = sync_report_query_versions(db, 1, "Report One", {"T1": M_A}, None, LATEST)

        assert first["action_id"] != second["action_id"]
        actions = _active_actions(db)
        assert [a["id"] for a in actions] == [second["action_id"]]
        prior = db.execute(
            "SELECT * FROM actions WHERE id = ?", (first["action_id"],)
        ).fetchone()
        assert prior["status"] == "resolved"
        assert "superseded query change" in prior["notes"]
        # All versions remain available
        assert len(_versions(db, report_id=1, artifact_name="T1")) == 3


# ── MV definition versioning ──

def _seed_mv(db, owner=None):
    db.execute(
        """INSERT INTO sources (id, name, type, connection_info, owner, discovered_by)
           VALUES (10, 'reporting.sales_mv', 'postgresql', 'reporting.sales_mv', ?, 'scan')""",
        (owner,),
    )
    db.execute("INSERT INTO reports (id, name, owner) VALUES (1, 'MV Report', 'Carol')")
    db.execute(
        "INSERT INTO report_tables (report_id, table_name, source_id) VALUES (1, 'Sales', 10)"
    )


def test_mv_baseline_then_change_alerts_the_mv_source(db_env):
    sql_v1 = "SELECT a, b\nFROM base.orders\nWHERE active = true;"
    sql_v2 = "SELECT a, b, c\nFROM base.orders\nWHERE active = true;"
    with database.get_db() as db:
        _seed_mv(db, owner=None)
        first = sync_mv_query_version(db, 10, "reporting.sales_mv", sql_v1, None, NOW)
        assert first["baselined"] is True and first["changed"] is False
        assert _active_actions(db) == []

        second = sync_mv_query_version(db, 10, "reporting.sales_mv", sql_v2, None, LATER)
        assert second["changed"] is True
        actions = _active_actions(db)
        assert len(actions) == 1
        assert actions[0]["source_id"] == 10
        assert actions[0]["report_id"] is None
        # No source owner: falls back to the linked report's owner
        assert actions[0]["assigned_to"] == "Carol"
        rows = _versions(db, source_id=10)
        assert [r["change_kind"] for r in rows] == ["baseline", "changed"]
        assert all(r["language"] == "sql" for r in rows)


def test_mv_whitespace_only_definition_change_is_silent(db_env):
    sql_v1 = "SELECT a\nFROM t;"
    with database.get_db() as db:
        _seed_mv(db, owner="Dana")
        sync_mv_query_version(db, 10, "reporting.sales_mv", sql_v1, None, NOW)
        result = sync_mv_query_version(
            db, 10, "reporting.sales_mv", "SELECT a  \r\nFROM t;\r\n", None, LATER
        )
        assert result["changed"] is False
        assert len(_versions(db, source_id=10)) == 1
        assert _active_actions(db) == []


# ── pg_deps integration ──

class _DispatchingCursor:
    def __init__(self, dep_rows, matview_rows):
        self._dep_rows = dep_rows
        self._matview_rows = matview_rows
        self._last_sql = ""

    def execute(self, sql):
        self._last_sql = sql

    def fetchall(self):
        if "pg_matviews" in self._last_sql:
            if isinstance(self._matview_rows, Exception):
                raise self._matview_rows
            return list(self._matview_rows)
        return list(self._dep_rows)


class _DispatchingConnection:
    def __init__(self, dep_rows, matview_rows):
        self._dep_rows = dep_rows
        self._matview_rows = matview_rows
        self.closed = False

    def cursor(self):
        return _DispatchingCursor(self._dep_rows, self._matview_rows)

    def close(self):
        self.closed = True


def test_pg_deps_versions_tracked_mvs_and_ignores_untracked(db_env):
    import app.scanner.pg_deps as pg_deps

    dep_rows = [
        ("reporting", "sales_mv", "base", "orders", "r"),
        ("scratch", "untracked_mv", "base", "misc", "r"),
    ]
    matviews_v1 = [
        ("reporting", "sales_mv", "SELECT a FROM base.orders;"),
        ("scratch", "untracked_mv", "SELECT 1;"),
    ]
    with database.get_db() as db:
        _seed_mv(db, owner="Dana")

    conn = _DispatchingConnection(dep_rows, matviews_v1)
    with patch.object(pg_deps, "_get_pg_connection", return_value=conn):
        result = pg_deps.scan_pg_dependencies(scan_run_id=None)
    assert result["status"] == "completed"
    assert result["mv_definitions"] == "captured"
    assert result["query_changes"] == 0

    with database.get_db() as db:
        rows = _versions(db)
        # Only the tracked MV is versioned; scratch.untracked_mv feeds nothing
        assert {r["artifact_name"] for r in rows} == {"reporting.sales_mv"}
        assert rows[0]["change_kind"] == "baseline"

    matviews_v2 = [
        ("reporting", "sales_mv", "SELECT a, b FROM base.orders;"),
        ("scratch", "untracked_mv", "SELECT 2;"),
    ]
    conn = _DispatchingConnection(dep_rows, matviews_v2)
    with patch.object(pg_deps, "_get_pg_connection", return_value=conn):
        result = pg_deps.scan_pg_dependencies(scan_run_id=None)
    assert result["query_changes"] == 1
    assert any("reporting.sales_mv" in line for line in result["query_change_log"])

    with database.get_db() as db:
        actions = _active_actions(db)
        assert len(actions) == 1
        assert actions[0]["source_id"] == 10
        assert actions[0]["assigned_to"] == "Dana"
        history = _versions(db, source_id=10)
        assert [r["change_kind"] for r in history] == ["baseline", "changed"]


def test_pg_deps_continues_when_definition_capture_unavailable(db_env):
    import app.scanner.pg_deps as pg_deps

    dep_rows = [("reporting", "sales_mv", "base", "orders", "r")]
    with database.get_db() as db:
        _seed_mv(db, owner="Dana")

    conn = _DispatchingConnection(dep_rows, RuntimeError("permission denied"))
    with patch.object(pg_deps, "_get_pg_connection", return_value=conn):
        result = pg_deps.scan_pg_dependencies()

    assert result["status"] == "completed"
    assert result["deps_created"] >= 1
    assert result["mv_definitions"] == "unavailable"
    assert result["query_changes"] == 0
    assert "query history step skipped" in result["log"]
    with database.get_db() as db:
        assert _versions(db) == []
        assert _active_actions(db) == []


# ── Legacy cleanup ──

def test_legacy_actions_without_history_resolve_once_and_new_actions_survive(db_env):
    with database.get_db() as db:
        db.execute("INSERT INTO sources (id, name, type) VALUES (5, 'legacy.table', 'postgresql')")
        db.execute(
            """INSERT INTO actions (source_id, type, status, fingerprint, notes, created_at, updated_at)
               VALUES (5, 'changed_query', 'open', 'changed_query:5:abc', 'legacy', ?, ?)""",
            (NOW, NOW),
        )
    database.init_db()  # migrations resolve unattributable legacy alerts
    with database.get_db() as db:
        legacy = db.execute("SELECT * FROM actions WHERE notes LIKE 'legacy%'").fetchone()
        assert legacy["status"] == "resolved"
        assert "legacy source-level query alert" in legacy["notes"]

        # A new MV action with attached history is not affected by the cleanup
        _seed_mv(db, owner="Dana")
        sync_mv_query_version(db, 10, "reporting.sales_mv", "SELECT 1;", None, NOW)
        sync_mv_query_version(db, 10, "reporting.sales_mv", "SELECT 2;", None, LATER)
    database.init_db()
    with database.get_db() as db:
        actions = _active_actions(db)
        assert len(actions) == 1
        assert actions[0]["source_id"] == 10
