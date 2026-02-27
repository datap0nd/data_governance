"""Tests for the TMDL scanner."""
import os
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.scanner.tmdl_parser import parse_tmdl_file, parse_expressions_file
from app.scanner.walker import walk_tmdl_root
from app.scanner.source_matcher import deduplicate_sources


def test_parse_sql_source():
    """Test parsing a TMDL file with a SQL Server source."""
    tmdl = Path(__file__).parent.parent / "test_data" / "reports" / "Weekly_Sales" / "Weekly_Sales.SemanticModel" / "Definition" / "Tables" / "Main.tmdl"
    result = parse_tmdl_file(tmdl)

    assert result is not None
    assert result.table_name == "Main"
    assert result.source is not None
    assert result.source.source_type == "sql"
    assert result.source.server == "sqlserver01.company.local"
    assert result.source.database == "SalesDB"
    assert result.source.sql_table == "dbo.Orders"
    print(f"  SQL source: {result.source.server}/{result.source.database} -> {result.source.sql_table}")


def test_parse_excel_source():
    """Test parsing a TMDL file with an Excel source."""
    tmdl = Path(__file__).parent.parent / "test_data" / "reports" / "Weekly_Sales" / "Weekly_Sales.SemanticModel" / "Definition" / "Tables" / "SKU Master.tmdl"
    result = parse_tmdl_file(tmdl)

    assert result is not None
    assert result.table_name == "SKU Master"
    assert result.source is not None
    assert result.source.source_type == "excel"
    assert result.source.file_path == r"C:\Data\SKU_Master.xlsx"
    assert result.source.sheet_or_table == "Sheet1"
    print(f"  Excel source: {result.source.file_path} -> {result.source.sheet_or_table}")


def test_parse_csv_source():
    """Test parsing a TMDL file with a CSV source."""
    tmdl = Path(__file__).parent.parent / "test_data" / "reports" / "Weekly_Sales" / "Weekly_Sales.SemanticModel" / "Definition" / "Tables" / "MP Plan.tmdl"
    result = parse_tmdl_file(tmdl)

    assert result is not None
    assert result.table_name == "MP Plan"
    assert result.source is not None
    assert result.source.source_type == "csv"
    assert result.source.file_path == r"C:\Data\Plans\MP_Plan_2026.csv"
    assert result.source.delimiter == ","
    print(f"  CSV source: {result.source.file_path} (delimiter: {result.source.delimiter})")


def test_parse_measures_table():
    """Test parsing a measures-only table (no data source)."""
    tmdl = Path(__file__).parent.parent / "test_data" / "reports" / "Weekly_Sales" / "Weekly_Sales.SemanticModel" / "Definition" / "Tables" / "Measures_table.tmdl"
    result = parse_tmdl_file(tmdl)

    assert result is not None
    assert result.table_name == "Measures_table"
    assert result.source is None  # No partition/source for a measures table
    print("  Measures table: correctly identified as no-source")


def test_walk_tmdl_root():
    """Test walking the full test_data folder structure."""
    root = Path(__file__).parent.parent / "test_data"
    reports = walk_tmdl_root(root)

    assert len(reports) == 3
    report_names = [r.name for r in reports]
    assert "Weekly_Sales" in report_names
    assert "Monthly_KPI" in report_names
    assert "Product_Mix" in report_names

    weekly = next(r for r in reports if r.name == "Weekly_Sales")
    tables_with_source = [t for t in weekly.tables if t.source is not None]
    assert len(tables_with_source) == 3  # Main (SQL), SKU Master (Excel), MP Plan (CSV)
    print(f"  Found {len(reports)} reports, Weekly_Sales has {len(tables_with_source)} sourced tables")


def test_deduplicate_sources():
    """Test that sources shared across reports are deduplicated."""
    root = Path(__file__).parent.parent / "test_data"
    reports = walk_tmdl_root(root)
    sources = deduplicate_sources(reports)

    # Expected unique sources:
    # 1. SQL: sqlserver01.company.local/SalesDB (used by Weekly_Sales, Monthly_KPI, Product_Mix)
    # 2. Excel: SKU_Master.xlsx (used by Weekly_Sales, Monthly_KPI)
    # 3. CSV: MP_Plan_2026.csv (Weekly_Sales only)
    # 4. CSV: Categories.csv (Product_Mix only)
    assert len(sources) == 4

    source_types = {s.source_type for s in sources.values()}
    assert "sql" in source_types
    assert "excel" in source_types
    assert "csv" in source_types
    print(f"  Deduplicated to {len(sources)} unique sources: {[s.display_name for s in sources.values()]}")


if __name__ == "__main__":
    tests = [
        test_parse_sql_source,
        test_parse_excel_source,
        test_parse_csv_source,
        test_parse_measures_table,
        test_walk_tmdl_root,
        test_deduplicate_sources,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            print(f"Running {test.__name__}...")
            test()
            print(f"  PASSED")
            passed += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {len(tests)} tests")
    sys.exit(1 if failed > 0 else 0)
