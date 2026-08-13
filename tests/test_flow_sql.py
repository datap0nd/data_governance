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
