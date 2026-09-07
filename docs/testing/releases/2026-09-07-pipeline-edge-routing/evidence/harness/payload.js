window.__payload = {
  report: { id: 1, name: "Sales Overview", status: "fresh", owner: "Ana", can_refresh: false },
  pages: [
    { page_name: "Overview", visuals: [
      { visual_db_id: 1, visual_type: "barChart", title: "Revenue by month", fields: [{ table: "FactSales", field: "Revenue" }, { table: "DimDate", field: "Month" }] },
      { visual_db_id: 2, visual_type: "card", title: "Total revenue", fields: [{ table: "FactSales", field: "Revenue" }] },
      { visual_db_id: 3, visual_type: "slicer", title: "Region", fields: [{ table: "DimRegion", field: "Region" }] },
    ]},
    { page_name: "Detail", visuals: [
      { visual_db_id: 4, visual_type: "tableEx", title: "Orders", fields: [{ table: "FactOrders", field: "OrderId" }, { table: "DimCustomer", field: "Customer" }] },
      { visual_db_id: 5, visual_type: "lineChart", title: "Margin trend", fields: [{ table: "FactSales", field: "Margin" }, { table: "DimDate", field: "Month" }] },
    ]},
  ],
  tables: [
    { id: 1, table_name: "FactSales", source_id: 10 },
    { id: 2, table_name: "DimDate", source_id: 11 },
    { id: 3, table_name: "DimRegion", source_id: 12 },
    { id: 4, table_name: "FactOrders", source_id: 13 },
    { id: 5, table_name: "DimCustomer", source_id: 14 },
  ],
  sources: [
    { id: 10, name: "mv_sales", type: "postgres", status: "fresh", last_data_at: "2026-09-06T08:00:00Z", row_count: 120000, freshness_rule_type: "daily", upstream_id: null, postgres_ref: true },
    { id: 11, name: "dim_date.xlsx", type: "excel", status: "fresh", last_data_at: "2026-09-01T08:00:00Z", row_count: 3650, freshness_rule_type: "weekly", upstream_id: 100 },
    { id: 12, name: "regions.csv", type: "csv", status: "stale", last_data_at: "2026-08-01T08:00:00Z", row_count: 40, freshness_rule_type: "weekly", upstream_id: 101 },
    { id: 13, name: "mv_orders", type: "postgres", status: "fresh", last_data_at: "2026-09-06T07:00:00Z", row_count: 88000, freshness_rule_type: "daily", upstream_id: null, postgres_ref: true },
    { id: 14, name: "customers.xlsx", type: "excel", status: "fresh", last_data_at: "2026-09-05T08:00:00Z", row_count: 2000, freshness_rule_type: "weekly", upstream_id: 100 },
    { id: 20, name: "raw_sales", type: "postgres", status: "fresh", last_data_at: "2026-09-06T06:00:00Z", row_count: 500000, freshness_rule_type: "daily", upstream_id: 102 },
    { id: 21, name: "raw_orders", type: "postgres", status: "fresh", last_data_at: "2026-09-06T06:00:00Z", row_count: 300000, freshness_rule_type: "daily", upstream_id: 102 },
    { id: 22, name: "raw_products", type: "postgres", status: "fresh", last_data_at: "2026-09-06T06:00:00Z", row_count: 9000, freshness_rule_type: "daily", upstream_id: 101 },
    { id: 30, name: "erp_extract", type: "postgres", status: "fresh", last_data_at: "2026-09-06T05:00:00Z", row_count: 900000, freshness_rule_type: "daily", upstream_id: 103 },
  ],
  source_deps: [
    { source_id: 10, depends_on_id: 20 },
    { source_id: 10, depends_on_id: 22 },
    { source_id: 13, depends_on_id: 21 },
    { source_id: 13, depends_on_id: 22 },
    { source_id: 20, depends_on_id: 30 },
    { source_id: 21, depends_on_id: 30 },
  ],
  flows: [
    { id: 1, name: "Load sales extract", last_status: "succeeded", last_success_at: "2026-09-06T06:00:00Z", target_kind: "sql", sql_schema: "raw", sql_table: "sales", target_source_ids: [20] },
    { id: 2, name: "Load orders extract", last_status: "failed", last_success_at: "2026-09-05T06:00:00Z", target_kind: "sql", sql_schema: "raw", sql_table: "orders", target_source_ids: [21] },
    { id: 3, name: "Customer workbook", last_status: "succeeded", last_success_at: "2026-09-05T08:00:00Z", target_kind: "file", target: { filename: "customers.xlsx" }, target_source_ids: [14] },
    { id: 4, name: "Regions file (candidate)", last_status: null, executable: false, target_kind: "file", target: { filename: "regions.csv" }, target_source_ids: [12] },
  ],
  upstreams: [
    { id: 100, name: "SharePoint Finance", refresh_day: "Monday" },
    { id: 101, name: "Regional planning", refresh_day: "Friday" },
    { id: 102, name: "ERP staging DB", refresh_day: "Daily" },
    { id: 103, name: "SAP ERP", refresh_day: "Daily" },
  ],
};
