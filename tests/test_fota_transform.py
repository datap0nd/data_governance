import csv
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "transforms" / "asap_fota_unpivot_v1.py"
SPEC = importlib.util.spec_from_file_location("asap_fota_unpivot_v1", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)


def test_fota_transform_unpivots_one_week_and_adds_live_calendar_dates(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    _write_csv(source, [
        ["Sell-out Region", "Sell-out Subsidiary", "Weekly", "202520"],
        ["Middle East", "SEEG", "FOTA", "123"],
    ])

    assert MODULE.transform(source, target) == 1

    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows == [
        [
            "Sell-out Region", "Sell-out Subsidiary", "Weekly", "Week",
            "Week Start Date", "Week End Date", "FOTA Value",
        ],
        ["Middle East", "SEEG", "FOTA", "2025-W20", "2025-05-11", "2025-05-17", "123"],
    ]


def test_fota_transform_rejects_week_mismatch_without_creating_output(tmp_path):
    source = tmp_path / "ASAP_Fota_2025-W20_normalized.csv"
    target = tmp_path / "result.csv"
    _write_csv(source, [
        ["Sell-out Region", "202521"],
        ["Middle East", "123"],
    ])

    with pytest.raises(ValueError, match="does not match"):
        MODULE.transform(source, target)

    assert not target.exists()
