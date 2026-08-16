"""Normalize Regional FOTA exports into a geocoded, long-form CSV."""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path


EXPLICIT_WEEK_COLUMN = re.compile(
    r"^(20\d{2})(?:\s*[-_/]?\s*W)?\s*[-_/]?\s*(\d{1,2})$",
    re.IGNORECASE,
)
WEEK_ONLY_COLUMN = re.compile(
    r"^(?:W|Week)\s*[-_:]?\s*(\d{1,2})(?:\s*[-_/ ]\s*(20\d{2}))?$",
    re.IGNORECASE,
)
FILENAME_WEEK = re.compile(r"(?<!\d)(20\d{2})-W(\d{2})(?!\d)", re.IGNORECASE)
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
GEO_COLUMNS = ["Country", "City", "District", "State"]
OUTPUT_DIMENSIONS = [
    *CONTRACTED_DIMENSIONS[:8],
    *GEO_COLUMNS,
    *CONTRACTED_DIMENSIONS[8:],
]
EMPTY_GEO = {name: "" for name in GEO_COLUMNS}


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


def _compact_week(year: str | int, week: str | int) -> str:
    compact = f"{int(year):04d}{int(week):02d}"
    _week_dates(compact)
    return compact


def _filename_weeks(filename: str) -> list[str]:
    weeks: list[str] = []
    for match in FILENAME_WEEK.finditer(filename):
        compact = _compact_week(*match.groups())
        if compact not in weeks:
            weeks.append(compact)
    return weeks


def _week_value_columns(header: list[str], filename_weeks: list[str]) -> list[tuple[str, str]]:
    """Return ``(column name, YYYYWW)`` pairs in source-column order."""
    filename_years = {compact[:4] for compact in filename_weeks}
    filename_by_number = {int(compact[4:]): compact for compact in filename_weeks}
    default_year = next(iter(filename_years)) if len(filename_years) == 1 else None
    value_columns: list[tuple[str, str]] = []
    for name in header:
        explicit = EXPLICIT_WEEK_COLUMN.fullmatch(name)
        if explicit:
            compact = _compact_week(*explicit.groups())
        else:
            week_only = WEEK_ONLY_COLUMN.fullmatch(name)
            if not week_only:
                continue
            week_number, explicit_year = week_only.groups()
            known = filename_by_number.get(int(week_number))
            year = explicit_year or (known[:4] if known else default_year)
            if not year:
                continue
            compact = _compact_week(year, week_number)
        if any(existing_week == compact for _, existing_week in value_columns):
            raise ValueError(f"Multiple export columns resolve to week {compact}.")
        value_columns.append((name, compact))
    return value_columns


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


def _selected_rows(
    source: Path,
    raw_header: list[str],
    keep: list[int],
    duplicate_groups: dict[str, list[int]],
):
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            observed_header = [str(value).strip() for value in next(reader)]
        except StopIteration as exc:
            raise ValueError("The normalized export is empty.") from exc
        if observed_header != raw_header:
            raise ValueError("The normalized export header changed during transformation.")
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
            yield row_number, [row[index] for index in keep]


def _coordinate(selected: list[str], header: list[str]) -> tuple[float, float] | None:
    values = [selected[header.index(name)] for name in ("Latitude", "Longitude")]
    parsed: list[float] = []
    for value in values:
        text = str(value).strip()
        if text.casefold() in {"", "empty", "nan", "none", "null", "-"}:
            return None
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        parsed.append(number)
    latitude, longitude = parsed
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def _has_week_value(
    selected: list[str], header: list[str], value_columns: list[tuple[str, str]],
) -> bool:
    return any(str(selected[header.index(name)]).strip() for name, _compact in value_columns)


