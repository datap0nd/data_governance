"""
Generate expanded TMDL test data for the Data Governance Panel.

Creates 8 additional reports under test_data/reports/ and
sample data files under test_data/sample_files/.
"""

import csv
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent / "test_data"
REPORTS_DIR = BASE / "reports"
SAMPLES_DIR = BASE / "sample_files"


def guid():
    return str(uuid.uuid4())


# ── Report definitions ──

REPORTS = [
    {
        "name": "HR_Dashboard",
        "report_owner": "Sofia Rodrigues",
        "business_owner": "Carlos Mendes",
        "tables": [
            {"name": "Employee_Roster", "type": "csv", "path": "C:\\Data\\HR\\employee_roster.csv",
             "columns": [("employee_id", "string"), ("name", "string"), ("department", "string"), ("hire_date", "string")]},
            {"name": "Payroll_Export", "type": "excel", "path": "C:\\Data\\HR\\payroll_export.xlsx",
             "columns": [("employee_id", "string"), ("month", "string"), ("gross_pay", "decimal"), ("net_pay", "decimal")]},
        ],
    },
    {
        "name": "Finance_Monthly",
        "report_owner": "Joao Santos",
        "business_owner": "Ana Costa",
        "tables": [
            {"name": "GL_Transactions", "type": "csv", "path": "C:\\Data\\Finance\\gl_transactions.csv",
             "columns": [("txn_id", "string"), ("account", "string"), ("amount", "decimal"), ("date", "string")]},
            {"name": "Budget_Forecast", "type": "excel", "path": "C:\\Data\\Finance\\budget_forecast.xlsx",
             "columns": [("department", "string"), ("q1", "decimal"), ("q2", "decimal"), ("q3", "decimal"), ("q4", "decimal")]},
            {"name": "Vendor_Payments", "type": "csv", "path": "C:\\Data\\Finance\\vendor_payments.csv",
             "columns": [("vendor_id", "string"), ("vendor_name", "string"), ("amount", "decimal"), ("paid_date", "string")]},
        ],
    },
    {
        "name": "Supply_Chain_Tracker",
        "report_owner": "Pedro Lima",
        "business_owner": "Maria Silva",
        "tables": [
            {"name": "Inventory_Levels", "type": "excel", "path": "C:\\Data\\Supply\\inventory_levels.xlsx",
             "columns": [("sku", "string"), ("warehouse", "string"), ("qty_on_hand", "int64"), ("reorder_point", "int64")]},
            {"name": "Shipping_Log", "type": "csv", "path": "C:\\Data\\Supply\\shipping_log.csv",
             "columns": [("shipment_id", "string"), ("origin", "string"), ("destination", "string"), ("status", "string"), ("ship_date", "string")]},
            {"name": "Warehouse_DB", "type": "sql", "server": "sqlserver01.company.local", "database": "WarehouseDB",
             "schema": "dbo", "table": "StockMovements",
             "columns": [("movement_id", "string"), ("sku", "string"), ("qty", "int64"), ("movement_date", "string")]},
            {"name": "Supplier_Catalog", "type": "csv", "path": "C:\\Data\\Supply\\supplier_catalog.csv",
             "columns": [("supplier_id", "string"), ("supplier_name", "string"), ("lead_time_days", "int64"), ("rating", "decimal")]},
        ],
    },
    {
        "name": "Customer_360",
        "report_owner": "Rafael Cunha",
        "business_owner": "Carlos Mendes",
        "tables": [
            {"name": "CRM_Contacts", "type": "sql", "server": "sqlserver01.company.local", "database": "CRM_DB",
             "schema": "dbo", "table": "Contacts",
             "columns": [("contact_id", "string"), ("company", "string"), ("email", "string"), ("segment", "string")]},
            {"name": "Survey_Responses", "type": "csv", "path": "C:\\Data\\CRM\\survey_responses.csv",
             "columns": [("response_id", "string"), ("customer_id", "string"), ("score", "int64"), ("comment", "string")]},
            {"name": "Support_Tickets", "type": "csv", "path": "C:\\Data\\CRM\\support_tickets.csv",
             "columns": [("ticket_id", "string"), ("customer_id", "string"), ("priority", "string"), ("status", "string"), ("created", "string")]},
        ],
    },
    {
        "name": "Marketing_ROI",
        "report_owner": "Sofia Rodrigues",
        "business_owner": "Joao Santos",
        "tables": [
            {"name": "Campaign_Metrics", "type": "csv", "path": "C:\\Data\\Marketing\\campaign_metrics.csv",
             "columns": [("campaign_id", "string"), ("channel", "string"), ("impressions", "int64"), ("clicks", "int64"), ("conversions", "int64")]},
            {"name": "Ad_Spend", "type": "excel", "path": "C:\\Data\\Marketing\\ad_spend.xlsx",
             "columns": [("month", "string"), ("channel", "string"), ("budget", "decimal"), ("actual_spend", "decimal")]},
        ],
    },
    {
        "name": "Executive_Summary",
        "report_owner": "Ana Costa",
        "business_owner": "Maria Silva",
        "tables": [
            {"name": "Revenue_Data", "type": "sql", "server": "sqlserver01.company.local", "database": "FinanceDB",
             "schema": "dbo", "table": "MonthlyRevenue",
             "columns": [("month", "string"), ("region", "string"), ("revenue", "decimal"), ("target", "decimal")]},
            {"name": "Headcount", "type": "csv", "path": "C:\\Data\\Executive\\headcount.csv",
             "columns": [("department", "string"), ("headcount", "int64"), ("budget_hc", "int64"), ("date", "string")]},
            {"name": "Project_Status", "type": "excel", "path": "C:\\Data\\Executive\\project_status.xlsx",
             "columns": [("project", "string"), ("owner", "string"), ("status", "string"), ("pct_complete", "decimal")]},
            {"name": "Customer_NPS", "type": "csv", "path": "C:\\Data\\Executive\\customer_nps.csv",
             "columns": [("quarter", "string"), ("segment", "string"), ("nps_score", "decimal"), ("responses", "int64")]},
            {"name": "Financial_KPIs", "type": "sql", "server": "sqlserver01.company.local", "database": "FinanceDB",
             "schema": "dbo", "table": "KPI_Dashboard",
             "columns": [("kpi_name", "string"), ("value", "decimal"), ("target", "decimal"), ("period", "string")]},
        ],
    },
    {
        "name": "Inventory_Analysis",
        "report_owner": "Carlos Mendes",
        "business_owner": "Pedro Lima",
        "tables": [
            {"name": "Warehouse_Stock", "type": "excel", "path": "C:\\Data\\Supply\\inventory_levels.xlsx",  # shared source
             "columns": [("sku", "string"), ("warehouse", "string"), ("qty_on_hand", "int64"), ("reorder_point", "int64")]},
            {"name": "Reorder_Points", "type": "csv", "path": "C:\\Data\\Inventory\\reorder_points.csv",
             "columns": [("sku", "string"), ("min_qty", "int64"), ("max_qty", "int64"), ("lead_time", "int64")]},
        ],
    },
    {
        "name": "Sales_Pipeline",
        "report_owner": "Rafael Cunha",
        "business_owner": "Sofia Rodrigues",
        "tables": [
            {"name": "Opportunities", "type": "sql", "server": "sqlserver01.company.local", "database": "CRM_DB",
             "schema": "sales", "table": "Opportunities",
             "columns": [("opp_id", "string"), ("account", "string"), ("stage", "string"), ("value", "decimal"), ("close_date", "string")]},
            {"name": "Activities_Log", "type": "csv", "path": "C:\\Data\\Sales\\activities_log.csv",
             "columns": [("activity_id", "string"), ("opp_id", "string"), ("type", "string"), ("date", "string"), ("notes", "string")]},
            {"name": "Forecast", "type": "excel", "path": "C:\\Data\\Sales\\forecast.xlsx",
             "columns": [("quarter", "string"), ("rep", "string"), ("pipeline", "decimal"), ("weighted", "decimal")]},
        ],
    },
]

