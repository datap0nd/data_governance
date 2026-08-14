from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app import flow_sql, flow_worker


@pytest.mark.parametrize(
    ("identifier", "quoted"),
    [
        ("reporting", '"reporting"'),
        ("Import First and Second Activation", '"Import First and Second Activation"'),
        ('Team "Current" Imports', '"Team ""Current"" Imports"'),
        ('name"; DROP TABLE users; --', '"name""; DROP TABLE users; --"'),
    ],
)
def test_sql_identifier_quoting_supports_discovered_postgres_names(identifier, quoted):
    assert flow_sql._quote_identifier(identifier) == quoted


@pytest.mark.parametrize("identifier", ["", "invalid\x00identifier", "x" * 64, None])
def test_sql_identifier_quoting_rejects_values_postgres_cannot_identify(identifier):
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        flow_sql._quote_identifier(identifier)


def test_asap_csv_normalization_removes_title_and_blank_row(tmp_path):
    path = tmp_path / "download.csv"
    path.write_text(
        'Report title\n\n"Sell-in Region","Active"\n"MIDDLE EAST","116"\n',
        encoding="utf-8",
    )
    result = flow_worker._normalize_csv(path)
    assert result["preamble_rows_removed"] == 2
    assert result["columns"] == ["Sell-in Region", "Active"]
    assert path.read_text(encoding="utf-8-sig").splitlines() == [
        "Sell-in Region,Active", "MIDDLE EAST,116",
    ]
    assert flow_worker._csv_metadata(path)["row_count"] == 1


def test_asap_csv_normalization_detects_semicolon_delimiter(tmp_path):
    path = tmp_path / "download.csv"
    path.write_text(
        'Report title\n\n"Sell-in Region";"Active"\n"MIDDLE EAST";"116"\n',
        encoding="cp1252",
    )
    result = flow_worker._normalize_csv(path)
    assert result["source_delimiter"] == ";"
    assert result["columns"] == ["Sell-in Region", "Active"]
    assert path.read_text(encoding="utf-8-sig").splitlines() == [
        "Sell-in Region,Active", "MIDDLE EAST,116",
    ]