def _validated_fota_value(value: object, *, row_number: int, week: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = float(text.replace(",", ""))
    except ValueError as exc:
        raise ValueError(
            f"FOTA value must be numeric at source row {row_number}, week {week}: {text!r}."
        ) from exc
    if not math.isfinite(number):
        raise ValueError(
            f"FOTA value must be finite at source row {row_number}, week {week}: {text!r}."
        )
    return text


def _reverse_geocode_coordinates(
    coordinates: list[tuple[float, float]],
) -> dict[tuple[float, float], dict[str, str]]:
    if not coordinates:
        return {}
    try:
        import pycountry
        import reverse_geocoder as rg
    except ImportError as exc:
        raise RuntimeError(
            "Geolocation dependencies are unavailable. Install reverse_geocoder and pycountry."
        ) from exc
    locations = rg.search(coordinates, mode=1, verbose=False)
    if len(locations) != len(coordinates):
        raise RuntimeError(
            f"Reverse geocoder returned {len(locations)} result(s) for {len(coordinates)} coordinate(s)."
        )
    enriched: dict[tuple[float, float], dict[str, str]] = {}
    for coordinate, location in zip(coordinates, locations):
        country_code = str(location.get("cc") or "").upper()
        country = pycountry.countries.get(alpha_2=country_code) if country_code else None
        enriched[coordinate] = {
            "Country": country.name if country else "",
            "City": str(location.get("name") or ""),
            # Preserve the field mapping used by the authorized legacy script.
            "District": str(location.get("admin1") or ""),
            "State": str(location.get("admin2") or ""),
        }
    return enriched


def transform(source: Path, target: Path) -> int:
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            raw_header = [str(value).strip() for value in next(reader)]
        except StopIteration as exc:
            raise ValueError("The normalized export is empty.") from exc
        header, keep, duplicate_groups = _column_plan(raw_header)
        filename_weeks = _filename_weeks(source.name)
        week_columns = _week_value_columns(header, filename_weeks)
        metric_columns = [name for name in header if name in METRIC_COLUMN_ALIASES]
        if week_columns:
            compact_weeks = [compact for _, compact in week_columns]
            if filename_weeks and filename_weeks != compact_weeks:
                raise ValueError(
                    f"Filename week selection {filename_weeks} does not match export columns "
                    f"{compact_weeks}."
                )
            value_columns = week_columns
        elif len(metric_columns) == 1 and len(filename_weeks) == 1:
            # The live flat Excel export names its sole weekly value field
            # ``Metric``. Each artifact is already scoped to exactly one week,
            # so its validated filename is the canonical week key.
            value_columns = [(metric_columns[0], filename_weeks[0])]
        else:
            preview = [name[:80] for name in header[:25]]
            raise ValueError(
                "Expected one or more week value columns, or one Metric column with exactly one "
                "week in the filename; "
                f"found week columns {[name for name, _ in week_columns] or 'none'}, filename weeks "
                f"{filename_weeks or 'none'}, and metric columns {metric_columns or 'none'}. "
                f"Detected header: {preview!r}."
            )
        allowed = {*CONTRACTED_DIMENSIONS, *(name for name, _ in value_columns), LINEAGE_COLUMN}
        unexpected = [name for name in header if name not in allowed]
        if unexpected:
            raise ValueError(f"Unexpected FOTA column(s): {unexpected}.")
        missing = [
            name for name in CONTRACTED_DIMENSIONS
            if name != "Category" and name not in header
        ]
        if missing:
            raise ValueError(f"Missing contracted FOTA dimension(s): {missing}.")

        coordinates: dict[tuple[float, float], None] = {}
        source_row_count = 0
        for _row_number, selected in _selected_rows(source, raw_header, keep, duplicate_groups):
            if not _has_week_value(selected, header, value_columns):
                continue
            source_row_count += 1
            coordinate = _coordinate(selected, header)
            if coordinate is not None:
                coordinates.setdefault(coordinate, None)
        if source_row_count == 0:
            raise ValueError("The normalized export contains no data rows.")
        geocoded = _reverse_geocode_coordinates(list(coordinates))

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        if temporary.exists():
            raise ValueError(f"Temporary output already exists: {temporary}")
        row_count = 0
        try:
            with temporary.open("x", encoding="utf-8-sig", newline="") as output_handle:
                writer = csv.writer(output_handle, lineterminator="\n")
                writer.writerow([
                    *OUTPUT_DIMENSIONS,
                    "Week", "Week Start Date", "Week End Date", "FOTA Value",
                ])
                for row_number, selected in _selected_rows(
                    source, raw_header, keep, duplicate_groups,
                ):
                    dimension_values: dict[str, str] = {}
                    for name in CONTRACTED_DIMENSIONS:
                        if name == "Category" and name not in header:
                            dimension_values[name] = "Weekly"
                        else:
                            dimension_values[name] = selected[header.index(name)]
                    geo_values = geocoded.get(_coordinate(selected, header), EMPTY_GEO)
                    output_dimensions = [
                        dimension_values[name] if name in dimension_values else geo_values[name]
                        for name in OUTPUT_DIMENSIONS
                    ]
                    for value_column, compact_week in value_columns:
                        week, week_start, week_end = _week_dates(compact_week)
                        value = _validated_fota_value(
                            selected[header.index(value_column)],
                            row_number=row_number,
                            week=week,
                        )
                        if not value:
                            continue
                        writer.writerow([
                            *output_dimensions,
                            week,
                            week_start,
                            week_end,
                            value,
                        ])
                        row_count += 1
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
    return row_count


def _output_week_counts(target: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with target.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "Week" not in reader.fieldnames:
            raise ValueError("Transformed output does not contain the contracted Week column.")
        for row in reader:
            week = str(row.get("Week") or "").strip()
            counts[week] = counts.get(week, 0) + 1
    return counts


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
    week_counts = _output_week_counts(target)
    summary = ", ".join(f"{week}={count}" for week, count in week_counts.items())
    print(f"Transformed {row_count} row(s) from {source.name}. Week rows: {summary}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
