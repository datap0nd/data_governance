"""API and scan-integration tests for query change history."""

import shutil
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import database
from app.scanner.query_history import sync_mv_query_version, sync_report_query_versions

NOW = "2026-08-20T10:00:00+00:00"
LATER = "2026-08-21T10:00:00+00:00"

M_V1 = "let\n    Source = Sql.Database(\"h\", \"db\"),\n    t = Source\nin\n    t"
M_V2 = "let\n    Source = Sql.Database(\"h\", \"db2\"),\n    t = Source\nin\n    t"


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "governance.db"))
    database.init_db()
    return tmp_path


def test_report_history_grouped_and_ordered(db_env):
    from app.routers.query_history import report_query_history

    with database.get_db() as db:
        db.execute("INSERT INTO reports (id, name, owner) VALUES (1, 'R', 'A')")
        sync_report_query_versions(db, 1, "R", {"T1": M_V1, "T2": M_V1}, None, NOW)
        sync_report_query_versions(db, 1, "R", {"T1": M_V2, "T2": M_V1}, None, LATER)

    history = report_query_history(1)
    assert history["report_name"] == "R"
    groups = {t["table_name"]: t["versions"] for t in history["tables"]}
    assert set(groups) == {"T1", "T2"}
    t1 = groups["T1"]
    assert [v["change_kind"] for v in t1] == ["baseline", "changed"]
    assert t1[0]["id"] < t1[1]["id"]
    assert t1[1]["prev_version_id"] == t1[0]["id"]
    # Lightweight payload: no query text, just a text marker
    assert all("query_text" not in v and v["has_text"] for v in t1)

    with pytest.raises(HTTPException) as err:
        report_query_history(999)
    assert err.value.status_code == 404


def test_source_history_lists_mv_versions(db_env):
    from app.routers.query_history import source_query_history

    with database.get_db() as db:
        db.execute(
            "INSERT INTO sources (id, name, type) VALUES (10, 'reporting.mv', 'postgresql')"
        )
        sync_mv_query_version(db, 10, "reporting.mv", "SELECT 1;", None, NOW)
        sync_mv_query_version(db, 10, "reporting.mv", "SELECT 2;", None, LATER)

    history = source_query_history(10)
    assert history["source_name"] == "reporting.mv"
    versions = history["artifacts"][0]["versions"]
    assert [v["change_kind"] for v in versions] == ["baseline", "changed"]
    assert all(v["language"] == "sql" for v in versions)

    with pytest.raises(HTTPException) as err:
        source_query_history(999)
    assert err.value.status_code == 404


def test_compare_returns_aligned_rows_and_validates_artifacts(db_env):
    from app.routers.query_history import compare_query_versions

    with database.get_db() as db:
        db.execute("INSERT INTO reports (id, name) VALUES (1, 'R')")
        db.execute(
            "INSERT INTO sources (id, name, type) VALUES (10, 'reporting.mv', 'postgresql')"
        )
        sync_report_query_versions(db, 1, "R", {"T1": M_V1}, None, NOW)
        sync_report_query_versions(db, 1, "R", {"T1": M_V2}, None, LATER)
        sync_mv_query_version(db, 10, "reporting.mv", "SELECT 1;", None, NOW)
        t1 = db.execute(
            "SELECT id FROM query_versions WHERE artifact_name='T1' ORDER BY id"
        ).fetchall()
        mv = db.execute(
            "SELECT id FROM query_versions WHERE artifact_name='reporting.mv'"
        ).fetchone()

    v1_id, v2_id = t1[0]["id"], t1[1]["id"]

    diff = compare_query_versions(to_id=v2_id, from_id=v1_id)
    assert diff["artifact_name"] == "T1"
    assert diff["from_version"]["id"] == v1_id
    assert diff["to_version"]["id"] == v2_id
    kinds = {row["kind"] for row in diff["rows"]}
    assert "context" in kinds
    changed_rows = [r for r in diff["rows"] if r["kind"] in ("changed", "removed", "added")]
    assert changed_rows
    changed = changed_rows[0]
    assert 'db' in changed["left_text"] and 'db2' in changed["right_text"]
    assert changed["left_line"] and changed["right_line"]
    # Context rows carry both line numbers
    ctx = next(r for r in diff["rows"] if r["kind"] == "context")
    assert ctx["left_line"] and ctx["right_line"]

    # Default before = recorded predecessor
    diff_default = compare_query_versions(to_id=v2_id, from_id=None)
    assert diff_default["from_version"]["id"] == v1_id

    # Explicit empty before
    diff_empty = compare_query_versions(to_id=v2_id, from_id=0)
    assert diff_empty["from_version"] is None
    assert all(r["kind"] == "added" for r in diff_empty["rows"])

    # Cross-artifact comparisons are rejected
    with pytest.raises(HTTPException) as err:
        compare_query_versions(to_id=v2_id, from_id=mv["id"])
    assert err.value.status_code == 400

    # Missing versions
    for kwargs in ({"to_id": 9999, "from_id": None}, {"to_id": v2_id, "from_id": 9999}):
        with pytest.raises(HTTPException) as err:
            compare_query_versions(**kwargs)
        assert err.value.status_code == 404


