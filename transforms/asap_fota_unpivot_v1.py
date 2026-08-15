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


def transform(source: Path, target: Path) -> int:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = [str(value).strip() for value in next(reader)]
        except StopIteration as exc:
            raise ValueError("The normalized export is empty.") from exc
        if len(header) != len(set(header)):
            raise ValueError("The normalized export contains duplicate column names.")
        week_columns = [name for name in header if WEEK_COLUMN.fullmatch(name)]
        if len(week_columns) != 1:
            raise ValueError(
                "Expected exactly one YYYYWW value column in the weekly export; "
                f"found {week_columns or 'none'}."
            )
        week_column = week_columns[0]
        filename_match = FILENAME_WEEK.search(source.name)
        if filename_match:
            filename_week = "".join(filename_match.groups())
            if filename_week != week_column:
                raise ValueError(
                    f"Filename week {filename_week} does not match export column {week_column}."
                )
        week, week_start, week_end = _week_dates(week_column)
        week_index = header.index(week_column)
        dimensions = [name for name in header if name != week_column]
        rows = []
        for row_number, raw_row in enumerate(reader, start=2):
            row = list(raw_row[: len(header)]) + [""] * max(0, len(header) - len(raw_row))
            if not any(str(value).strip() for value in row):
                continue
            if len(raw_row) > len(header) and any(str(value).strip() for value in raw_row[len(header):]):
                raise ValueError(f"Row {row_number} has more values than the header.")
            dimension_values = [row[index] for index in range(len(header)) if index != week_index]
            rows.append([*dimension_values, week, week_start, week_end, row[week_index]])
    if not rows:
        raise ValueError("The normalized export contains no data rows.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    if temporary.exists():
        raise ValueError(f"Temporary output already exists: {temporary}")
    try:
        with temporary.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow([*dimensions, "Week", "Week Start Date", "Week End Date", "FOTA Value"])
            writer.writerows(rows)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return len(rows)


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
