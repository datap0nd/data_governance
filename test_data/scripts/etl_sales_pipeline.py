"""ETL pipeline - loads daily sales data from staging into analytics schema."""

import pandas as pd
from sqlalchemy import create_engine

engine = create_engine("postgresql://user:pass@db-host:5432/analytics")

# Read from staging tables
query = """
    SELECT o.*, c.customer_name, c.region
    FROM staging.orders o
    JOIN staging.customers c ON c.id = o.customer_id
    WHERE o.order_date >= CURRENT_DATE - INTERVAL '7 days'
"""
df = pd.read_sql(query, engine)

# Transformations
df["revenue"] = df["quantity"] * df["unit_price"]
df["margin"] = df["revenue"] - df["cost"]

# Write to analytics schema
df.to_sql("analytics.fact_sales", engine, if_exists="append", index=False)

# Refresh the materialized view
with engine.connect() as conn:
    conn.execute("REFRESH MATERIALIZED VIEW analytics.mv_sales_summary")
    conn.execute("TRUNCATE TABLE staging.temp_sales_buffer")