def test_transformations_run_once_per_file_and_use_script_results(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("name,value\na,1\n", encoding="utf-8")
    script = tmp_path / "transform.py"
    script.write_text(
        "from argparse import ArgumentParser\nfrom pathlib import Path\n"
        "parser = ArgumentParser()\nparser.add_argument('--input', required=True)\n"
        "parser.add_argument('--output', required=True)\nargs = parser.parse_args()\n"
        "Path(args.output).write_text(Path(args.input).read_text(), encoding='utf-8')\n",
        encoding="utf-8",
    )
    output = flow_worker._run_transformations(
        [{"file_path": str(source), "filename": source.name, "period_key": ["2026-W01"], "status": "saved"}],
        {"enabled": True, "script_path": str(script)},
    )
    assert len(output) == 1
    assert Path(output[0]["file_path"]).parent.name == "script_results"
    assert output[0]["status"] == "transformed"
    assert output[0]["row_count"] == 1


def test_transformation_requires_reserved_output_file(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("name,value\na,1\n", encoding="utf-8")
    script = tmp_path / "transform.py"
    script.write_text("print('done')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="did not create"):
        flow_worker._run_transformations(
            [{"file_path": str(source), "filename": source.name, "status": "saved"}],
            {"enabled": True, "script_path": str(script)},
        )


def test_powershell_transformation_uses_named_path_parameters(tmp_path):
    command = flow_worker._script_command(
        tmp_path / "transform.ps1", tmp_path / "input.csv", tmp_path / "output.csv",
    )
    assert command[-4:] == [
        "-InputPath", str(tmp_path / "input.csv"),
        "-OutputPath", str(tmp_path / "output.csv"),
    ]


def test_csv_reader_rejects_duplicate_normalized_columns(tmp_path):
    path = tmp_path / "duplicate.csv"
    path.write_text("Sell-out Week,Sell out Week\n202630,202631\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicate column"):
        flow_sql._read_artifact(path)


def test_csv_reader_uses_normalized_comma_format(tmp_path, monkeypatch):
    path = tmp_path / "normalized.csv"
    path.write_text("Sell-out Week,Active\n202630,116\n", encoding="utf-8-sig")
    artifact = flow_sql._read_artifact(path)

    assert artifact["columns"] == ["sell_out_week", "active"]
    assert artifact["row_count"] == 1
    assert artifact["path"] == path


def test_sql_preflight_reports_missing_and_unexpected_columns(tmp_path, monkeypatch):
    path = tmp_path / "wrong.csv"
    pd.DataFrame({"a": [1], "extra": [2]}).to_csv(path, index=False)

    class Result:
        def fetchall(self):
            return [("a", "NO", None, "NO", "NEVER"), ("required_b", "NO", None, "NO", "NEVER")]

    class Connection:
        def __init__(self):
            self.transaction = SimpleNamespace(rollback=lambda: None)

        def begin(self):
            return self.transaction

        def execute(self, statement, *_args, **_kwargs):
            if str(statement).startswith("SET LOCAL"):
                return None
            return Result()

        def close(self):
            return None

    engine = SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None)
    monkeypatch.setattr(flow_sql, "_engine", lambda _database: engine)
    with pytest.raises(RuntimeError, match="missing: required_b; unexpected: extra"):
        flow_sql.load_artifacts(
            [{"file_path": str(path)}],
            {"database": "db", "schema": "reporting", "table": "target", "mode": "append"},
        )


def test_sql_copy_streams_csv_through_postgres_copy(tmp_path):
    copied = {}
    path = tmp_path / "normalized.csv"
    path.write_text("Sell-out Week,Active\n202627,116\n", encoding="utf-8-sig")

    class Cursor:
        def copy_expert(self, statement, stream):
            copied["statement"] = statement
            copied["content"] = stream.read()

        def close(self):
            copied["closed"] = True

    raw = SimpleNamespace(cursor=lambda: Cursor())
    connection = SimpleNamespace(connection=raw)
    artifact = flow_sql._read_artifact(path)

    flow_sql._copy_artifact(connection, artifact, '"bi_reporting"."this_is_test"')

    assert copied["statement"] == (
        'COPY "bi_reporting"."this_is_test" ("sell_out_week", "active") '
        "FROM STDIN WITH (FORMAT CSV, HEADER TRUE, ENCODING 'UTF8')"
    )
    assert copied["content"] == "Sell-out Week,Active\n202627,116\n"
    assert copied["closed"] is True


def test_sql_append_maps_normalized_csv_headers_to_exact_target_columns(tmp_path, monkeypatch):
    path = tmp_path / "normalized.csv"
    path.write_text("Active,Biz Sub\n116,Mobile\n", encoding="utf-8")
    executed = []
    copied = []
    transaction = SimpleNamespace(
        commit=lambda: executed.append("commit"),
        rollback=lambda: executed.append("rollback"),
    )

    class Result:
        def fetchall(self):
            return [
                ("Active", "NO", None, "NO", "NEVER"),
                ("Biz Sub", "NO", None, "NO", "NEVER"),
                ("Optional Legacy", "YES", None, "NO", "NEVER"),
            ]

    class Cursor:
        def copy_expert(self, statement, stream):
            copied.append((statement, stream.read()))

        def close(self):
            return None

    class Connection:
        connection = SimpleNamespace(cursor=lambda: Cursor())

        def begin(self):
            return transaction

        def execute(self, statement, *_args, **_kwargs):
            executed.append(str(statement))
            return None if str(statement).startswith("SET LOCAL") else Result()

        def close(self):
            return None

    monkeypatch.setattr(
        flow_sql, "_engine",
        lambda _database: SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None),
    )

    result = flow_sql.load_artifacts(
        [{"file_path": str(path)}],
        {
            "database": "db", "schema": "Reporting Area",
            "table": "Import First and Second Activation", "mode": "append",
        },
    )

    assert result["rows_written"] == 1
    assert copied == [(
        'COPY "Reporting Area"."Import First and Second Activation" '
        '("Active", "Biz Sub") FROM STDIN '
        "WITH (FORMAT CSV, HEADER TRUE, ENCODING 'UTF8')",
        "Active,Biz Sub\n116,Mobile\n",
    )]
    assert executed[-1] == "commit"
    assert "rollback" not in executed


def test_sql_append_rejects_ambiguous_target_columns_after_normalization():
    with pytest.raises(RuntimeError, match="ambiguous column name.*sell_out_week"):
        flow_sql._target_columns_by_normalized_name(["Sell-out Week", "sell_out_week"])


def test_sql_append_maps_the_live_thirteen_column_shape_to_display_headers():
    csv_columns = [
        "active", "biz_sub", "item", "mkt_name", "sell_in_account",
        "sell_in_customer", "sell_in_region", "sell_in_subsidiary",
        "sell_out_country", "sell_out_month", "sell_out_region",
        "sell_out_subsidiary", "series",
    ]
    target_columns = [
        "Active", "Biz Sub", "Item", "MKT Name", "Sell-in Account",
        "Sell-in Customer", "Sell-in Region", "Sell-in Subsidiary",
        "Sell-out Country", "Sell-out Month", "Sell-out Region",
        "Sell-out Subsidiary", "Series",
    ]

    mapping = flow_sql._target_columns_by_normalized_name(target_columns)

    assert [mapping[column] for column in csv_columns] == target_columns


def test_sql_managed_snapshot_preserves_existing_table_and_commits(tmp_path, monkeypatch):
    path = tmp_path / "normalized.csv"
    path.write_text("Sell-out Week,Active\n202627,116\n", encoding="utf-8-sig")
    executed = []
    copied = []
    transaction = SimpleNamespace(
        commit=lambda: executed.append("commit"),
        rollback=lambda: executed.append("rollback"),
    )

    class Result:
        def fetchall(self):
            return [
                ("sell_out_week", "NO", None, "NO", "NEVER"),
                ("active", "NO", None, "NO", "NEVER"),
            ]

    class Cursor:
        def copy_expert(self, statement, stream):
            copied.append((statement, stream.read()))

        def close(self):
            return None

    class Connection:
        connection = SimpleNamespace(cursor=lambda: Cursor())

        def begin(self):
            return transaction

        def execute(self, statement, *_args, **_kwargs):
            executed.append(str(statement))
            return Result()

        def close(self):
            executed.append("close")

    engine = SimpleNamespace(connect=lambda: Connection(), dispose=lambda: executed.append("dispose"))
    monkeypatch.setattr(flow_sql, "_engine", lambda _database: engine)
    events = []

    result = flow_sql.load_artifacts(
        [{"file_path": str(path), "row_count": 1}],
        {
            "database": "db", "schema": "Reporting Area",
            "table": "Import First and Second Activation", "mode": "replace",
        },
        progress=events.append,
    )

    assert result["rows_written"] == 1
    assert result["target"] == "db.Reporting Area.Import First and Second Activation"
    assert executed.count("commit") == 1
    assert "rollback" not in executed
    assert "SET LOCAL lock_timeout = '30s'" in executed
    assert "SET LOCAL statement_timeout = '120s'" in executed
    staging_create = next(
        item for item in executed
        if item.startswith('CREATE TABLE "Reporting Area"."_metronome_stage_')
    )
    staging_qualified = staging_create.removeprefix("CREATE TABLE ").split(" (", 1)[0]
    assert not any(
        item.startswith('DROP TABLE "Reporting Area"."Import First and Second Activation"')
        for item in executed
    )
    assert 'TRUNCATE TABLE "Reporting Area"."Import First and Second Activation"' in executed
    assert any(
        item.startswith(
            'INSERT INTO "Reporting Area"."Import First and Second Activation" '
            '("sell_out_week", "active") SELECT "sell_out_week", "active" FROM '
        )
        for item in executed
    )
    assert f"DROP TABLE {staging_qualified}" in executed
    assert copied[0][0].startswith(f"COPY {staging_qualified} ")
    assert copied[0][1] == "Sell-out Week,Active\n202627,116\n"
    assert [event["stage"] for event in events] == [
        "sql_artifact_validation", "sql_artifact_validation", "sql_connecting", "sql_connected",
        "sql_target_validation", "sql_target_validation", "sql_staging", "sql_staging",
        "sql_copy", "sql_copy", "sql_replace", "sql_replace", "sql_commit", "sql_commit",
    ]
    assert result["schema_replaced"] is False
    assert result["snapshot_refreshed"] is True
    assert result["target_created"] is False
    assert result["columns_added"] == []
    assert result["column_type"] == "TEXT"


def test_sql_managed_snapshot_adds_new_columns_without_dropping_existing_table(tmp_path, monkeypatch):
    path = tmp_path / "replacement.csv"
    path.write_text("New Column,Another\nvalue,2\n", encoding="utf-8")
    executed = []
    transaction = SimpleNamespace(commit=lambda: executed.append("commit"), rollback=lambda: None)

    class Result:
        def fetchall(self):
            return [("old_column", "YES", None, "NO", "NEVER")]

    class Cursor:
        def copy_expert(self, statement, stream):
            executed.append(statement)
            stream.read()

        def close(self):
            return None

    class Connection:
        connection = SimpleNamespace(cursor=lambda: Cursor())

        def begin(self):
            return transaction

        def execute(self, statement, *_args, **_kwargs):
            executed.append(str(statement))
            return None if str(statement).startswith("SET LOCAL") else Result()

        def close(self):
            return None

    monkeypatch.setattr(
        flow_sql, "_engine",
        lambda _database: SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None),
    )

    result = flow_sql.load_artifacts(
        [{"file_path": str(path)}],
        {"database": "db", "schema": "reporting", "table": "target", "mode": "replace"},
    )

    assert result["rows_written"] == 1
    assert 'ALTER TABLE "reporting"."target" ADD COLUMN "new_column" TEXT' in executed
    assert 'ALTER TABLE "reporting"."target" ADD COLUMN "another" TEXT' in executed
    assert 'TRUNCATE TABLE "reporting"."target"' in executed
    assert not any(item.startswith('DROP TABLE "reporting"."target"') for item in executed)
    assert any(
        item.startswith(
            'INSERT INTO "reporting"."target" ("new_column", "another") '
            'SELECT "new_column", "another" FROM "reporting"."_metronome_stage_'
        )
        for item in executed
    )
    assert result["columns_added"] == ["new_column", "another"]
    assert result["target_created"] is False
    assert executed[-1] == "commit"


