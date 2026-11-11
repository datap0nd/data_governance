"""F-10: CSV header detection is linear and chooses exactly what it chose before.

The two ``_old_*`` functions are private copies of the algorithms this change
replaced. Every comparison below runs them against the current implementation
on synthetic text, so a difference in the chosen header row or delimiter for
any generated input fails the test.
"""
import csv
import io
import random
import time

import pytest

from app import flow_worker


def _old_strict_delimited_prefix(decoded):
    lines = decoded.lstrip("﻿").splitlines()
    for start in range(min(len(lines), 2_000)):
        if not any(delimiter in lines[start] for delimiter in (",", ";", "\t", "|")):
            continue
        parsed = flow_worker._parse_strict_delimited_rows("\n".join(lines[start:]))
        if parsed is not None:
            return start, parsed[1]
    return None


def _old_asap_csv_header_index(rows):
    sections = []
    start = None
    for index, row in enumerate(rows):
        populated = any(str(value).strip() for value in row)
        if populated and start is None:
            start = index
        elif not populated and start is not None:
            sections.append((start, index))
            start = None
    if start is not None:
        sections.append((start, len(rows)))

    section_candidates = []
    for section_number, (section_start, section_end) in enumerate(sections):
        candidates = []
        for index in range(section_start, min(section_end, 2_000)):
            header = [str(value).strip() for value in rows[index]]
            populated = [value for value in header if value]
            if (
                len(populated) < 2
                or len({value.casefold() for value in populated}) != len(populated)
            ):
                continue
            width = len(header)
            data_rows = rows[index + 1:section_end]
            if not data_rows or any(
                any(str(value).strip() for value in row[width:]) for row in data_rows
            ):
                continue
            exact = sum(len(row) == width for row in data_rows)
            if exact * 2 < len(data_rows):
                continue
            nonempty = sum(
                sum(bool(str(value).strip()) for value in row[:width])
                for row in data_rows
            )
            candidates.append((len(data_rows), exact, nonempty, -index, index))
        if candidates:
            section_candidates.append((section_number, max(candidates)[-1]))
    return max(section_candidates)[-1] if section_candidates else 0


def _table(delimiter, rows=6, columns=4, start=0):
    lines = [delimiter.join(f"Column {index}" for index in range(columns))]
    lines += [
        delimiter.join(f"r{start + row}c{column}" for column in range(columns))
        for row in range(rows)
    ]
    return lines


FILTER_BLOCK = [f"Filter {index},Value {index}" for index in range(40)]

SYNTHETIC_FILES = {
    "plain comma table": _table(","),
    "semicolon table with decimal commas": ["Model;Share;Units", "A;0,5;10", "B;0,25;7", "C;1,5;3"],
    "tab table with commas in text": ["Name\tNote", "A\thello, world", "B\tone, two, three"],
    "pipe table": _table("|", rows=3, columns=3),
    "title, filter block, blank, table": ["Sales report", "", *FILTER_BLOCK, "", *_table(",", rows=30, columns=5)],
    "filter block longer than the table": ["Report", *FILTER_BLOCK, "", *_table(",", rows=3)],
    "two tables separated by a blank row": [*_table(",", rows=4, columns=3), "", *_table(",", rows=9, columns=6)],
    "header with a duplicate column": ["A,B,A", "1,2,3", "4,5,6", "", "X,Y", "1,2"],
    "header with a blank column": ["A,,C", "1,2,3", "4,5,6"],
    "rows wider than the header": ["A,B", "1,2,3", "4,5,6", "7,8,9"],
    "mostly short rows": ["A,B,C", "1", "2", "3", "4,5,6"],
    "padded short rows": ["A,B,C", "1,2", "3,4", "5,6,7", "8,9,10"],
    "trailing empty cells beyond the header": ["A,B", "1,2,,", "3,4,,", "5,6"],
    "prose without a table": ["This is a message, not a table.", "Please sign in again; your session expired."],
    "single column": ["Name", "A", "B"],
    "header only": ["A,B,C"],
    "empty text": [],
    "blank lines only": ["", "  ", ""],
    "quoted delimiters": ['Name,Note', '"Smith, John","a;b|c"', '"Lee, Ann","x"'],
    "multi-line quoted cell": ["Title, with comma", 'Name,Note', '"A","first', 'second, line"', '"B","plain"'],
    "multi-line quoted cell in the preamble": ['Filter,"value one', 'value, two', 'value three"', "", *_table(",")],
    "unterminated quote swallows the rest": ['Name,Note', 'A,"never closed', 'B,2', 'C,3'],
    "stray quote inside a value": ['Size,Label', '5" drive,old', '3.5" drive,older'],
    "doubled quotes": ['Name,Quote', 'A,"He said ""hi"", twice"', 'B,"plain"'],
    "mixed candidate delimiters": ["a;b,c|d", "1;2,3|4", "5;6,7|8", "9;10,11|12"],
    "comma header over semicolon rows": ["Units, total;Share", "1;2", "3;4"],
    "whitespace-only cells": ["A,B,C", " , ,1", "2, ,3", " , , "],
    "table then a note with a comma": [*_table(",", rows=5), "", "Note: figures are provisional, unaudited"],
    "table then trailing blank lines": [*_table(";", rows=5), "", "", ""],
    "preamble longer than the search window": [*(f"Preamble line {index}" for index in range(2_005)), *_table(",")],
    "table starting just inside the search window": [*("note" for _ in range(1_998)), *_table(",")],
    "delimited preamble before a late table": [
        "Report", *(f"Filter {index},Value" for index in range(60)), "", *_table("\t", rows=40, columns=7),
    ],
}


