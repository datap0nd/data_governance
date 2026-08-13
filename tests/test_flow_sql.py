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


def test_sql_preflight_reports_missing_and_unexpected_columns(tmp_path, monkeypatch):
    path = tmp_path / "wrong.csv"
    pd.DataFrame({"a": [1], "extra": [2]}).to_csv(path, index=False)

    class Result:
        def fetchall(self):
            return [("a", "NO", None, "NO", "NEVER"), ("required_b", "NO", None, "NO", "NEVER")]

    class Connection:
        def execute(self, *_args, **_kwargs):
            return Result()

    class Begin:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return False

    engine = SimpleNamespace(begin=lambda: Begin(), dispose=lambda: None)
    monkeypatch.setattr(flow_sql, "_engine", lambda _database: engine)
    with pytest.raises(RuntimeError, match="missing: required_b; unexpected: extra"):
        flow_sql.load_artifacts(
            [{"file_path": str(path)}],
            {"database": "db", "schema": "reporting", "table": "target", "mode": "replace"},
        )


def test_sql_copy_streams_csv_through_postgres_copy():
    copied = {}

    class Cursor:
        def copy_expert(self, statement, stream):
            copied["statement"] = statement
            copied["content"] = stream.read()

        def close(self):
            copied["closed"] = True

    raw = SimpleNamespace(cursor=lambda: Cursor())
    connection = SimpleNamespace(connection=raw)
    frame = pd.DataFrame({"sell_out_week": [202627], "active": [116]})

    flow_sql._copy_frame(connection, frame, '"bi_reporting"."this_is_test"')

    assert copied["statement"] == (
        'COPY "bi_reporting"."this_is_test" ("sell_out_week", "active") '
        "FROM STDIN WITH (FORMAT CSV, HEADER TRUE, ENCODING 'UTF8')"
    )
    assert copied["content"] == "sell_out_week,active\n202627,116\n"
    assert copied["closed"] is True
