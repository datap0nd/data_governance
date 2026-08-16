import csv
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "transforms" / "asap_fota_unpivot_v1.py"
SPEC = importlib.util.spec_from_file_location("asap_fota_unpivot_v1", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

DIMENSION_VALUES = {
    "Sell-out Region": "Middle East",
    "Sell-out Subsidiary": "SEEG",
    "Sell-out Country": "UAE",
    "Country Code": "AE",
    "Operator": "Operator A",
    "Province": "Dubai",
    "Latitude": "25.2",
    "Longitude": "55.3",
    "Category": "Weekly",
    "Biz Sub": "Mobile",
    "Series": "Galaxy",
    "MKT Name": "Model A",
    "Item": "SKU 1",
}


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _values_for(dimensions):
    return [DIMENSION_VALUES[name] for name in dimensions]


def _expected_rows(value="123"):
    return [
        [
            *MODULE.CONTRACTED_DIMENSIONS,
            "Week", "Week Start Date", "Week End Date", "FOTA Value",
        ],
        [
            *_values_for(MODULE.CONTRACTED_DIMENSIONS),
            "2025-W20", "2025-05-11", "2025-05-17", value,
        ],
    ]


def test_fota_transform_unpivots_compact_week_column_and_adds_calendar_dates(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, "202520", MODULE.LINEAGE_COLUMN],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "123", "Export Wizard (Sell-out Sub)"],
    ])

    assert MODULE.transform(source, target) == 1

    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        assert list(csv.reader(handle)) == _expected_rows()


def test_fota_transform_uses_live_metric_column_filename_week_and_filtered_category(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    exported_dimensions = [
        name for name in MODULE.CONTRACTED_DIMENSIONS if name != "Category"
    ]
    _write_csv(source, [
        [*exported_dimensions, "Metrics", MODULE.LINEAGE_COLUMN],
        [*_values_for(exported_dimensions), "456", "Export Wizard (Sell-out Sub)"],
    ])

    assert MODULE.transform(source, target) == 1

    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        assert list(csv.reader(handle)) == _expected_rows("456")


def test_fota_transform_rejects_week_mismatch_without_creating_output(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, "202521"],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "123"],
    ])

    with pytest.raises(ValueError, match="does not match"):
        MODULE.transform(source, target)

    assert not target.exists()


def test_fota_transform_collapses_identical_duplicate_dimensions(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    remaining_dimensions = MODULE.CONTRACTED_DIMENSIONS[1:]
    _write_csv(source, [
        ["Sell-out Region", "Sell-out Region", *remaining_dimensions, "202520"],
        ["Middle East", "Middle East", *_values_for(remaining_dimensions), "123"],
    ])

    assert MODULE.transform(source, target) == 1

    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        assert list(csv.reader(handle)) == _expected_rows()


def test_fota_transform_rejects_conflicting_duplicate_dimensions(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    remaining_dimensions = MODULE.CONTRACTED_DIMENSIONS[1:]
    _write_csv(source, [
        ["Sell-out Region", "Sell-out Region", *remaining_dimensions, "202520"],
        ["Middle East", "Europe", *_values_for(remaining_dimensions), "123"],
    ])

    with pytest.raises(ValueError, match="conflicting values at row 2"):
        MODULE.transform(source, target)

    assert not target.exists()


def test_fota_transform_rejects_missing_contracted_dimension(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    exported_dimensions = [
        name for name in MODULE.CONTRACTED_DIMENSIONS
        if name not in {"Category", "Item"}
    ]
    _write_csv(source, [
        [*exported_dimensions, MODULE.METRIC_COLUMN],
        [*_values_for(exported_dimensions), "123"],
    ])

    with pytest.raises(ValueError, match="Missing contracted FOTA dimension"):
        MODULE.transform(source, target)

    assert not target.exists()
