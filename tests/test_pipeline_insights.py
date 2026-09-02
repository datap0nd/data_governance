import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app import config, database, pipeline_insights, pipeline_insights_db, settings as app_settings
from app.ai.protocol import AssistantTurn, ToolCall
from app.ai.runtime_config import environment_settings
from app.pipeline_insights_settings import save_pipeline_insights_settings
from app.scanner import pipeline_insights as scanner
from app.scanner import pg_deps
from app.scanner import runner


@pytest.fixture()
def insights_store(monkeypatch):
    with TemporaryDirectory(
        prefix="metronome-pipeline-insights-", ignore_cleanup_errors=True
    ) as folder:
        main_path = Path(folder) / "governance.db"
        sidecar_path = Path(folder) / "pipeline_insights.db"
        monkeypatch.setattr(database, "DB_PATH", str(main_path))
        monkeypatch.setattr(app_settings, "DB_PATH", str(main_path))
        monkeypatch.setattr(pipeline_insights_db, "PIPELINE_INSIGHTS_DB_PATH", str(sidecar_path))
        database.init_db()
        pipeline_insights_db.init_pipeline_insights_db()
        yield main_path, sidecar_path


def _source(db, source_id, name, kind, *, server="pg:5432", database_name="warehouse"):
    db.execute(
        "INSERT INTO sources(id, name, type, discovered_by) VALUES (?, ?, 'postgresql', 'scanner')",
        (source_id, name),
    )
    db.execute(
        """INSERT INTO source_postgres_identities
               (source_id, server_name, database_name, schema_name, relation_name, relation_kind)
           VALUES (?, ?, ?, 'public', ?, ?)""",
        (source_id, server, database_name, name, kind),
    )


def test_sidecar_has_independent_version_and_sqlite_safety_pragmas(insights_store):
    main_path, sidecar_path = insights_store
    assert main_path.exists() and sidecar_path.exists()
    with pipeline_insights_db.get_insights_db() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == database.SQLITE_BUSY_TIMEOUT_MS
        assert db.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone()[0] == str(pipeline_insights_db.SCHEMA_VERSION)


def test_relation_extract_quotes_hostile_names_and_serializes_binary_safely():
    class Cursor:
        description = [("payload", 17)]

        def __init__(self):
            self.calls = []
            self._schema = True

        def execute(self, statement, parameters=()):
            self.calls.append((statement, parameters))

        def fetchall(self):
            if len(self.calls) == 1:
                return [("payload", "bytea")]
            return [(b"abc",)]

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()

        def cursor(self):
            return self.cursor_value

        def close(self):
            raise AssertionError("a shared endpoint connection must remain open")

    connection = Connection()
    identity = {
        "server_name": "pg", "database_name": "warehouse",
        "schema_name": 'odd"schema', "relation_name": 'table"; DROP TABLE x; --',
    }
    result = pipeline_insights.extract_relation(
        identity, limit=15, connection=connection, close_connection=False
    )
    statement, parameters = connection.cursor_value.calls[1]
    assert 'FROM "odd""schema"."table""; DROP TABLE x; --" LIMIT %s' in statement
    assert parameters == (15,)
    assert result["columns"] == [{"name": "payload", "type": "bytea"}]
    assert result["rows"] == [["<binary: 3 bytes>"]]
    assert result["truncated"] is True


