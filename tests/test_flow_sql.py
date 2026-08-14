from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app import flow_sql, flow_worker


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
            {"database": "db", "schema": "reporting", "table": "target", "mode": "replace"},
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


def test_sql_load_reports_each_phase_and_commits(tmp_path, monkeypatch):
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
        {"database": "db", "schema": "reporting", "table": "target", "mode": "replace"},
        progress=events.append,
    )

    assert result["rows_written"] == 1
    assert result["target"] == "db.reporting.target"
    assert executed.count("commit") == 1
    assert "rollback" not in executed
    assert "SET LOCAL lock_timeout = '30s'" in executed
    assert "SET LOCAL statement_timeout = '120s'" in executed
    assert any(item.startswith("TRUNCATE TABLE") for item in executed)
    assert copied[0][1] == "Sell-out Week,Active\n202627,116\n"
    assert [event["stage"] for event in events] == [
        "sql_artifact_validation", "sql_artifact_validation", "sql_connecting", "sql_connected",
        "sql_target_validation", "sql_target_validation", "sql_truncate", "sql_truncate",
        "sql_copy", "sql_copy", "sql_commit", "sql_commit",
    ]


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