def test_sql_managed_snapshot_copy_failure_leaves_existing_target_untouched(tmp_path, monkeypatch):
    path = tmp_path / "replacement.csv"
    path.write_text("new_column\nvalue\n", encoding="utf-8")
    executed = []
    transaction = SimpleNamespace(
        commit=lambda: executed.append("commit"),
        rollback=lambda: executed.append("rollback"),
    )

    class Result:
        def fetchall(self):
            return [("old_column", "YES", None, "NO", "NEVER")]

    class Cursor:
        def copy_expert(self, _statement, _stream):
            raise RuntimeError("simulated COPY failure")

        def close(self):
            return None

    class Connection:
        connection = SimpleNamespace(cursor=lambda: Cursor())

        def begin(self):
            return transaction

        def execute(self, statement, *_args, **_kwargs):
            executed.append(str(statement))
            return None if str(statement).startswith("SET LOCAL") else Result()

        def close(self):
            return None

    monkeypatch.setattr(
        flow_sql, "_engine",
        lambda _database: SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None),
    )
    events = []

    with pytest.raises(flow_sql.SqlHandoffError, match="PostgreSQL confirmed rollback"):
        flow_sql.load_artifacts(
            [{"file_path": str(path)}],
            {"database": "db", "schema": "reporting", "table": "target", "mode": "replace"},
            progress=events.append,
        )

    assert any(item.startswith('CREATE TABLE "reporting"."_metronome_stage_') for item in executed)
    assert not any(item.startswith('TRUNCATE TABLE "reporting"."target"') for item in executed)
    assert not any(item.startswith('ALTER TABLE "reporting"."target"') for item in executed)
    assert not any(item.startswith('DROP TABLE "reporting"."target"') for item in executed)
    assert "rollback" in executed
    assert "commit" not in executed
    assert events[-1]["stage"] == "sql_failed"
    assert events[-1]["outcome"] == "PostgreSQL confirmed rollback. No SQL changes were committed."


