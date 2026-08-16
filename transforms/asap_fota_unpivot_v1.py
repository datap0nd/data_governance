"""Normalize one weekly Regional FOTA export into a stable long-form CSV."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, timedelta
from pathlib import Path


WEEK_COLUMN = re.compile(r"^20\d{4}$")
FILENAME_WEEK = re.compile(r"(?<!\d)(20\d{2})-W(\d{2})(?!\d)")
METRIC_COLUMN = "Metric"
METRIC_COLUMN_ALIASES = frozenset({METRIC_COLUMN, "Metrics"})
LINEAGE_COLUMN = "Metronome Export View"
CONTRACTED_DIMENSIONS = [
    "Sell-out Region",
    "Sell-out Subsidiary",
    "Sell-out Country",
    "Country Code",
    "Operator",
    "Province",
    "Latitude",
    "Longitude",
    "Category",
    "Biz Sub",
    "Series",
    "MKT Name",
    "Item",
]


def _week_dates(compact_week: str) -> tuple[str, str, str]:
    year = int(compact_week[:4])
    week = int(compact_week[4:])
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError(f"Export contains a nonexistent week column: {compact_week}") from exc
    sunday = monday - timedelta(days=1)
    saturday = sunday + timedelta(days=6)
    return f"{year:04d}-W{week:02d}", sunday.isoformat(), saturday.isoformat()


def _column_plan(header: list[str]) -> tuple[list[str], list[int], dict[str, list[int]]]:
    """Plan a safe collapse of repeated export columns.

    ASAP can repeat a selected dimension in the flat Excel export. Keeping both
    copies would create an invalid SQL schema, while blindly dropping one could
    hide a real data conflict. The safe case is an exact duplicate column: the
    header matches and every row carries the same value in both positions.
    """
    positions: dict[str, list[int]] = {}
    for index, name in enumerate(header):
        positions.setdefault(name, []).append(index)
    duplicate_groups = {name: indexes for name, indexes in positions.items() if len(indexes) > 1}
    keep = [indexes[0] for indexes in positions.values()]
    return [header[index] for index in keep], keep, duplicate_groups


def transform(source: Path, target: Path) -> int:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            raw_header = [str(value).strip() for value in next(reader)]
        except StopIteration as exc:
            raise ValueError("The normalized export is empty.") from exc
        header, keep, duplicate_groups = _column_plan(raw_header)
        week_columns = [name for name in header if WEEK_COLUMN.fullmatch(name)]
        metric_columns = [name for name in header if name in METRIC_COLUMN_ALIASES]
        filename_match = FILENAME_WEEK.search(source.name)
        filename_week = "".join(filename_match.groups()) if filename_match else None
        if len(week_columns) == 1:
            week_column = week_columns[0]
            if filename_week and filename_week != week_column:
                raise ValueError(
                    f"Filename week {filename_week} does not match export column {week_column}."
                )
            compact_week = week_column
            value_column = week_column
        elif not week_columns and len(metric_columns) == 1 and filename_week:
            # The live flat Excel export names its sole weekly value field
            # ``Metric``. Each artifact is already scoped to exactly one week,
            # so its validated filename is the canonical week key.
            compact_week = filename_week
            value_column = metric_columns[0]
        else:
            preview = [name[:80] for name in header[:25]]
            raise ValueError(
                "Expected one YYYYWW value column or one Metric column with a week in the filename; "
                f"found week columns {week_columns or 'none'} and metric columns "
                f"{metric_columns or 'none'}. Detected header: {preview!r}."
            )
        week, week_start, week_end = _week_dates(compact_week)
        value_index = header.index(value_column)
        allowed = {*CONTRACTED_DIMENSIONS, value_column, LINEAGE_COLUMN}
        unexpected = [name for name in header if name not in allowed]
        if unexpected:
            raise ValueError(f"Unexpected FOTA column(s): {unexpected}.")
        missing = [
            name for name in CONTRACTED_DIMENSIONS
            if name != "Category" and name not in header
        ]
        if missing:
            raise ValueError(f"Missing contracted FOTA dimension(s): {missing}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        if temporary.exists():
            raise ValueError(f"Temporary output already exists: {temporary}")
        row_count = 0
        try:
            with temporary.open("x", encoding="utf-8-sig", newline="") as output_handle:
                writer = csv.writer(output_handle, lineterminator="\n")
                writer.writerow([
                    *CONTRACTED_DIMENSIONS,
                    "Week", "Week Start Date", "Week End Date", "FOTA Value",
                ])
                for row_number, raw_row in enumerate(reader, start=2):
                    row = list(raw_row[: len(raw_header)]) + [""] * max(0, len(raw_header) - len(raw_row))
                    if not any(str(value).strip() for value in row):
                        continue
                    if len(raw_row) > len(raw_header) and any(
                        str(value).strip() for value in raw_row[len(raw_header):]
                    ):
                        raise ValueError(f"Row {row_number} has more values than the header.")
                    for name, indexes in duplicate_groups.items():
                        baseline = str(row[indexes[0]]).strip()
                        if any(str(row[index]).strip() != baseline for index in indexes[1:]):
                            label = name or "<blank>"
                            raise ValueError(
                                f"Duplicate column {label!r} contains conflicting values at row {row_number}."
                            )
                    selected = [row[index] for index in keep]
                    dimension_values = []
                    for name in CONTRACTED_DIMENSIONS:
                        if name == "Category" and name not in header:
                            dimension_values.append("Weekly")
                        else:
                            dimension_values.append(selected[header.index(name)])
                    writer.writerow([
                        *dimension_values, week, week_start, week_end, selected[value_index],
                    ])
                    row_count += 1
            if row_count == 0:
                raise ValueError("The normalized export contains no data rows.")
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
    return row_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    target = Path(args.output)
    try:
        row_count = transform(source, target)
    except Exception as exc:
        print(f"FOTA transformation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Transformed {row_count} row(s) from {source.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