def test_actions_payload_includes_grouped_query_changes(db_env):
    from app.routers.actions import list_actions

    with database.get_db() as db:
        db.execute("INSERT INTO reports (id, name, owner) VALUES (1, 'R', 'A')")
        db.execute("INSERT INTO reports (id, name, owner, archived) VALUES (2, 'Archived R', 'A', 1)")
        sync_report_query_versions(db, 1, "R", {"T1": M_V1, "T2": M_V1}, None, NOW)
        sync_report_query_versions(db, 1, "R", {"T1": M_V2, "T2": M_V2}, None, LATER)
        sync_report_query_versions(db, 2, "Archived R", {"T1": M_V1}, None, NOW)
        sync_report_query_versions(db, 2, "Archived R", {"T1": M_V2}, None, LATER)

    actions = [a for a in list_actions() if a.type == "changed_query"]
    # The archived report's action is hidden; its history remains queryable
    assert len(actions) == 1
    action = actions[0]
    assert action.asset_type == "report"
    assert action.report_id == 1
    assert action.report_name == "R"
    names = sorted(c.artifact_name for c in action.query_changes)
    assert names == ["T1", "T2"]
    change = action.query_changes[0]
    assert change.version_id and change.prev_version_id
    assert change.artifact_kind == "report_table"
    assert change.language == "m"
    assert change.detected_at == LATER

    from app.routers.query_history import report_query_history
    archived_history = report_query_history(2)
    assert archived_history["tables"][0]["versions"]


def _copy_fixture_reports(tmp_path):
    dest = tmp_path / "reports"
    shutil.copytree(REPO_ROOT / "test_reports", dest)
    return dest


def test_run_scan_versions_each_report_table_and_counts_changes(db_env, tmp_path, monkeypatch):
    import app.scanner.runner as runner

    monkeypatch.setattr(runner, "DB_PATH", str(Path(db_env) / "governance.db"))
    reports_root = _copy_fixture_reports(tmp_path)

    first = runner.run_scan(str(reports_root), run_followup_probe=False)
    assert first["status"] == "completed"
    # First observation is a baseline: no per-table changes, no alerts
    assert first["changed_queries"] == 0
    with database.get_db() as db:
        baseline_count = db.execute(
            "SELECT COUNT(*) AS c FROM query_versions WHERE change_kind = 'baseline'"
        ).fetchone()["c"]
        assert baseline_count > 0
        assert db.execute(
            """SELECT COUNT(*) AS c FROM actions
               WHERE type='changed_query' AND status IN ('open','acknowledged','investigating')"""
        ).fetchone()["c"] == 0

    # Edit one table's M expression in one report
    target = reports_root / "Weekly_Sales/Weekly_Sales.SemanticModel/Definition/Tables/Sales Orders.tmdl"
    text = target.read_text()
    assert 'Item="orders"' in text
    target.write_text(text.replace('Item="orders"', 'Item="orders_v2"'))

    second = runner.run_scan(str(reports_root), run_followup_probe=False)
    assert second["status"] == "completed"
    assert second["changed_queries"] == 1
    assert any("QUERY CHANGED: Weekly_Sales/Sales Orders" in line for line in second["log"].split("\n"))

    with database.get_db() as db:
        actions = db.execute(
            """SELECT a.*, r.name AS report_name FROM actions a
               JOIN reports r ON r.id = a.report_id
               WHERE a.type='changed_query' AND a.status IN ('open','acknowledged','investigating')"""
        ).fetchall()
        assert len(actions) == 1
        assert actions[0]["report_name"] == "Weekly_Sales"
        changed = db.execute(
            "SELECT * FROM query_versions WHERE action_id = ?", (actions[0]["id"],)
        ).fetchall()
        assert [row["artifact_name"] for row in changed] == ["Sales Orders"]
        run = db.execute(
            "SELECT changed_queries FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run["changed_queries"] == 1

    # Idempotent rerun: no new versions, no new actions, count back to zero
    third = runner.run_scan(str(reports_root), run_followup_probe=False)
    assert third["changed_queries"] == 0
    with database.get_db() as db:
        assert db.execute(
            """SELECT COUNT(*) AS c FROM actions
               WHERE type='changed_query' AND status IN ('open','acknowledged','investigating')"""
        ).fetchone()["c"] == 1
