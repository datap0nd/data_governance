"""Inventory sync - pulls warehouse data and updates inventory tables."""

import psycopg2
import pandas as pd
from datetime import datetime

conn = psycopg2.connect(
    host="db-host", port=5432, dbname="warehouse",
    user="etl_user", password="secret"
)

# Read current inventory levels
df_inv = pd.read_sql("SELECT * FROM warehouse.inventory_levels", conn)
df_products = pd.read_sql("SELECT * FROM warehouse.products", conn)

# Read from external source via COPY
cur = conn.cursor()
cur.execute("COPY warehouse.raw_shipments FROM '/data/shipments.csv' CSV HEADER")

# Process and write results
merged = df_inv.merge(df_products, on="product_id")
merged["stock_status"] = merged.apply(
    lambda r: "low" if r["qty_on_hand"] < r["reorder_point"] else "ok", axis=1
)

# Insert into reporting schema
merged.to_sql("reporting.inventory_snapshot", conn, if_exists="replace", index=False)

# Also update the daily summary
cur.execute("""
    INSERT INTO reporting.inventory_daily_summary (snapshot_date, total_items, low_stock_count)
    SELECT CURRENT_DATE, COUNT(*), SUM(CASE WHEN stock_status = 'low' THEN 1 ELSE 0 END)
    FROM reporting.inventory_snapshot
""")

conn.commit()
conn.close()
