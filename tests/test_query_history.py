from pathlib import Path

import pytest
from fastapi import HTTPException

from app import database
from app.query_history import (
    MATERIALIZED_VIEW_KIND,
    REPORT_M_KIND,
    observe_query,
    report_artifact_key,
)
from app.routers.actions import list_actions
from app.routers.query_history import compare_query_versions, report_query_history
from app.scanner import pg_deps, runner
from app.scanner.tmdl_parser import ParsedTable, SourceInfo
from app.scanner.walker import DiscoveredReport
from app.source_identity import upsert_postgres_identity


@pytest.fixture
def query_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "query-history.db"))
    database.init_db()
    return tmp_path


def _pg_source(expression: str, table: str = "reporting.shared_table") -> SourceInfo:
    return SourceInfo(
        source_type="postgresql",
        server="warehouse",
        database="analytics",
        sql_table=table,
        raw_expression=expression,
    )


def _report(name: str, owner: str, expressions: dict[str, str]) -> DiscoveredReport:
    return DiscoveredReport(
        name=name,
        tmdl_path=f"C:/{name}",
        report_owner=owner,
        tables=[
            ParsedTable(
                table_name=table_name,
                m_expression=expression,
                source=_pg_source(expression),
            )
            for table_name, expression in expressions.items()
        ],
    )


def _stub_scan_side_effects(monkeypatch):
    from app import usage
    from app.routers import best_practices, documentation, schedules
    from app.scanner import pg_cron

    monkeypatch.setattr(pg_deps, "scan_pg_dependencies", lambda scan_run_id=None: {
        "status": "completed", "changed_queries": 0, "query_change_log": "",
    })
    monkeypatch.setattr(pg_cron, "scan_pg_cron", lambda: {"status": "completed"})
    monkeypatch.setattr(usage, "sync_usage_from_csv_if_configured", lambda db: {"status": "skipped"})
    monkeypatch.setattr(best_practices, "run_best_practice_scan", lambda persist=False: {"status": "completed"})
    monkeypatch.setattr(schedules, "run_schedule_discrepancy_scan", lambda persist=True: {"status": "completed"})
    monkeypatch.setattr(documentation, "sync_documentation_completeness_actions", lambda: {"status": "completed"})


def test_shared_source_change_is_attributed_to_only_the_changed_report(query_db, monkeypatch):
    reports = [
        _report("Report A", "Owner A", {"Shared": "let Source = 1 in Source"}),
        _report("Report B", "Owner B", {"Shared": "let Source = 2 in Source"}),
    ]
    monkeypatch.setattr(runner, "walk_reports_root", lambda root: reports)
    _stub_scan_side_effects(monkeypatch)

    baseline = runner.run_scan(str(query_db), run_followup_probe=False)
    assert baseline["changed_queries"] == 0

    reports[1] = _report("Report B", "Owner B", {"Shared": "let Source = 3 in Source"})
    changed = runner.run_scan(str(query_db), run_followup_probe=False)
    assert changed["changed_queries"] == 1

    with database.get_db() as db:
        action = db.execute(
            """SELECT a.report_id, a.source_id, a.assigned_to, r.name
               FROM actions a JOIN reports r ON r.id = a.report_id
               WHERE a.type='changed_query' AND a.status='open'"""
        ).fetchone()
        versions = db.execute(
            """SELECT qv.artifact_name, qv.report_id, qv.action_id
               FROM query_versions qv WHERE qv.is_baseline=0"""
        ).fetchall()
    assert dict(action) == {
        "report_id": 2,
        "source_id": None,
        "assigned_to": "Owner B",
        "name": "Report B",
    }
    assert len(versions) == 1
    assert versions[0]["report_id"] == 2
    assert versions[0]["artifact_name"] == "Shared"
    api_action = next(action for action in list_actions(status="open") if action.type == "changed_query")
    assert api_action.asset_type == "report"
    assert api_action.asset_name == "Report B"
    assert [change.artifact_name for change in api_action.query_changes] == ["Shared"]


