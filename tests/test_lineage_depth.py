import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


if "fastapi" not in sys.modules:
    fastapi_stub = types.ModuleType("fastapi")

    class APIRouter:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return lambda fn: fn

        def post(self, *args, **kwargs):
            return lambda fn: fn

        def patch(self, *args, **kwargs):
            return lambda fn: fn

        def put(self, *args, **kwargs):
            return lambda fn: fn

        def delete(self, *args, **kwargs):
            return lambda fn: fn

    class HTTPException(Exception):
        def __init__(self, status_code=None, detail=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    def Query(default=None, *args, **kwargs):
        return default

    fastapi_stub.APIRouter = APIRouter
    fastapi_stub.HTTPException = HTTPException
    fastapi_stub.Query = Query
    fastapi_stub.Request = object
    sys.modules["fastapi"] = fastapi_stub


from app.database import get_db, init_db
import app.database as database
from app.routers.lineage import get_lineage_diagram
from app.routers.sources import list_sources
import app.scanner.pg_deps as pg_deps


_SCAN_PG_DEPENDENCIES = pg_deps.scan_pg_dependencies


class _FakePgCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _sql):
        pass

    def fetchall(self):
        return list(self.rows)


class _FakePgConnection:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def cursor(self):
        return _FakePgCursor(self.rows)

    def close(self):
        self.closed = True


class LineageDepthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "governance.db")
        database.DB_PATH = self.db_path
        init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_report_graph(self, source_ids, edges):
        with get_db() as db:
            db.execute(
                """INSERT INTO reports (id, name, owner, archived)
                   VALUES (1, 'Deep Lineage Report', 'Report Owner', 0)"""
            )
            for source_id in source_ids:
                source_type = "excel" if source_id == max(source_ids) else "postgresql"
                db.execute(
                    """INSERT INTO sources
                       (id, name, type, connection_info, discovered_by, archived)
                       VALUES (?, ?, ?, ?, 'pg_deps', 0)""",
                    (
                        source_id,
                        f"source_{source_id}",
                        source_type,
                        f"location_{source_id}",
                    ),
                )
            db.execute(
                """INSERT INTO report_tables
                   (report_id, table_name, source_id, source_expression)
                   VALUES (1, 'ReportTable', ?, 'source expression')""",
                (source_ids[0],),
            )
            db.execute(
                "INSERT INTO report_pages (id, report_id, page_name, page_ordinal) VALUES (100, 1, 'Overview', 0)"
            )
            db.execute(
                """INSERT INTO report_visuals
                   (id, page_id, visual_id, visual_type, title)
                   VALUES (200, 100, 'visual-1', 'table', 'Deep lineage visual')"""
            )
            db.execute(
                """INSERT INTO visual_fields (visual_id, table_name, field_name)
                   VALUES (200, 'ReportTable', 'Amount')"""
            )
            for source_id, depends_on_id in edges:
                db.execute(
                    """INSERT INTO source_dependencies
                       (source_id, depends_on_id, discovered_by)
                       VALUES (?, ?, 'test')""",
                    (source_id, depends_on_id),
                )

    def test_diagram_returns_every_level_and_deep_linked_assets(self):
        source_ids = [10, 11, 12, 13, 14]
        edges = [(10, 11), (11, 12), (12, 13), (13, 14)]
        self._seed_report_graph(source_ids, edges)

        with get_db() as db:
            db.execute(
                "UPDATE sources SET discovered_by = 'scan' WHERE id IN (10, 12, 14)"
            )
            db.execute(
                """INSERT INTO source_probes
                   (source_id, probed_at, last_data_at, row_count, status)
                   VALUES (14, '2026-07-10T10:00:00+00:00',
                           '2026-07-10T09:00:00+00:00', 321, 'fresh')"""
            )
            db.execute(
                """INSERT INTO upstream_systems
                   (id, name, code, refresh_day, archived)
                   VALUES (50, 'Root System', 'ROOT', 'Monday', 0)"""
            )
            db.execute("UPDATE sources SET upstream_id = 50 WHERE id = 14")
            db.execute(
                """INSERT INTO sources
                   (id, name, type, discovered_by, archived)
                   VALUES (90, 'unrelated_source', 'postgresql', 'pg_deps', 0),
                          (91, 'unrelated_root', 'postgresql', 'pg_deps', 0)"""
            )
            db.execute(
                """INSERT INTO source_dependencies
                   (source_id, depends_on_id, discovered_by)
                   VALUES (90, 91, 'test')"""
            )

        result = get_lineage_diagram(1)

        self.assertEqual({s["id"] for s in result["sources"]}, set(source_ids))
        self.assertEqual(
            {(d["source_id"], d["depends_on_id"]) for d in result["source_deps"]},
            set(edges),
        )
        deepest = next(s for s in result["sources"] if s["id"] == 14)
        self.assertEqual(deepest["type"], "excel")
        self.assertEqual(deepest["status"], "fresh")
        self.assertEqual(deepest["row_count"], 321)
        self.assertNotIn("scripts", result)
        self.assertNotIn("scheduled_tasks", result)
        self.assertEqual([u["id"] for u in result["upstreams"]], [50])
        self.assertEqual({s.name for s in list_sources()}, {f"source_{i}" for i in source_ids})

    def test_diagram_preserves_branching_and_shared_ancestors(self):
        source_ids = [10, 11, 12, 13, 14]
        edges = [(10, 11), (10, 12), (11, 13), (12, 13), (13, 14)]
        self._seed_report_graph(source_ids, edges)

        result = get_lineage_diagram(1)

        returned_edges = [
            (d["source_id"], d["depends_on_id"])
            for d in result["source_deps"]
        ]
        self.assertEqual(set(returned_edges), set(edges))
        self.assertEqual(len(returned_edges), len(edges))
        self.assertEqual({s["id"] for s in result["sources"]}, set(source_ids))

    def test_diagram_places_matching_flow_upstream_of_target_source(self):
        self._seed_report_graph([10], [])
        with get_db() as db:
            db.execute(
                """UPDATE reports
                   SET pbi_dataset_id='33333333-3333-3333-3333-333333333333'
                   WHERE id=1"""
            )
            db.execute(
                """UPDATE sources
                   SET name='bi_reporting.asap_fota_output',
                       connection_info='warehouse/postgres/bi_reporting.asap_fota_output'
                   WHERE id=10"""
            )
            db.execute(
                """INSERT INTO flow_sites (id, name, adapter, base_url)
                   VALUES (90, 'Portal', 'web_export', 'https://example.test')"""
            )
            db.execute(
                """INSERT INTO flow_reports (id, site_id, name, report_url)
                   VALUES (1, 90, 'FOTA export', 'https://example.test/fota')"""
            )
            db.execute(
                """INSERT INTO flows
                   (id, name, site_id, report_id, target_folder, filename_template,
                    sql_handoff_enabled, sql_database, sql_schema, sql_table,
                    sql_target_source_id, last_run_at, last_success_at, last_status)
                   VALUES
                   (1, 'FOTA', 90, 1, '/tmp', 'fota.csv', 1, 'postgres',
                    'BI_REPORTING', 'ASAP_FOTA_OUTPUT', 10,
                    '2026-08-15T11:00:00', '2026-08-15T11:00:00', 'succeeded'),
                   (2, 'Unrelated', 90, 1, '/tmp', 'other.csv', 1, 'postgres',
                    'other_schema', 'other_table', NULL, NULL, NULL, NULL)"""
            )

        result = get_lineage_diagram(1)

        self.assertTrue(result["report"]["can_refresh"])
        self.assertEqual(len(result["flows"]), 1)
        self.assertEqual(result["flows"][0]["name"], "FOTA")
        self.assertEqual(result["flows"][0]["target_source_ids"], [10])
        self.assertEqual(result["flows"][0]["last_success_at"], "2026-08-15T11:00:00")

    def test_diagram_terminates_on_dependency_cycle(self):
        source_ids = [10, 11, 12]
        edges = [(10, 11), (11, 12), (12, 10)]
        self._seed_report_graph(source_ids, edges)

        result = get_lineage_diagram(1)

        self.assertEqual(
            {(d["source_id"], d["depends_on_id"]) for d in result["source_deps"]},
            set(edges),
        )
        self.assertEqual({s["id"] for s in result["sources"]}, set(source_ids))

    def test_pg_scan_discovers_reverse_ordered_mv_chain_in_one_run(self):
        with get_db() as db:
            db.execute(
                """INSERT INTO sources
                   (id, name, type, connection_info, discovered_by, archived)
                   VALUES (10, 'public.z_report_mv', 'postgresql',
                           'public.z_report_mv', 'scan', 0)"""
            )
            db.execute(
                """INSERT INTO source_probes
                   (source_id, probed_at, status)
                   VALUES (10, '2026-07-10T10:00:00+00:00', 'fresh')"""
            )
            db.execute(
                "INSERT INTO reports (id, name, archived) VALUES (1, 'Lineage Report', 0)"
            )
            db.execute(
                """INSERT INTO report_tables (report_id, table_name, source_id)
                   VALUES (1, 'ReportTable', 10)"""
            )

        catalog_rows = [
            ("public", "a_upstream_mv", "public", "base_table", "r"),
            ("public", "z_report_mv", "public", "a_upstream_mv", "m"),
        ]
        fake_connection = _FakePgConnection(catalog_rows)

        with patch.object(pg_deps, "_get_pg_connection", return_value=fake_connection):
            result = _SCAN_PG_DEPENDENCIES()

        with get_db() as db:
            sources = {
                row["name"]: row["id"]
                for row in db.execute("SELECT id, name FROM sources").fetchall()
            }
            edges = {
                (row["source_id"], row["depends_on_id"])
                for row in db.execute(
                    "SELECT source_id, depends_on_id FROM source_dependencies"
                ).fetchall()
            }

        self.assertTrue(fake_connection.closed)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["mvs_found"], 2)
        self.assertEqual(result["deps_created"], 2)
        self.assertIn("public.a_upstream_mv", sources)
        self.assertIn("public.base_table", sources)
        visible_source_names = {source.name for source in list_sources()}
        self.assertTrue(
            {"public.z_report_mv", "public.a_upstream_mv", "public.base_table"}
            <= visible_source_names
        )
        self.assertEqual(
            edges,
            {
                (sources["public.z_report_mv"], sources["public.a_upstream_mv"]),
                (sources["public.a_upstream_mv"], sources["public.base_table"]),
            },
        )


if __name__ == "__main__":
    unittest.main()