def _compare(text):
    """Assert both replaced functions still choose what the old algorithms chose."""
    expected = _old_strict_delimited_prefix(text)
    assert flow_worker._strict_delimited_prefix(text) == expected
    for delimiter in (",", ";", "\t", "|"):
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        except csv.Error:
            continue
        assert flow_worker._asap_csv_header_index(rows) == _old_asap_csv_header_index(rows)
    return expected


@pytest.mark.parametrize("name", sorted(SYNTHETIC_FILES))
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_header_detection_matches_the_previous_algorithm_on_synthetic_files(name, newline):
    lines = SYNTHETIC_FILES[name]
    for text in (newline.join(lines), newline.join(lines) + newline, "﻿" + newline.join(lines)):
        _compare(text)


def test_synthetic_files_cover_every_kind_of_answer():
    answers = {name: _old_strict_delimited_prefix("\n".join(lines)) for name, lines in SYNTHETIC_FILES.items()}
    assert answers["plain comma table"] == (0, ",")
    assert answers["semicolon table with decimal commas"] == (0, ";")
    assert answers["tab table with commas in text"] == (0, "\t")
    assert answers["pipe table"] == (0, "|")
    # The filter block is not the table: the wider data rows below overflow it.
    assert answers["title, filter block, blank, table"] == (43, ",")
    assert answers["filter block longer than the table"] == (42, ",")
    assert answers["prose without a table"] is None
    assert answers["preamble longer than the search window"] is None
    assert answers["table starting just inside the search window"] == (1_998, ",")
    assert {answer[1] for answer in answers.values() if answer} == {",", ";", "\t", "|"}


FUZZ_CELLS = ["a", "b", "A", "x y", "1", "2.5", "", " ", '"', '"q"', '"a,b"', '"multi', 'line"', "é", "0"]
FUZZ_DELIMITERS = [",", ",", ",", ";", "\t", "|"]


def _random_text(rng):
    lines = []
    for _ in range(rng.randint(0, 14)):
        shape = rng.random()
        if shape < 0.12:
            lines.append("")
        elif shape < 0.2:
            lines.append(rng.choice(["Report title", "  ", "Filter: all", 'He said "hi"']))
        else:
            delimiter = rng.choice(FUZZ_DELIMITERS) if rng.random() < 0.15 else None
            width = rng.choice([1, 2, 2, 3, 3, 3, 4, 5])
            cells = [rng.choice(FUZZ_CELLS) for _ in range(width)]
            lines.append((delimiter or rng.choice(FUZZ_DELIMITERS[:4])).join(cells))
    newline = rng.choice(["\n", "\n", "\r\n", "\r"])
    return newline.join(lines) + (newline if rng.random() < 0.5 else "")


def _random_table_text(rng):
    """Mostly rectangular text, so many inputs do contain an accepted table."""
    delimiter = rng.choice([",", ";", "\t", "|"])
    width = rng.randint(2, 5)
    lines = [rng.choice(["Title", "Filter,Value", "", "a;b", 'note "x"']) for _ in range(rng.randint(0, 4))]
    lines.append(delimiter.join(f"h{index}" if rng.random() < 0.93 else "h0" for index in range(width)))
    for _ in range(rng.randint(0, 9)):
        row_width = width if rng.random() < 0.8 else rng.randint(1, width + 2)
        lines.append(delimiter.join(rng.choice(FUZZ_CELLS) for _ in range(row_width)))
        if rng.random() < 0.08:
            lines.append("")
    return "\n".join(lines)


