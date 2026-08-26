import base64
from pathlib import Path

import pytest

from app import flow_worker


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _excel_fixture(name: str) -> bytes:
    return base64.b64decode(
        FIXTURE_DIR.joinpath(f"minimal.{name}.b64").read_text(encoding="ascii")
    )


@pytest.mark.parametrize("signature", [b"\x09\x00", b"\x09\x02", b"\x09\x04", b"\x09\x08"])
def test_standalone_legacy_biff_streams_are_detected_as_xls(tmp_path, signature):
    source = tmp_path / "legacy.xls"
    source.write_bytes(signature + b"\x00" * 64)

    assert flow_worker._detect_download_format(source) == "xls"


@pytest.mark.parametrize(
    ("suffix", "fixture", "detected", "source_encoding", "columns"),
    [
        (".xls", "xls", "xls", "xls", ["Code", "Units"]),
        (".xlt", "xls", "xlt", "xls", ["Code", "Units"]),
        # Derived from pandas' BSD-licensed test3.xlsb interoperability fixture.
        (".xlsb", "xlsb", "xlsb", "xlsb", ["Test"]),
    ],
)
def test_legacy_and_binary_excel_workbooks_are_preserved_and_normalized(
    tmp_path, suffix, fixture, detected, source_encoding, columns,
):
    source = tmp_path / f"outlook-download{suffix}"
    source.write_bytes(_excel_fixture(fixture))

    metadata = flow_worker._store_completed_download(
        source, tmp_path / f"report{suffix}", file_format="auto",
        csv_preamble="none", strict_headers=True, xlsx_header_mode="first_row",
    )

    assert metadata["detected_format"] == detected
    assert metadata["source_encoding"] == source_encoding
    assert metadata["columns"] == columns
    assert Path(metadata["original_file_path"]).suffix == suffix
    assert Path(metadata["original_file_path"]).read_bytes() == source.read_bytes()
    assert Path(metadata["file_path"]).suffix == ".csv"
    assert metadata["row_count"] == 1


@pytest.mark.parametrize("suffix", [".xlsx", ".xlsm", ".xltx", ".xltm"])
def test_modern_excel_extensions_keep_the_original_name_and_normalize(tmp_path, suffix):
    from openpyxl import Workbook

    source = tmp_path / f"outlook-download{suffix}"
    workbook = Workbook()
    workbook.active.append(["Code", "Units"])
    workbook.active.append(["A", 7])
    workbook.save(source)

    metadata = flow_worker._store_completed_download(
        source, tmp_path / f"report{suffix}", file_format="auto",
        csv_preamble="none", strict_headers=True, xlsx_header_mode="first_row",
    )

    assert metadata["detected_format"] == suffix.lstrip(".")
    assert metadata["source_encoding"] == "xlsx"
    assert metadata["columns"] == ["Code", "Units"]
    assert Path(metadata["original_file_path"]).suffix == suffix
    assert metadata["row_count"] == 1