def test_pipeline_relation_enumeration_uses_live_roots_recursive_closure_and_exact_exclusions(
    insights_store, monkeypatch,
):
    flow_server = "flow.example"
    monkeypatch.setattr(config, "UPLOAD_PGHOST", "flow.example")
    monkeypatch.setattr(config, "UPLOAD_PGPORT", 5432)
    with database.get_db() as db:
        _source(db, 1, "summary_mv", "materialized_view")
        _source(db, 2, "stage_view", "view")
        _source(db, 3, "raw_table", "table")
        _source(db, 4, "unattached", "table")
        _source(db, 5, "remote_foreign", "foreign_table")
        _source(db, 6, "flow_target", "table", server=flow_server)
        _source(db, 7, "stale_flow_target", "table", server=flow_server)
        db.execute("INSERT INTO reports(id, name) VALUES (1, 'Active report')")
        db.execute(
            "INSERT INTO report_tables(report_id, table_name, source_id) VALUES (1, 'Model', 1)"
        )
        db.execute("INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (1, 2)")
        db.execute("INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (2, 3)")
        db.execute("INSERT INTO source_dependencies(source_id, depends_on_id) VALUES (2, 5)")
        db.execute(
            "INSERT INTO flow_sites(id, name, adapter) VALUES (100, 'Insight Site', 'web_export')"
        )
        db.execute(
            "INSERT INTO flow_reports(id, site_id, name, report_url) VALUES (100, 100, 'Flow report', 'https://example.test')"
        )
        for flow_id, source_id, table_name in (
            (1, 6, "flow_target"),
            (2, 7, "different_target"),
        ):
            db.execute(
                """INSERT INTO flows
                       (id, name, site_id, report_id, target_folder, filename_template,
                        sql_handoff_enabled, sql_database, sql_schema, sql_table,
                        sql_target_source_id)
                       VALUES (?, ?, 100, 100, 'C:/tmp', 'x.csv', 1, 'warehouse',
                           'public', ?, ?)""",
                (flow_id, f"Flow {flow_id}", table_name, source_id),
            )

    relations = pipeline_insights.pipeline_relations()
    assert {item["source_id"] for item in relations} == {1, 2, 3, 5, 6}
    assert {item["relation_kind"] for item in relations} >= {
        "table", "view", "materialized_view", "foreign_table"
    }

    save_pipeline_insights_settings({"exclusions": ["pg/warehouse/public.raw_table"]})
    assert {item["source_id"] for item in pipeline_insights.pipeline_relations()} == {1, 2, 5, 6}


def test_ordinary_view_definition_capture_is_scoped_to_dependency_parent(monkeypatch):
    class Cursor:
        def __init__(self):
            self.rows = []
            self.one = None
            self.calls = []

        def execute(self, statement, parameters=None):
            self.calls.append((statement, parameters))
            if "FROM pg_depend" in statement:
                self.rows = [
                    ("sales", "stage_view", "v", "sales", "raw_table", "r")
                ]
            elif "FROM pg_matviews" in statement:
                self.rows = []
            elif "pg_get_viewdef" in statement:
                assert parameters == ("sales", "stage_view")
                self.one = ("SELECT * FROM sales.raw_table",)
            else:
                raise AssertionError(statement)

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.one

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()

        def cursor(self):
            return self.cursor_value

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(
        pg_deps, "_connection_for_database", lambda _database: connection
    )
    catalog = pg_deps._fetch_database_catalog("warehouse")
    assert catalog.parent_kinds == {("sales", "stage_view"): "v"}
    assert catalog.definitions == {
        ("sales", "stage_view"): "SELECT * FROM sales.raw_table"
    }
    rendered = "\n".join(call[0] for call in connection.cursor_value.calls)
    assert "FROM pg_views" not in rendered
    assert "n.nspname=%s AND c.relname=%s" in rendered


class _ReusableConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_relation_sample_preserves_success_on_failure_and_prunes_only_after_clean_run(
    insights_store, monkeypatch,
):
    relation = {
        "source_id": 1,
        "server_name": "pg:5432",
        "database_name": "warehouse",
        "schema_name": "public",
        "relation_name": "products",
        "relation_kind": "table",
    }
    relation["identity_key"] = pipeline_insights.canonical_identity(relation)
    monkeypatch.setattr(scanner, "pipeline_relations", lambda: [dict(relation)])
    connection = _ReusableConnection()
    monkeypatch.setattr(scanner, "open_relation_connection", lambda _identity: connection)
    monkeypatch.setattr(scanner, "scanner_job_heartbeat", lambda *_a, **_kw: None)
    responses = iter([
        {"status": "completed", "columns": [{"name": "id", "type": "integer"}], "rows": [[1]], "truncated": False},
        {"status": "failed", "error_code": "permission_denied", "error_message": "Read denied."},
        {"status": "completed", "columns": [{"name": "id", "type": "integer"}], "rows": [[2]], "truncated": False},
    ])
    monkeypatch.setattr(scanner, "extract_relation", lambda *_a, **_kw: next(responses))

    assert scanner.run_relation_samples()["status"] == "completed"
    with pipeline_insights_db.get_insights_db() as db:
        db.execute(
            """INSERT INTO relation_samples
                   (identity_key, server_name, database_name, schema_name, relation_name,
                    relation_kind, last_attempt_at, last_attempt_status)
               VALUES ('obsolete', 'pg:5432', 'warehouse', 'public', 'old', 'table',
                       '2026-01-01', 'completed')"""
        )

    assert scanner.run_relation_samples()["status"] == "failed"
    with pipeline_insights_db.get_insights_db() as db:
        current = db.execute(
            "SELECT rows_json, sampled_at, last_attempt_status FROM relation_samples WHERE identity_key=?",
            (relation["identity_key"],),
        ).fetchone()
        assert json.loads(current["rows_json"]) == [[1]]
        assert current["sampled_at"]
        assert current["last_attempt_status"] == "failed"
        assert db.execute("SELECT 1 FROM relation_samples WHERE identity_key='obsolete'").fetchone()

    assert scanner.run_relation_samples()["status"] == "completed"
    with pipeline_insights_db.get_insights_db() as db:
        assert db.execute("SELECT 1 FROM relation_samples WHERE identity_key='obsolete'").fetchone() is None
    assert connection.closed is True