def test_sql_managed_snapshot_creates_missing_target_from_staging(tmp_path, monkeypatch):
    path = tmp_path / "first_snapshot.csv"
    path.write_text("New Column,Another\nvalue,2\n", encoding="utf-8")
    executed = []
    copied = []
    transaction = SimpleNamespace(
        commit=lambda: executed.append("commit"),
        rollback=lambda: executed.append("rollback"),
    )

    class Result:
        def fetchall(self):
            return []

    class Cursor:
        def copy_expert(self, statement, stream):
            copied.append((statement, stream.read()))

        def close(self):
            return None

    class Connection:
        connection = SimpleNamespace(cursor=lambda: Cursor())

        def begin(self):
            return transaction

        def execute(self, statement, *_args, **_kwargs):
            executed.append(str(statement))
            return None if str(statement).startswith("SET LOCAL") else Result()

        def close(self):
            return None

    monkeypatch.setattr(
        flow_sql, "_engine",
        lambda _database: SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None),
    )

    result = flow_sql.load_artifacts(
        [{"file_path": str(path)}],
        {"database": "db", "schema": "reporting", "table": "new target", "mode": "replace"},
    )

    staging_create = next(
        item for item in executed
        if item.startswith('CREATE TABLE "reporting"."_metronome_stage_')
    )
    staging_qualified = staging_create.removeprefix("CREATE TABLE ").split(" (", 1)[0]
    assert copied[0][0].startswith(f"COPY {staging_qualified} ")
    assert f'ALTER TABLE {staging_qualified} RENAME TO "new target"' in executed
    assert not any(item.startswith('TRUNCATE TABLE "reporting"."new target"') for item in executed)
    assert result["target_created"] is True
    assert result["snapshot_refreshed"] is True
    assert result["columns_added"] == ["new_column", "another"]
    assert executed[-1] == "commit"