# Freshness ages for sample files (days ago)
FILE_AGES = {
    "employee_roster.csv": 2,
    "payroll_export.xlsx": 15,
    "gl_transactions.csv": 5,
    "budget_forecast.xlsx": 60,
    "vendor_payments.csv": 1,
    "inventory_levels.xlsx": 35,
    "shipping_log.csv": 3,
    "supplier_catalog.csv": 95,
    "survey_responses.csv": 10,
    "support_tickets.csv": 0,
    "campaign_metrics.csv": 7,
    "ad_spend.xlsx": 45,
    "headcount.csv": 20,
    "project_status.xlsx": 100,
    "customer_nps.csv": 12,
    "reorder_points.csv": 8,
    "activities_log.csv": 1,
    "forecast.xlsx": 25,
}

SAMPLE_DATA = {
    "string": ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa",
                "Lambda", "Mu", "Nu", "Xi", "Omicron", "Pi", "Rho", "Sigma", "Tau", "Upsilon"],
    "decimal": [round(random.uniform(100, 99999), 2) for _ in range(20)],
    "int64": [random.randint(1, 10000) for _ in range(20)],
}


def make_tmdl_m_expression(table_def):
    """Generate the M expression (partition source) for a table."""
    t = table_def["type"]
    if t == "csv":
        return (
            f'let\n'
            f'    Source = Csv.Document(File.Contents("{table_def["path"]}"),'
            f'[Delimiter=",", Columns={len(table_def["columns"])}, Encoding=65001, QuoteStyle=QuoteStyle.None]),\n'
            f'    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true])\n'
            f'in\n'
            f'    #"Promoted Headers"'
        )
    elif t == "excel":
        return (
            f'let\n'
            f'    Source = Excel.Workbook(File.Contents("{table_def["path"]}"), null, true),\n'
            f'    Sheet1_Sheet = Source{{[Item="Sheet1",Kind="Sheet"]}}[Data],\n'
            f'    #"Promoted Headers" = Table.PromoteHeaders(Sheet1_Sheet, [PromoteAllScalars=true])\n'
            f'in\n'
            f'    #"Promoted Headers"'
        )
    elif t == "sql":
        return (
            f'let\n'
            f'    Source = Sql.Database("{table_def["server"]}", "{table_def["database"]}"),\n'
            f'    {table_def["schema"]}_{table_def["table"]} = Source{{[Schema="{table_def["schema"]}",Item="{table_def["table"]}"]}}[Data]\n'
            f'in\n'
            f'    {table_def["schema"]}_{table_def["table"]}'
        )


