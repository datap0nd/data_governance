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
    "Category": "Domestic",
    "Biz Sub": "Mobile",
    "Series": "Galaxy",
    "MKT Name": "Model A",
    "Item": "SKU 1",
}
GEO_VALUES = {
    "Country": "United Arab Emirates",
    "City": "Dubai",
    "District": "Dubai",
    "State": "",
}


@pytest.fixture(autouse=True)
def _stub_reverse_geocoder(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_reverse_geocode_coordinates",
        lambda coordinates: {coordinate: dict(GEO_VALUES) for coordinate in coordinates},
    )


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _values_for(dimensions):
    return [DIMENSION_VALUES[name] for name in dimensions]


def _expected_rows(value="123"):
    dimensions = {
        **DIMENSION_VALUES,
        **GEO_VALUES,
    }
    return [
        [
            *MODULE.OUTPUT_DIMENSIONS,
            "Week", "Week Start Date", "Week End Date", "Sell-out Qty",
        ],
        [
            *[dimensions[name] for name in MODULE.OUTPUT_DIMENSIONS],
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

    assert MODULE.transform(source, target, ["2025-W20"]) == 1

    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        assert list(csv.reader(handle)) == _expected_rows()


def test_fota_transform_uses_requested_week_for_live_metric_column_and_preserves_category(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, "Metrics", MODULE.LINEAGE_COLUMN],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "456", "Export Wizard (Sell-out Sub)"],
    ])

    assert MODULE.transform(source, target, ["2025-W20"]) == 1

    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        assert list(csv.reader(handle)) == _expected_rows("456")


def test_fota_transform_unpivots_two_human_week_columns_and_geocodes_once(tmp_path, monkeypatch):
    source = tmp_path / "ASAP_Fota_2026-W30_2026-W31_normalized.csv"
    target = tmp_path / "result.csv"
    calls = []

    def fake_geocoder(coordinates):
        calls.append(coordinates)
        return {coordinate: dict(GEO_VALUES) for coordinate in coordinates}

    monkeypatch.setattr(MODULE, "_reverse_geocode_coordinates", fake_geocoder)
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, "Week 30", "Week 31", MODULE.LINEAGE_COLUMN],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "300", "310", "Export Wizard (Sell-out Sub)"],
    ])

    assert MODULE.transform(source, target, ["2026-W30", "2026-W31"]) == 2
    assert calls == [[(25.2, 55.3)]]

    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    dimensions = {**DIMENSION_VALUES, **GEO_VALUES}
    assert rows == [
        [*MODULE.OUTPUT_DIMENSIONS, "Week", "Week Start Date", "Week End Date", "Sell-out Qty"],
        [
            *[dimensions[name] for name in MODULE.OUTPUT_DIMENSIONS],
            "2026-W30", "2026-07-19", "2026-07-25", "300",
        ],
        [
            *[dimensions[name] for name in MODULE.OUTPUT_DIMENSIONS],
            "2026-W31", "2026-07-26", "2026-08-01", "310",
        ],
    ]


def test_fota_transform_uses_complete_requested_period_list_not_filename_endpoints(tmp_path):
    source = tmp_path / "ASAP_Fota_2026-W22_2026-W26_normalized.csv"
    target = tmp_path / "result.csv"
    requested = [f"2026-W{week:02d}" for week in range(22, 27)]
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, "202622", "202623", "202624", "202625", "202626"],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "22", "23", "24", "25", "26"],
    ])

    assert MODULE.transform(source, target, requested) == 5
    assert MODULE._output_week_counts(target) == {week: 1 for week in requested}


def test_fota_transform_reads_requested_periods_from_metronome_environment(tmp_path, monkeypatch):
    source = tmp_path / "filename_without_a_week.csv"
    target = tmp_path / "result.csv"
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, MODULE.METRIC_COLUMN],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "123"],
    ])
    monkeypatch.setenv("METRONOME_FLOW_PERIODS", '["2025-W20"]')

    assert MODULE.transform(source, target) == 1
    assert MODULE._output_week_counts(target) == {"2025-W20": 1}


def test_fota_transform_skips_blank_week_cells_in_sparse_multi_week_matrix(tmp_path):
    source = tmp_path / "ASAP_Fota_2026-W30_2026-W31_normalized.csv"
    target = tmp_path / "result.csv"
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, "202630", "202631"],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "300", ""],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "", "310"],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "", ""],
    ])

    assert MODULE.transform(source, target, ["2026-W30", "2026-W31"]) == 2

    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert [row[-4] for row in rows[1:]] == ["2026-W30", "2026-W31"]
    assert [row[-1] for row in rows[1:]] == ["300", "310"]
    assert MODULE._output_week_counts(target) == {"2026-W30": 1, "2026-W31": 1}


def test_fota_transform_rejects_metric_label_as_week_value(tmp_path):
    source = tmp_path / "ASAP_Fota_2026-W30_2026-W31_normalized.csv"
    target = tmp_path / "result.csv"
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, "202630", "202631"],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "Sell-out", "310"],
    ])

    with pytest.raises(ValueError, match="Sell-out Qty must be numeric.*2026-W30.*Sell-out"):
        MODULE.transform(source, target, ["2026-W30", "2026-W31"])

    assert not target.exists()


def test_fota_transform_leaves_geography_blank_for_missing_coordinates(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    values = dict(DIMENSION_VALUES, Latitude="Empty", Longitude="")
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, "202520"],
        [*[values[name] for name in MODULE.CONTRACTED_DIMENSIONS], "123"],
    ])

    assert MODULE.transform(source, target, ["2025-W20"]) == 1

    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[1][MODULE.OUTPUT_DIMENSIONS.index("Country")] == ""
    assert rows[1][MODULE.OUTPUT_DIMENSIONS.index("City")] == ""


def test_fota_transform_rejects_week_mismatch_without_creating_output(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    _write_csv(source, [
        [*MODULE.CONTRACTED_DIMENSIONS, "202521"],
        [*_values_for(MODULE.CONTRACTED_DIMENSIONS), "123"],
    ])

    with pytest.raises(ValueError, match="do not match"):
        MODULE.transform(source, target, ["2025-W20"])

    assert not target.exists()


def test_fota_transform_collapses_identical_duplicate_dimensions(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    remaining_dimensions = MODULE.CONTRACTED_DIMENSIONS[1:]
    _write_csv(source, [
        ["Sell-out Region", "Sell-out Region", *remaining_dimensions, "202520"],
        ["Middle East", "Middle East", *_values_for(remaining_dimensions), "123"],
    ])

    assert MODULE.transform(source, target, ["2025-W20"]) == 1

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
        MODULE.transform(source, target, ["2025-W20"])

    assert not target.exists()


def test_fota_transform_rejects_missing_contracted_dimension(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    exported_dimensions = [
        name for name in MODULE.CONTRACTED_DIMENSIONS if name != "Category"
    ]
    _write_csv(source, [
        [*exported_dimensions, MODULE.METRIC_COLUMN],
        [*_values_for(exported_dimensions), "123"],
    ])

    with pytest.raises(
        ValueError, match=r"Missing contracted FOTA dimension\(s\): \['Category'\]"
    ):
        MODULE.transform(source, target, ["2025-W20"])

    assert not target.exists()
