"""Report exporter - reads analytics data and exports to CSV for finance team."""

import os
import pandas as pd
from sqlalchemy import create_engine
from pathlib import Path

engine = create_engine("postgresql://readonly:pass@db-host:5432/analytics")

# Read multiple analytics tables
df_sales = pd.read_sql("SELECT * FROM analytics.fact_sales WHERE year = 2025", engine)
df_regions = pd.read_sql("SELECT * FROM analytics.dim_regions", engine)
df_summary = pd.read_sql("SELECT * FROM analytics.mv_sales_summary", engine)

# Join and aggregate
report = df_sales.merge(df_regions, on="region_id")
monthly = report.groupby(["month", "region_name"]).agg(
    total_revenue=("revenue", "sum"),
    total_orders=("order_id", "nunique"),
).reset_index()

# Export via COPY TO for the raw backup
with engine.connect() as conn:
    conn.execute("COPY analytics.fact_sales TO '/exports/fact_sales_backup.csv' CSV HEADER")

# Save processed report
output_dir = Path("/exports/finance")
output_dir.mkdir(parents=True, exist_ok=True)
monthly.to_csv(output_dir / "monthly_revenue.csv", index=False)

# Drop old temp table if it exists
with engine.connect() as conn:
    conn.execute("DROP TABLE IF EXISTS analytics.temp_finance_export")
    conn.execute("""
        CREATE TABLE analytics.temp_finance_export AS
        SELECT * FROM analytics.fact_sales WHERE year = 2025
    """)