def test_header_detection_matches_the_previous_algorithm_on_random_text():
    rng = random.Random(20261111)
    answers = set()
    for _ in range(1_500):
        answers.add(_compare(_random_text(rng)))
        answers.add(_compare(_random_table_text(rng)))
    # The comparison is only meaningful if the inputs exercise real variety.
    assert None in answers and len(answers) > 12
    assert {answer[1] for answer in answers if answer} == {",", ";", "\t", "|"}


def test_header_detection_matches_when_the_csv_parser_rejects_a_field():
    # A field longer than the csv module's limit raises csv.Error for that
    # delimiter. Lower the limit so short random text reaches that branch.
    rng = random.Random(7)
    previous = csv.field_size_limit(6)
    try:
        answers = set()
        for _ in range(1_000):
            text = _random_table_text(rng)
            # Sometimes one over-long field sits below an otherwise valid table.
            text += "\n" + "w" * rng.randint(0, 12) + rng.choice(["", ",1", ";2"])
            expected = _old_strict_delimited_prefix(text)
            assert flow_worker._strict_delimited_prefix(text) == expected, text
            answers.add(expected)
    finally:
        csv.field_size_limit(previous)
    assert None in answers and len(answers) > 4


def test_header_index_matches_the_previous_algorithm_on_random_rows():
    rng = random.Random(1010)
    cells = ["a", "b", "c", "A", "1", "", " ", "x"]
    answers = set()
    for _ in range(4_000):
        rows = []
        for _ in range(rng.randint(0, 16)):
            if rng.random() < 0.15:
                rows.append(rng.choice([[], [""], [" ", ""]]))
            else:
                rows.append([rng.choice(cells) for _ in range(rng.choice([1, 2, 2, 3, 3, 4, 6]))])
        expected = _old_asap_csv_header_index(rows)
        assert flow_worker._asap_csv_header_index(rows) == expected, rows
        answers.add(expected)
    assert len(answers) > 8


def test_header_index_keeps_the_two_thousand_row_search_window():
    filler = [["same", "same"]] * 1_990  # never header-shaped: duplicate values
    table = [["Code", "Units"], *([f"c{index}", str(index)] for index in range(40))]
    for gap in range(0, 16):
        rows = [*filler, *([["same", "same"]] * gap), *table]
        expected = _old_asap_csv_header_index(rows)
        assert flow_worker._asap_csv_header_index(rows) == expected
    # One section straddling the window, and a later section wholly outside it.
    straddling = [*filler, *table, [], *table]
    assert flow_worker._asap_csv_header_index(straddling) == _old_asap_csv_header_index(straddling) == 1_990
    outside = [*filler, *([["same", "same"]] * 20), [], *table]
    assert flow_worker._asap_csv_header_index(outside) == _old_asap_csv_header_index(outside) == 0


def _large_export(rows, preamble=0):
    lines = ["Sales report", ""] if preamble else []
    lines += [f"Filter {index},Value {index}" for index in range(preamble)]
    lines += [""] if preamble else []
    lines.append("Region,Country,Model,Units,Share")
    lines += [f"R{index % 7},C{index % 91},M{index},{index * 3},{(index % 100) / 100}" for index in range(rows)]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("preamble", [0, 300])
def test_header_detection_is_linear_on_a_large_export(tmp_path, preamble):
    # Before: 20,000 rows took about 24 s and 40,000 about 51 s (quadratic),
    # and a 300-line filter block added a full reparse per filter line. The
    # same work now takes well under a second; the bound leaves wide headroom
    # for a slow CI machine while still failing a quadratic implementation.
    text = _large_export(40_000, preamble)
    header_line = preamble + 3 if preamble else 0

    started = time.perf_counter()
    assert flow_worker._strict_delimited_prefix(text) == (header_line, ",")
    rows = list(csv.reader(io.StringIO(text)))
    assert flow_worker._asap_csv_header_index(rows) == header_line
    path = tmp_path / "export.csv"
    path.write_text(text, encoding="utf-8")
    normalized = flow_worker._normalize_csv(path)
    elapsed = time.perf_counter() - started

    assert normalized["preamble_rows_removed"] == header_line
    assert normalized["columns"] == ["Region", "Country", "Model", "Units", "Share"]
    assert normalized["source_delimiter"] == ","
    assert elapsed < 10, f"header detection took {elapsed:.1f} s for 40,000 rows"