def test_sql_managed_snapshot_final_insert_failure_rolls_back_target_refresh(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.csv"
    path.write_text("value\nnew\n", encoding="utf-8")
    executed = []
    transaction = SimpleNamespace(
        commit=lambda: executed.append("commit"),
        rollback=lambda: executed.append("rollback"),
    )

    class Result:
        def fetchall(self):
            return [("value", "YES", None, "NO", "NEVER")]

    class Cursor:
        def copy_expert(self, _statement, stream):
            stream.read()

        def close(self):
            return None

    class Connection:
        connection = SimpleNamespace(cursor=lambda: Cursor())

        def begin(self):
            return transaction

        def execute(self, statement, *_args, **_kwargs):
            rendered = str(statement)
            executed.append(rendered)
            if rendered.startswith('INSERT INTO "reporting"."target"'):
                raise RuntimeError("simulated final promotion failure")
            return None if rendered.startswith("SET LOCAL") else Result()

        def close(self):
            return None

    monkeypatch.setattr(
        flow_sql, "_engine",
        lambda _database: SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None),
    )

    with pytest.raises(flow_sql.SqlHandoffError, match="PostgreSQL confirmed rollback"):
        flow_sql.load_artifacts(
            [{"file_path": str(path)}],
            {"database": "db", "schema": "reporting", "table": "target", "mode": "replace"},
        )

    assert 'TRUNCATE TABLE "reporting"."target"' in executed
    assert "rollback" in executed
    assert "commit" not in executed


def test_sql_copy_error_is_clean_and_rollback_is_logged(tmp_path, monkeypatch):
    path = tmp_path / "normalized.csv"
    path.write_text("value\nnot-an-integer\n", encoding="utf-8")
    rolled_back = []

    class DatabaseFailure(Exception):
        pgcode = "22P02"
        diag = SimpleNamespace(message_primary="invalid input syntax for type integer")

    class Result:
        def fetchall(self):
            return [("value", "NO", None, "NO", "NEVER")]

    class Cursor:
        def copy_expert(self, _statement, _stream):
            raise DatabaseFailure("noisy driver detail")

        def close(self):
            return None

    transaction = SimpleNamespace(commit=lambda: None, rollback=lambda: rolled_back.append(True))

    class Connection:
        connection = SimpleNamespace(cursor=lambda: Cursor())

        def begin(self):
            return transaction

        def execute(self, statement, *_args, **_kwargs):
            return None if str(statement).startswith("SET LOCAL") else Result()

        def close(self):
            return None

    monkeypatch.setattr(
        flow_sql, "_engine",
        lambda _database: SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None),
    )
    events = []

    with pytest.raises(flow_sql.SqlHandoffError, match="SQLSTATE 22P02") as error:
        flow_sql.load_artifacts(
            [{"file_path": str(path)}],
            {"database": "db", "schema": "reporting", "table": "target", "mode": "append"},
            progress=events.append,
        )

    assert "invalid input syntax for type integer" in str(error.value)
    assert "PostgreSQL confirmed rollback" in str(error.value)
    assert rolled_back == [True]
    assert events[-1]["stage"] == "sql_failed"
    assert events[-1]["sql_stage"] == "copy"