def make_tmdl_file(table_def):
    """Generate a complete .tmdl file for a data table."""
    name = table_def["name"]
    tag = guid()
    lines = [f"table {name}", f"\tlineageTag: {tag}", ""]

    for col_name, col_type in table_def["columns"]:
        dt = {"string": "string", "decimal": "decimal", "int64": "int64"}.get(col_type, "string")
        summarize = "sum" if dt in ("decimal", "int64") else "none"
        lines.append(f"\tcolumn {col_name}")
        lines.append(f"\t\tdataType: {dt}")
        lines.append(f"\t\tlineageTag: {guid()}")
        lines.append(f"\t\tsummarizeBy: {summarize}")
        lines.append(f"\t\tsourceColumn: {col_name}")
        lines.append("")

    lines.append(f"\tpartition {name} = m")
    lines.append(f"\t\tmode: import")
    lines.append(f"\t\tsource =")
    m_expr = make_tmdl_m_expression(table_def)
    for m_line in m_expr.split("\n"):
        lines.append(f"\t\t\t\t{m_line}")
    lines.append("")
    lines.append(f"\tannotation PBI_ResultType = Table")
    return "\n".join(lines) + "\n"


def make_owner_tmdl(label, value):
    """Generate a metadata owner TMDL file."""
    tag = guid()
    return (
        f"table '{label}'\n"
        f"\tlineageTag: {tag}\n"
        f"\n"
        f"\tcolumn '{label}'\n"
        f"\t\tdataType: string\n"
        f"\t\tlineageTag: {guid()}\n"
        f"\t\tsummarizeBy: none\n"
        f"\t\tsourceColumn: {label}\n"
        f"\n"
        f"\t\tannotation SummarizationSetBy = Automatic\n"
        f"\n"
        f"\tpartition '{label}' = m\n"
        f"\t\tmode: import\n"
        f"\t\tsource =\n"
        f'\t\t\t\tlet\n'
        f'\t\t\t\t    Source = #table({{"{label}"}}, {{{{"{value}"}}}})\n'
        f'\t\t\t\tin\n'
        f'\t\t\t\t    Source\n'
        f"\n"
        f"\tannotation PBI_ResultType = Table\n"
    )