def _candidate():
    identity = {
        "source_id": 1,
        "server_name": "pg:5432",
        "database_name": "warehouse",
        "schema_name": "public",
        "relation_name": "products",
        "relation_kind": "table",
    }
    identity["identity_key"] = pipeline_insights.canonical_identity(identity)
    target = {**identity, "source_id": 2, "relation_name": "product_summary", "relation_kind": "materialized_view"}
    target["identity_key"] = pipeline_insights.canonical_identity(target)
    key = pipeline_insights.edge_key(
        "postgres_dependency", pipeline_insights.relation_ref(identity), pipeline_insights.relation_ref(target)
    )
    return {
        "edge_kind": "postgres_dependency",
        "source_id": 2,
        "depends_on_id": 1,
        "edge_key": key,
        "from_key": pipeline_insights.relation_ref(identity),
        "to_key": pipeline_insights.relation_ref(target),
        "from_name": "public.products",
        "to_name": "public.product_summary",
        "source_identity": identity,
        "target_identity": target,
        "definition": "SELECT product_name FROM public.products",
        "definition_hash": "sql-hash",
        "tmdl": "",
        "semantic_columns": [],
        "visual_fields": [],
        "source_schema": [{"name": "product_name", "type": "text"}],
        "target_schema": [{"name": "product_name", "type": "text"}],
        "structural_base": {
            "kind": "postgres_dependency", "from": "source", "to": "target",
            "definition_hash": "sql-hash", "tmdl_hash": "", "source_schema": [{"name": "product_name", "type": "text"}],
            "target_schema": [{"name": "product_name", "type": "text"}], "semantic_columns": [],
            "visual_fields": [], "prompt_version": pipeline_insights.PROMPT_VERSION,
        },
    }


class _Provider:
    def __init__(self, edge_key):
        self.edge_key = edge_key
        self.calls = 0

    def _payload(self, messages, tools):
        return {"model": "Qwen/test", "messages": messages, "tools": tools}

    def complete(self, _messages, _tools, **_kwargs):
        self.calls += 1
        return AssistantTurn(tool_calls=(ToolCall(
            "call-1", "submit_pipeline_explanations", {
                "explanations": [{
                    "edge_key": self.edge_key,
                    "sentence": "public.products supplies product_name to public.product_summary.",
                    "confidence": "high",
                    "source_columns": ["product_name"],
                    "target_columns": ["product_name"],
                }]
            }
        ),))


def test_explanations_skip_preview_and_structurally_unchanged_ai_without_row_queries(
    insights_store, monkeypatch,
):
    preview = replace(environment_settings(), mode="preview")
    monkeypatch.setattr(scanner, "build_edge_candidates", lambda **_kw: pytest.fail("preview enumerated evidence"))
    assert scanner.run_pipeline_explanations(settings=preview)["status"] == "skipped"

    candidate = _candidate()
    monkeypatch.setattr(scanner, "build_edge_candidates", lambda **_kw: [dict(candidate)])
    monkeypatch.setattr(scanner, "relation_schemas", lambda: {})
    monkeypatch.setattr(scanner, "save_relation_schema", lambda *_a, **_kw: None)
    monkeypatch.setattr(scanner, "scanner_job_heartbeat", lambda *_a, **_kw: None)
    limits = []

    def extract(_identity, *, limit, **_kwargs):
        limits.append(limit)
        return {
            "status": "completed",
            "columns": [{"name": "product_name", "type": "text"}],
            "rows": [["first" if len(limits) < 4 else "changed"]],
            "truncated": False,
        }

    monkeypatch.setattr(scanner, "extract_relation", extract)
    settings = replace(
        environment_settings(), mode="qwen", endpoint="http://qwen.test/v1/chat/completions",
        model="Qwen/test", pipeline_explanations_enabled=True,
    )
    provider = _Provider(candidate["edge_key"])
    first = scanner.run_pipeline_explanations(settings=settings, provider=provider)
    assert first["status"] == "completed"
    assert provider.calls == 1
    assert 100 in limits

    limits.clear()
    second = scanner.run_pipeline_explanations(settings=settings, provider=provider)
    assert second["status"] == "completed"
    assert second["unchanged"] == 1
    assert provider.calls == 1
    assert limits and set(limits) == {0}