def test_multiple_m_changes_group_into_one_report_action_and_ignore_whitespace(query_db, monkeypatch):
    reports = [_report("Grouped Report", "Owner", {
        "One": "let\n    Source = 1\nin\n    Source",
        "Two": "let Source = 2 in Source",
    })]
    monkeypatch.setattr(runner, "walk_reports_root", lambda root: reports)
    _stub_scan_side_effects(monkeypatch)
    runner.run_scan(str(query_db), run_followup_probe=False)

    # Outer/trailing whitespace and CRLF changes normalize away.
    reports[0] = _report("Grouped Report", "Owner", {
        "One": "  \r\n  let\r\n    Source = 1   \r\nin\r\n    Source\r\n  ",
        "Two": "let Source = 2 in Source",
    })
    unchanged = runner.run_scan(str(query_db), run_followup_probe=False)
    assert unchanged["changed_queries"] == 0

    reports[0] = _report("Grouped Report", "Owner", {
        "One": "let\n    Source = 10\nin\n    Source",
        "Two": "// keep this comment\nlet Source = 20 in Source",
    })
    changed = runner.run_scan(str(query_db), run_followup_probe=False)
    assert changed["changed_queries"] == 2
    with database.get_db() as db:
        actions = db.execute(
            "SELECT id FROM actions WHERE type='changed_query' AND status='open'"
        ).fetchall()
        linked = db.execute(
            "SELECT artifact_name FROM query_versions WHERE action_id=? ORDER BY artifact_name",
            (actions[0]["id"],),
        ).fetchall()
        scan_count = db.execute(
            "SELECT changed_queries FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()["changed_queries"]
    assert len(actions) == 1
    assert [row["artifact_name"] for row in linked] == ["One", "Two"]
    assert scan_count == 2


def test_query_history_supports_reverts_and_same_artifact_diff(query_db):
    with database.get_db() as db:
        db.execute("INSERT INTO reports (id, name, archived) VALUES (1, 'History Report', 0)")
        first = observe_query(
            db,
            artifact_kind=REPORT_M_KIND,
            artifact_key=report_artifact_key(1, "Sales"),
            report_id=1,
            source_id=None,
            artifact_name="Sales",
            language="m",
            query_text="let Source = 1 in Source",
            scan_run_id=None,
            detected_at="2026-08-01T10:00:00+00:00",
        )
        second = observe_query(
            db,
            artifact_kind=REPORT_M_KIND,
            artifact_key=report_artifact_key(1, "Sales"),
            report_id=1,
            source_id=None,
            artifact_name="Sales",
            language="m",
            query_text="let Source = 2 in Source",
            scan_run_id=None,
            detected_at="2026-08-02T10:00:00+00:00",
        )
        reverted = observe_query(
            db,
            artifact_kind=REPORT_M_KIND,
            artifact_key=report_artifact_key(1, "Sales"),
            report_id=1,
            source_id=None,
            artifact_name="Sales",
            language="m",
            query_text="let Source = 1 in Source",
            scan_run_id=None,
            detected_at="2026-08-03T10:00:00+00:00",
        )
    assert not first.changed
    assert second.changed and reverted.changed
    assert reverted.version_id != first.version_id

    history = report_query_history(1)
    assert [version.id for version in history[0].versions] == [
        reverted.version_id, second.version_id, first.version_id,
    ]
    diff = compare_query_versions(second.version_id, reverted.version_id)
    assert any(row.kind == "changed" for row in diff.rows)

    with database.get_db() as db:
        db.execute("INSERT INTO reports (id, name, archived) VALUES (2, 'Other', 0)")
        other = observe_query(
            db,
            artifact_kind=REPORT_M_KIND,
            artifact_key=report_artifact_key(2, "Other"),
            report_id=2,
            source_id=None,
            artifact_name="Other",
            language="m",
            query_text="let Source = 1 in Source",
            scan_run_id=None,
            detected_at="2026-08-03T10:00:00+00:00",
        )
    with pytest.raises(HTTPException) as exc:
        compare_query_versions(first.version_id, other.version_id)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        compare_query_versions(first.version_id, 999999)
    assert exc.value.status_code == 404

    with database.get_db() as db:
        db.execute("UPDATE reports SET archived=1 WHERE id=1")
    assert report_query_history(1)[0].versions[-1].id == first.version_id


def test_m_query_removal_restoration_and_rerun_are_ordered_and_idempotent(query_db, monkeypatch):
    reports = [_report("Removal Report", "Owner", {"Sales": "let Source = 1 in Source"})]
    monkeypatch.setattr(runner, "walk_reports_root", lambda root: reports)
    _stub_scan_side_effects(monkeypatch)
    assert runner.run_scan(str(query_db), run_followup_probe=False)["changed_queries"] == 0

    reports[0] = DiscoveredReport(
        name="Removal Report",
        tmdl_path="C:/Removal Report",
        report_owner="Owner",
        tables=[ParsedTable(table_name="Sales", m_expression=None, source=None)],
    )
    assert runner.run_scan(str(query_db), run_followup_probe=False)["changed_queries"] == 1

    reports[0] = _report("Removal Report", "Owner", {"Sales": "let Source = 1 in Source"})
    assert runner.run_scan(str(query_db), run_followup_probe=False)["changed_queries"] == 1
    assert runner.run_scan(str(query_db), run_followup_probe=False)["changed_queries"] == 0

    history = report_query_history(1)[0].versions
    assert len(history) == 3
    assert [version.is_baseline for version in history] == [False, False, True]
    with database.get_db() as db:
        actions = db.execute(
            "SELECT status FROM actions WHERE type='changed_query' ORDER BY id"
        ).fetchall()
    assert [row["status"] for row in actions] == ["resolved", "open"]


def test_mv_changes_are_included_in_overall_scan_count_and_log(query_db, monkeypatch):
    monkeypatch.setattr(runner, "walk_reports_root", lambda root: [])
    _stub_scan_side_effects(monkeypatch)
    monkeypatch.setattr(pg_deps, "scan_pg_dependencies", lambda scan_run_id=None: {
        "status": "completed",
        "changed_queries": 1,
        "query_change_log": "CHANGED MV QUERY: public.sales_mv",
    })
    result = runner.run_scan(str(query_db), run_followup_probe=False)
    assert result["changed_queries"] == 1
    with database.get_db() as db:
        scan = db.execute(
            "SELECT changed_queries, log FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert scan["changed_queries"] == 1
    assert "CHANGED MV QUERY: public.sales_mv" in scan["log"]


class _CatalogCursor:
    def __init__(self, dependencies, definitions):
        self.dependencies = dependencies
        self.definitions = definitions
        self.result = []

    def execute(self, sql):
        self.result = self.definitions if "FROM pg_matviews" in sql else self.dependencies

    def fetchall(self):
        return list(self.result)


class _CatalogConnection:
    def __init__(self, dependencies, definitions):
        self.cursor_value = _CatalogCursor(dependencies, definitions)
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def close(self):
        self.closed = True


def test_mv_definition_change_alerts_the_tracked_mv(query_db, monkeypatch):
    with database.get_db() as db:
        db.execute(
            """INSERT INTO sources (id, name, type, connection_info, owner, discovered_by, archived)
               VALUES (10, 'public.sales_mv', 'postgresql', 'public.sales_mv', 'MV Owner', 'scan', 0)"""
        )
        db.execute("INSERT INTO reports (id, name, archived) VALUES (1, 'MV Report', 0)")
        db.execute(
            "INSERT INTO report_tables (report_id, table_name, source_id) VALUES (1, 'Sales', 10)"
        )
        upsert_postgres_identity(
            db,
            source_id=10,
            server=pg_deps.PGHOST,
            database=pg_deps.PGDATABASE,
            schema="public",
            relation="sales_mv",
            relation_kind="materialized_view",
        )

    dependencies = [("public", "sales_mv", "public", "sales", "r")]
    first_connection = _CatalogConnection(
        dependencies,
        [("public", "sales_mv", "SELECT id, amount FROM public.sales")],
    )
    monkeypatch.setattr(pg_deps, "_get_pg_connection", lambda: first_connection)
    baseline = pg_deps.scan_pg_dependencies()
    assert baseline["changed_queries"] == 0

    second_connection = _CatalogConnection(
        dependencies,
        [("public", "sales_mv", "SELECT id, amount, region FROM public.sales")],
    )
    monkeypatch.setattr(pg_deps, "_get_pg_connection", lambda: second_connection)
    changed = pg_deps.scan_pg_dependencies()
    assert changed["changed_queries"] == 1
    assert "public.sales_mv" in changed["query_change_log"]

    with database.get_db() as db:
        action = db.execute(
            "SELECT id, source_id, report_id, assigned_to FROM actions WHERE type='changed_query' AND status='open'"
        ).fetchone()
        versions = db.execute(
            "SELECT artifact_kind, artifact_name, action_id FROM query_versions WHERE source_id=10 ORDER BY id"
        ).fetchall()
    assert dict(action) == {
        "id": action["id"],
        "source_id": 10,
        "report_id": None,
        "assigned_to": "MV Owner",
    }
    assert len(versions) == 2
    assert versions[-1]["artifact_kind"] == MATERIALIZED_VIEW_KIND
    assert versions[-1]["action_id"] == action["id"]


def test_upstream_mv_uses_linked_report_owner_and_impact_context(query_db, monkeypatch):
    with database.get_db() as db:
        db.execute(
            """INSERT INTO sources (id, name, type, connection_info, discovered_by, archived)
               VALUES (10, 'public.consumer_mv', 'postgresql', 'public.consumer_mv', 'scan', 0)"""
        )
        db.execute(
            """INSERT INTO sources (id, name, type, connection_info, discovered_by, archived)
               VALUES (11, 'public.sales_mv', 'postgresql', 'public.sales_mv', 'scan', 0)"""
        )
        db.execute(
            "INSERT INTO reports (id, name, owner, archived) VALUES (1, 'Impact Report', 'Report Owner', 0)"
        )
        db.execute(
            "INSERT INTO report_tables (report_id, table_name, source_id) VALUES (1, 'Consumer', 10)"
        )
        upsert_postgres_identity(
            db,
            source_id=10,
            server=pg_deps.PGHOST,
            database=pg_deps.PGDATABASE,
            schema="public",
            relation="consumer_mv",
            relation_kind="materialized_view",
        )
        upsert_postgres_identity(
            db,
            source_id=11,
            server=pg_deps.PGHOST,
            database=pg_deps.PGDATABASE,
            schema="public",
            relation="sales_mv",
            relation_kind="materialized_view",
        )

    dependencies = [("public", "consumer_mv", "public", "sales_mv", "m")]
    baseline_connection = _CatalogConnection(dependencies, [
        ("public", "consumer_mv", "SELECT * FROM public.sales_mv"),
        ("public", "sales_mv", "SELECT id, amount FROM public.sales"),
    ])
    monkeypatch.setattr(pg_deps, "_get_pg_connection", lambda: baseline_connection)
    assert pg_deps.scan_pg_dependencies()["changed_queries"] == 0

    changed_connection = _CatalogConnection(dependencies, [
        ("public", "consumer_mv", "SELECT * FROM public.sales_mv"),
        ("public", "sales_mv", "SELECT id, amount, region FROM public.sales"),
    ])
    monkeypatch.setattr(pg_deps, "_get_pg_connection", lambda: changed_connection)
    assert pg_deps.scan_pg_dependencies()["changed_queries"] == 1

    mv_action = next(action for action in list_actions(status="open") if action.type == "changed_query")
    assert mv_action.source_id == 11
    assert mv_action.assigned_to == "Report Owner"
    assert mv_action.report_names == ["Impact Report"]


def test_frontend_contains_query_history_and_diff_controls():
    app_js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "style.css").read_text(encoding="utf-8")
    assert "/api/query-history/report/" in app_js
    assert "/api/query-history/materialized-view/" in app_js
    assert "query-diff-open" in app_js
    assert ".query-diff-table" in css