def make_sample_csv(filename, columns, n_rows=25):
    """Create a sample CSV file with random data."""
    filepath = SAMPLES_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([c[0] for c in columns])
        for i in range(n_rows):
            row = []
            for col_name, col_type in columns:
                pool = SAMPLE_DATA.get(col_type, SAMPLE_DATA["string"])
                row.append(pool[i % len(pool)])
            writer.writerow(row)

    return filepath


def make_sample_xlsx(filename, columns, n_rows=20):
    """Create a minimal XLSX-like file (actually just CSV for probe testing)."""
    # We create a real file so probes can check modification time
    filepath = SAMPLES_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w") as f:
        f.write(",".join(c[0] for c in columns) + "\n")
        for i in range(n_rows):
            row = []
            for col_name, col_type in columns:
                pool = SAMPLE_DATA.get(col_type, SAMPLE_DATA["string"])
                row.append(str(pool[i % len(pool)]))
            f.write(",".join(row) + "\n")

    return filepath


def set_file_age(filepath, days_ago):
    """Set a file's modification time to N days ago."""
    target_time = datetime.now() - timedelta(days=days_ago)
    ts = target_time.timestamp()
    os.utime(filepath, (ts, ts))


def main():
    print(f"Generating test data in {BASE}")

    created_files = set()

    for report in REPORTS:
        name = report["name"]
        tables_dir = REPORTS_DIR / name / f"{name}.SemanticModel" / "Definition" / "Tables"
        tables_dir.mkdir(parents=True, exist_ok=True)

        # Write Report Owner and Business Owner tmdl files
        (tables_dir / "Report Owner.tmdl").write_text(
            make_owner_tmdl("Report Owner", report["report_owner"])
        )
        (tables_dir / "Business Owner.tmdl").write_text(
            make_owner_tmdl("Business Owner", report["business_owner"])
        )

        # Write data table tmdl files
        for table_def in report["tables"]:
            tmdl_content = make_tmdl_file(table_def)
            tmdl_path = tables_dir / f"{table_def['name']}.tmdl"
            tmdl_path.write_text(tmdl_content)

            # Create sample data files for file-based sources
            if table_def["type"] in ("csv", "excel"):
                filename = table_def["path"].replace("\\", "/").split("/")[-1]
                if filename not in created_files:
                    if table_def["type"] == "csv":
                        fp = make_sample_csv(filename, table_def["columns"])
                    else:
                        fp = make_sample_xlsx(filename, table_def["columns"])

                    age = FILE_AGES.get(filename, 10)
                    set_file_age(fp, age)
                    created_files.add(filename)
                    print(f"  {filename:30s} → {age:>3d} days old")

        print(f"  Report: {name} ({len(report['tables'])} tables)")

    print(f"\nDone! {len(REPORTS)} new reports, {len(created_files)} sample files created.")
    print(f"Total reports in test_data: {len(list(REPORTS_DIR.iterdir()))} (including originals)")


if __name__ == "__main__":
    main()