def test_cached_hover_read_never_calls_postgres_and_exclusions_hide_existing_values(
    insights_store, monkeypatch,
):
    with database.get_db() as db:
        _source(db, 1, "secret_table", "table")
    identity = {
        "server_name": "pg:5432", "database_name": "warehouse",
        "schema_name": "public", "relation_name": "secret_table",
    }
    key = pipeline_insights.canonical_identity(identity)
    with pipeline_insights_db.get_insights_db() as db:
        db.execute(
            """INSERT INTO relation_samples
                   (identity_key, source_id, server_name, database_name, schema_name,
                    relation_name, relation_kind, columns_json, rows_json, sampled_at,
                    last_attempt_at, last_attempt_status)
               VALUES (?, 1, 'pg:5432', 'warehouse', 'public', 'secret_table', 'table',
                       '[{"name":"value","type":"text"}]', '[["cached"]]',
                       '2026-01-01', '2026-01-01', 'completed')""",
            (key,),
        )
    monkeypatch.setattr(pipeline_insights, "extract_relation", lambda *_a, **_kw: pytest.fail("live query"))
    assert pipeline_insights.cached_sample_for_source(1)["rows"] == [["cached"]]
    save_pipeline_insights_settings({"exclusions": ["pg/warehouse/public.secret_table"]})
    hidden = pipeline_insights.cached_sample_for_source(1)
    assert hidden["last_attempt_status"] == "excluded"
    assert hidden["rows"] == []


def test_normal_backup_copies_only_governance_database(insights_store, monkeypatch):
    main_path, sidecar_path = insights_store
    main_path.write_bytes(b"main")
    sidecar_path.write_bytes(b"private cache")
    monkeypatch.setattr(runner, "DB_PATH", str(main_path))
    runner._backup_db()
    files = list((main_path.parent / "backups").iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("governance_")
    assert files[0].read_bytes() == b"main"
    assert all("pipeline_insights" not in path.name for path in files)


def test_weekly_scheduler_orders_modules_and_retries_busy_lane(insights_store, monkeypatch):
    from app import main
    from app.routers import scanner as scanner_router

    save_pipeline_insights_settings({
        "samples_scheduled": True,
        "explanations_scheduled": True,
        "weekday": "sunday",
        "time": "10:00",
    })
    requested = []
    monkeypatch.setattr(
        scanner_router,
        "start_scheduled_pipeline_insights_job",
        lambda module_keys: requested.append(module_keys) or {
            "accepted": False, "status": "busy", "job": {"id": 99},
        },
    )
    scheduled = []
    monkeypatch.setattr(
        main._scheduler, "add_job",
        lambda function, trigger, **kwargs: scheduled.append((function, trigger, kwargs)),
    )

    result = main._scheduled_pipeline_insights()

    assert requested == [("relation_samples", "pipeline_explanations")]
    assert result["retry_scheduled_for"]
    assert scheduled[0][1] == "date"
    assert scheduled[0][2]["id"] == main._PIPELINE_INSIGHTS_RETRY_JOB_ID
    assert scheduled[0][2]["args"] == [1]


@pytest.mark.parametrize("text", [
    "**Bold** explanation.",
    "First sentence. Second sentence.",
    "<b>HTML</b> explanation.",
    "Multiline\nexplanation.",
])
def test_explanation_output_rejects_markdown_html_and_multiple_sentences(text):
    with pytest.raises(ValueError):
        scanner.ExplanationItem(
            edge_key="a" * 64,
            sentence=text,
            confidence="high",
            source_columns=[],
            target_columns=[],
        )
