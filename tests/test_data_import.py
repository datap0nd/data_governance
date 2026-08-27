"""Normalization rules applied before Import Data writes to SQL.

Table names: lowercased, whitespace converted to underscores, anything else
that is not a plain identifier rejected. File column headers: lowercased with
spaces (and other punctuation) converted to underscores.
"""

import pytest
from fastapi import HTTPException

from app.routers import data_import


class TestCleanTableName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("orders", "orders"),
            ("Orders", "orders"),
            ("MY TABLE", "my_table"),
            ("My Table", "my_table"),
            ("my  spaced   table", "my_spaced_table"),
            ("  padded_name  ", "padded_name"),
            ("Sales\tReport 2024", "sales_report_2024"),
            ("_staging", "_staging"),
        ],
    )
    def test_lowercases_and_converts_whitespace_to_underscores(self, raw, expected):
        assert data_import._clean_table_name(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "1starts_with_digit",
            "has-dash",
            "has.dot",
            "has;semicolon",
            'has"quote',
            "",
            "   ",
        ],
    )
    def test_rejects_names_that_are_not_plain_identifiers(self, raw):
        with pytest.raises(HTTPException) as excinfo:
            data_import._clean_table_name(raw)
        assert excinfo.value.status_code == 400


class TestReadDataframeColumnHeaders:
    def _read(self, tmp_path, header_line: str):
        pytest.importorskip("pandas")
        path = tmp_path / "upload.csv"
        path.write_text(f"{header_line}\n1,2,3\n", encoding="utf-8")
        return data_import._read_dataframe(path, "upload.csv")

    def test_headers_are_lowercased_with_spaces_as_underscores(self, tmp_path):
        df = self._read(tmp_path, "Order ID,Customer Name,TOTAL AMOUNT")
        assert list(df.columns) == ["order_id", "customer_name", "total_amount"]

    def test_punctuation_collapses_to_single_underscore(self, tmp_path):
        df = self._read(tmp_path, "Unit Price ($),Qty.,  Ship-To City ")
        assert list(df.columns) == ["unit_price", "qty", "ship_to_city"]

    def test_duplicate_headers_after_normalization_get_suffixes(self, tmp_path):
        df = self._read(tmp_path, "Name,name,NAME")
        assert list(df.columns) == ["name", "name_2", "name_3"]
