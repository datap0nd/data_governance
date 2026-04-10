"""Tests for the script scanner - validates extraction against real team script patterns."""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.scanner.script_scanner import (
    _extract_read_tables,
    _extract_write_tables,
    _extract_file_reads,
    _extract_file_writes,
    _extract_url_reads,
    _is_sql_false_positive,
    _normalize_file_path,
)


# ---------------------------------------------------------------------------
# SQL Read Detection
# ---------------------------------------------------------------------------

def test_sql_read_from_read_sql():
    """Alert_from_SQL.py: pd.read_sql("SELECT * FROM bi_reporting.agreed_price_alert", engine)"""
    code = '''
engine = create_engine("postgresql+psycopg2://metomx:123456789@111.101.50.135:5432/postgres")
df = pd.read_sql("SELECT * FROM bi_reporting.agreed_price_alert", engine)
'''
    tables = _extract_read_tables(code)
    assert "bi_reporting.agreed_price_alert" in tables, f"Expected bi_reporting.agreed_price_alert, got {tables}"


def test_sql_read_triple_quoted_from_join():
    """etl_sales_pipeline.py: triple-quoted SQL with FROM + JOIN."""
    code = '''
query = """
    SELECT o.*, c.customer_name, c.region
    FROM staging.orders o
    JOIN staging.customers c ON c.id = o.customer_id
    WHERE o.order_date >= CURRENT_DATE - INTERVAL '7 days'
"""
df = pd.read_sql(query, engine)
'''
    tables = _extract_read_tables(code)
    assert "staging.orders" in tables, f"Expected staging.orders, got {tables}"
    assert "staging.customers" in tables, f"Expected staging.customers, got {tables}"


def test_sql_read_multiple_read_sql():
    """report_export.py: multiple pd.read_sql calls."""
    code = '''
df_sales = pd.read_sql("SELECT * FROM analytics.fact_sales WHERE year = 2025", engine)
df_regions = pd.read_sql("SELECT * FROM analytics.dim_regions", engine)
df_summary = pd.read_sql("SELECT * FROM analytics.mv_sales_summary", engine)
'''
    tables = _extract_read_tables(code)
    assert "analytics.fact_sales" in tables
    assert "analytics.dim_regions" in tables
    assert "analytics.mv_sales_summary" in tables


def test_sql_read_with_cte():
    """CTE names should be filtered out from reads."""
    code = '''
query = """
    WITH daily_totals AS (
        SELECT date, SUM(amount) as total
        FROM reporting.daily_sales
        GROUP BY date
    ),
    monthly_agg AS (
        SELECT date_trunc('month', date) as month, SUM(total) as monthly_total
        FROM daily_totals
        GROUP BY 1
    )
    SELECT * FROM monthly_agg
"""
'''
    tables = _extract_read_tables(code)
    assert "reporting.daily_sales" in tables
    assert "daily_totals" not in tables, "CTE name should be filtered"
    assert "monthly_agg" not in tables, "CTE name should be filtered"


def test_sql_read_inventory_sync():
    """inventory_sync.py: read_sql with inline SQL."""
    code = '''
df_inv = pd.read_sql("SELECT * FROM warehouse.inventory_levels", conn)
df_products = pd.read_sql("SELECT * FROM warehouse.products", conn)
'''
    tables = _extract_read_tables(code)
    assert "warehouse.inventory_levels" in tables
    assert "warehouse.products" in tables


# ---------------------------------------------------------------------------
# SQL Read False Positive Prevention
# ---------------------------------------------------------------------------

def test_sql_false_positive_bare_words():
    """Bare English words without dots or underscores must be rejected."""
    for word in ["reporting", "external", "channel", "inventory", "orders",
                 "customers", "products", "dashboard", "pipeline"]:
        assert _is_sql_false_positive(word), f"'{word}' should be a false positive"


def test_sql_false_positive_explicit_set():
    """Words in the explicit false positives set must be caught."""
    for word in ["public", "dbo", "external", "keep", "main",
                 "pandas", "selenium", "requests"]:
        assert _is_sql_false_positive(word), f"'{word}' should be a false positive"


def test_sql_valid_table_names():
    """Real schema.table names must NOT be flagged as false positives."""
    for name in ["bi_reporting.agreed_price_alert", "staging.orders",
                 "analytics.fact_sales", "warehouse.inventory_levels",
                 "samsung_health.psi_data", "reporting.inventory_snapshot"]:
        assert not _is_sql_false_positive(name), f"'{name}' should NOT be a false positive"


def test_sql_valid_underscore_names():
    """Unqualified names with underscores should pass (to_sql targets)."""
    for name in ["fact_sales", "channel_mappings", "inventory_snapshot",
                 "agreed_price_alert", "dim_date"]:
        assert not _is_sql_false_positive(name), f"'{name}' should NOT be a false positive"


def test_sql_no_bare_words_from_wrapper():
    """Wrapper function calls should not produce bare word false positives.

    The old bare-word regex caught things like load_data("config_key").
    After removal, only qualified schema.table patterns should match wrappers.
    """
    code = '''
load_data("reporting")
upload_file("credentials")
download_images("dashboard")
'''
    tables = _extract_write_tables(code)
    assert "reporting" not in tables, "bare word 'reporting' from load_data should not be detected"
    assert "credentials" not in tables
    assert "dashboard" not in tables


# ---------------------------------------------------------------------------
# SQL Write Detection
# ---------------------------------------------------------------------------

def test_sql_write_to_sql_with_dot():
    """etl_sales_pipeline.py: .to_sql("analytics.fact_sales", ...)"""
    code = '''
df.to_sql("analytics.fact_sales", engine, if_exists="append", index=False)
'''
    tables = _extract_write_tables(code)
    assert "analytics.fact_sales" in tables


def test_sql_write_refresh_materialized_view():
    """etl_sales_pipeline.py: REFRESH MATERIALIZED VIEW."""
    code = '''
conn.execute("REFRESH MATERIALIZED VIEW analytics.mv_sales_summary")
'''
    tables = _extract_write_tables(code)
    assert "analytics.mv_sales_summary" in tables


def test_sql_write_truncate():
    """etl_sales_pipeline.py: TRUNCATE TABLE."""
    code = '''
conn.execute("TRUNCATE TABLE staging.temp_sales_buffer")
'''
    tables = _extract_write_tables(code)
    assert "staging.temp_sales_buffer" in tables


def test_sql_write_insert_into():
    """inventory_sync.py: INSERT INTO reporting.inventory_daily_summary."""
    code = '''
cur.execute("""
    INSERT INTO reporting.inventory_daily_summary (snapshot_date, total_items, low_stock_count)
    SELECT CURRENT_DATE, COUNT(*), SUM(CASE WHEN stock_status = 'low' THEN 1 ELSE 0 END)
    FROM reporting.inventory_snapshot
""")
'''
    tables = _extract_write_tables(code)
    assert "reporting.inventory_daily_summary" in tables


def test_sql_write_to_sql_with_schema_kwarg():
    """to_sql with name= and schema= keyword arguments."""
    code = '''
sql_schema = "bi_reporting"
sql_table_name = "weekly_tp"
df.to_sql(name=sql_table_name, con=engine, schema=sql_schema, if_exists="replace")
'''
    tables = _extract_write_tables(code)
    assert "bi_reporting.weekly_tp" in tables


def test_sql_write_copy_from_stdin():
    """Table_manipulation: COPY table FROM STDIN via copy_expert."""
    code = '''
copy_sql = "COPY reporting.store_psi FROM STDIN WITH CSV"
cursor.copy_expert(copy_sql, buffer)
'''
    tables = _extract_write_tables(code)
    assert "reporting.store_psi" in tables


def test_sql_write_drop_and_create():
    """report_export.py: DROP + CREATE TABLE AS."""
    code = '''
conn.execute("DROP TABLE IF EXISTS analytics.temp_finance_export")
conn.execute("""
    CREATE TABLE analytics.temp_finance_export AS
    SELECT * FROM analytics.fact_sales WHERE year = 2025
""")
'''
    tables = _extract_write_tables(code)
    assert "analytics.temp_finance_export" in tables


def test_sql_write_wrapper_qualified():
    """Write_to_SQL(df, "schema.table") - wrapper with qualified name."""
    code = '''
Write_to_SQL(df, "reporting.store_psi", delete_historical=True)
'''
    tables = _extract_write_tables(code)
    assert "reporting.store_psi" in tables


def test_sql_write_fstring_variable_resolution():
    """COPY {sql_Table} with variable assignment."""
    code = '''
sql_table_name = "daily_summary"
sql_schema = "reporting"
copy_sql = f"COPY {sql_table_name} FROM STDIN WITH CSV"
cursor.copy_expert(copy_sql, buffer)
'''
    # Variable name matches the pattern, but COPY {var} resolution is
    # for the full f-string pattern. The var_map lookup needs sql_table_name = "..."
    # This should NOT resolve because COPY {sql_table_name} doesn't match
    # the f-string resolution regex (which looks for specific var names like table_name)
    tables = _extract_write_tables(code)
    # sql_table_name IS in the variable pattern list, so it should resolve
    assert "daily_summary" in tables or "reporting.daily_summary" in tables


def test_sql_write_copy_from():
    """inventory_sync.py: COPY schema.table FROM path."""
    code = '''
cur.execute("COPY warehouse.raw_shipments FROM '/data/shipments.csv' CSV HEADER")
'''
    tables = _extract_write_tables(code)
    assert "warehouse.raw_shipments" in tables


# ---------------------------------------------------------------------------
# File Read Detection
# ---------------------------------------------------------------------------

def test_file_read_workbooks_open_full_path():
    """AppleLaunch main.py: Workbooks.Open with full Windows path."""
    code = r'''
wb = excel.Workbooks.Open("D:\Projects\AppleLauch/Raw - Copy.xlsx", UpdateLinks=0)
'''
    files = _extract_file_reads(code)
    found = [f for f in files if "Raw - Copy.xlsx" in f]
    assert len(found) == 1, f"Expected to find 'Raw - Copy.xlsx' in {files}"
    assert found[0].startswith("[excel]"), f"Should be tagged [excel], got {found[0]}"
    assert "D:" in found[0] or "Projects" in found[0], f"Full path expected, got {found[0]}"


def test_file_read_workbooks_open_forward_slash():
    """Google Reviews main.py: Workbooks.Open with forward slashes."""
    code = '''
wb = excel.Workbooks.Open("D:/Projects/Reviews_Mappings/Collected Fact Table_additional.xlsx", UpdateLinks=0)
'''
    files = _extract_file_reads(code)
    found = [f for f in files if "Collected Fact Table_additional.xlsx" in f]
    assert len(found) == 1
    assert "D:/Projects" in found[0], f"Full path expected, got {found[0]}"


def test_file_read_excel_with_r_prefix():
    """Raw string prefix: read_excel(r"C:\\path\\file.xlsx")."""
    code = r'''
df = pd.read_excel(r"C:\Users\Admin\Desktop\Sales Data.xlsx")
'''
    files = _extract_file_reads(code)
    found = [f for f in files if "Sales Data.xlsx" in f]
    assert len(found) == 1, f"r-prefix should be handled. Got: {files}"
    assert "[excel]" in found[0]
    assert "Users" in found[0], f"Full path expected, got {found[0]}"


def test_file_read_csv_with_r_prefix():
    """Raw string prefix: read_csv(r"\\server\\share\\data.csv")."""
    code = r'''
df = pd.read_csv(r"\\METO-MX02\Users\METOMX\Desktop\data.csv")
'''
    files = _extract_file_reads(code)
    found = [f for f in files if "data.csv" in f]
    assert len(found) == 1, f"Expected csv read detection, got {files}"
    assert "[csv]" in found[0]
    assert "METO-MX02" in found[0] or "Desktop" in found[0], f"Full path expected, got {found[0]}"


def test_file_read_no_generic_excel_label():
    """If read_excel uses a variable, NO generic 'Excel files' should appear."""
    code = '''
file_path = get_path()
df = pd.read_excel(file_path)
'''
    files = _extract_file_reads(code)
    for f in files:
        assert "Excel files" not in f, f"Generic 'Excel files' label found: {f}"


def test_file_read_no_generic_csv_label():
    """If read_csv uses a variable, NO generic 'CSV files' should appear."""
    code = '''
for file in csv_files:
    df = pd.read_csv(file)
'''
    files = _extract_file_reads(code)
    for f in files:
        assert "CSV files" not in f, f"Generic 'CSV files' label found: {f}"


def test_file_read_fstring_path_preserved():
    """F-string template variables should be preserved, not replaced with *."""
    code = '''
df = pd.read_excel(f"C:\\Users\\{user}\\Documents\\{report_name}.xlsx")
'''
    files = _extract_file_reads(code)
    found = [f for f in files if ".xlsx" in f]
    assert len(found) == 1, f"Should detect f-string path, got {files}"
    assert "{user}" in found[0] or "{report_name}" in found[0], \
        f"F-string variables should be preserved, got {found[0]}"
    assert "*" not in found[0], f"Should NOT have * placeholder, got {found[0]}"


# ---------------------------------------------------------------------------
# File Write Detection
# ---------------------------------------------------------------------------

def test_file_write_to_excel():
    """AppleLaunch: DF.to_excel("Raw.xlsx")."""
    code = '''
DF.to_excel("Raw.xlsx")
'''
    files = _extract_file_writes(code)
    assert "[excel]Raw.xlsx" in files


def test_file_write_to_csv():
    """Google Reviews: df.to_csv("Store_id_map_v2.csv")."""
    code = '''
df.to_csv("Store_id_map_v2.csv")
'''
    files = _extract_file_writes(code)
    assert "[csv]Store_id_map_v2.csv" in files


def test_file_write_to_csv_with_full_path():
    """to_csv with full path should preserve directory."""
    code = r'''
updated_data.to_csv(r"D:\Projects\AppleLauch\FinalData.csv", index=False)
'''
    files = _extract_file_writes(code)
    found = [f for f in files if "FinalData.csv" in f]
    assert len(found) == 1, f"Expected CSV write detection, got {files}"
    assert "D:" in found[0] or "Projects" in found[0], f"Full path expected, got {found[0]}"


def test_file_write_refresh_all_pattern():
    """CopyTest: Workbooks.Open + RefreshAll = Excel write."""
    code = r'''
wb = excel.Workbooks.Open(r"\\MX-SHARE\Users\METOMX\Desktop\query.xlsb")
wb.RefreshAll()
time.sleep(60)
wb.Save()
'''
    files = _extract_file_writes(code)
    found = [f for f in files if "query.xlsb" in f]
    assert len(found) == 1, f"RefreshAll+Save pattern should detect write, got {files}"
    assert "[excel]" in found[0]


def test_file_write_saveas():
    """SaveAs to Excel file."""
    code = r'''
wb.SaveAs(r"C:\Users\Admin\Desktop\output.xlsx")
'''
    files = _extract_file_writes(code)
    found = [f for f in files if "output.xlsx" in f]
    assert len(found) == 1
    assert "C:" in found[0] or "Users" in found[0], f"Full path expected, got {found[0]}"


# ---------------------------------------------------------------------------
# PDF Detection
# ---------------------------------------------------------------------------

def test_pdf_adobe_com_with_path():
    """PdfREADER.py: AcrobatSDIWindow + literal .pdf path."""
    code = r'''
hwnd = win32gui.FindWindow("AcrobatSDIWindow", None)
pdf_path = r"\\METO-MX02\Users\METOMX\Desktop\PSI\test.pdf"
subprocess.Popen([pdf_path], shell=True)
'''
    files = _extract_file_reads(code)
    found = [f for f in files if "test.pdf" in f]
    assert len(found) == 1, f"Expected PDF detection via Adobe COM, got {files}"
    assert "[pdf]" in found[0]
    assert "METO-MX02" in found[0] or "PSI" in found[0], f"Full path expected, got {found[0]}"


def test_pdf_no_generic_label():
    """PdfReader import without file path should produce NO entry."""
    code = '''
from PyPDF2 import PdfReader
reader = PdfReader(open(file_path, "rb"))
'''
    files = _extract_file_reads(code)
    for f in files:
        assert "PDF files" not in f, f"Generic 'PDF files' label found: {f}"


def test_pdf_reader_with_literal_path():
    """PdfReader with literal path should extract it."""
    code = '''
from PyPDF2 import PdfReader
reader = PdfReader(open("C:/reports/monthly_summary.pdf", "rb"))
'''
    files = _extract_file_reads(code)
    found = [f for f in files if "monthly_summary.pdf" in f]
    assert len(found) == 1, f"Expected PdfReader path extraction, got {files}"
    assert "[pdf]" in found[0]


def test_pdf_fitz_open_with_path():
    """fitz.open("path.pdf") should extract."""
    code = '''
import fitz
doc = fitz.open("/data/documents/report.pdf")
'''
    files = _extract_file_reads(code)
    found = [f for f in files if "report.pdf" in f]
    assert len(found) == 1, f"Expected fitz.open detection, got {files}"


# ---------------------------------------------------------------------------
# URL Detection
# ---------------------------------------------------------------------------

def test_url_scraping_with_selenium():
    """AppleLaunch: Selenium + API URL -> web-scraping tag."""
    code = '''
from selenium import webdriver
driver = webdriver.Chrome(options=options)
driver.get(url)
url = 'https://agent.sec.samsung.net/api/v1/run/something?stream=false'
response = requests.post(url, headers=headers, json=data)
'''
    urls = _extract_url_reads(code)
    found = [u for u in urls if "agent.sec.samsung.net" in u]
    assert len(found) == 1, f"Expected Samsung agent URL, got {urls}"
    assert "[web-scraping]" in found[0], f"Should be web-scraping (Selenium present), got {found[0]}"


def test_url_download_without_scraping():
    """Alert_from_SQL.py: URL without scraping libraries -> web-download tag."""
    code = '''
dashboard = "https://app.powerbi.com/groups/me/apps/abc123/reports/xyz"
'''
    urls = _extract_url_reads(code)
    found = [u for u in urls if "app.powerbi.com" in u]
    assert len(found) == 1
    assert "[web-download]" in found[0], f"Should be web-download (no scraping), got {found[0]}"


def test_url_serpapi():
    """Google Reviews: SerpAPI URL without scraping libs."""
    code = '''
url = "https://serpapi.com/search"
params = {"api_key": "...", "engine": "google_maps", "q": query}
content = get_content(url, params=params)
'''
    urls = _extract_url_reads(code)
    found = [u for u in urls if "serpapi.com" in u]
    assert len(found) == 1


def test_url_no_generic_selenium_label():
    """Selenium without URLs should produce NO entries."""
    code = '''
from selenium import webdriver
driver = webdriver.Chrome()
driver.get(target_url)  # variable, not literal
'''
    urls = _extract_url_reads(code)
    for u in urls:
        assert "Selenium" not in u, f"Generic 'Selenium' label found: {u}"
        assert "Web scraping" not in u, f"Generic 'Web scraping' label found: {u}"


def test_url_no_generic_beautifulsoup_label():
    """BeautifulSoup without URLs should produce NO entries."""
    code = '''
from bs4 import BeautifulSoup
soup = BeautifulSoup(html_content, "html.parser")
rows = soup.find_all("tr")
'''
    urls = _extract_url_reads(code)
    for u in urls:
        assert "Web scraping" not in u, f"Generic 'Web scraping' label found: {u}"
        assert "BeautifulSoup" not in u


def test_url_pac_proxy_skipped():
    """PAC proxy URLs (.pac, .dat) should be filtered out."""
    code = '''
pac_url = "http://111.101.30.112/sge.pac"
response = requests.get(pac_url)
'''
    urls = _extract_url_reads(code)
    for u in urls:
        assert "111.101.30.112" not in u, f"PAC proxy URL should be skipped: {u}"


def test_url_localhost_skipped():
    """localhost / 127.0.0.1 should be skipped."""
    code = '''
url = "http://localhost:8000/api/data"
url2 = "https://127.0.0.1:5000/health"
'''
    urls = _extract_url_reads(code)
    assert len(urls) == 0, f"Localhost URLs should be skipped, got {urls}"


# ---------------------------------------------------------------------------
# _normalize_file_path
# ---------------------------------------------------------------------------

def test_normalize_path_backslashes():
    """Double backslashes should be normalized to forward slashes."""
    assert _normalize_file_path("C:\\\\Users\\\\Admin\\\\file.xlsx") == "C:/Users/Admin/file.xlsx"


def test_normalize_path_single_backslash():
    """Single backslashes (from r-strings) should be normalized."""
    assert _normalize_file_path("C:\\Users\\Admin\\file.xlsx") == "C:/Users/Admin/file.xlsx"


def test_normalize_path_forward_slash():
    """Forward slashes should be preserved."""
    assert _normalize_file_path("D:/Projects/AppleLauch/Raw.xlsx") == "D:/Projects/AppleLauch/Raw.xlsx"


def test_normalize_path_fstring_preserved():
    """F-string template variables should be kept."""
    result = _normalize_file_path("C:\\Users\\{user}\\{file}.xlsx")
    assert "{user}" in result
    assert "{file}" in result
    assert "*" not in result


def test_normalize_path_unc():
    """UNC paths should be normalized."""
    result = _normalize_file_path("\\\\MX-SHARE\\Users\\METOMX\\file.xlsx")
    assert "MX-SHARE" in result
    assert "METOMX" in result


# ---------------------------------------------------------------------------
# No [text] category
# ---------------------------------------------------------------------------

def test_no_text_category():
    """read_fwf should produce [csv], NOT [text]."""
    code = '''
df = pd.read_fwf("C:/data/fixed_width_file.dat")
'''
    files = _extract_file_reads(code)
    for f in files:
        assert not f.startswith("[text]"), f"[text] category should not exist: {f}"
    found = [f for f in files if "fixed_width_file.dat" in f]
    assert len(found) == 1
    assert found[0].startswith("[csv]"), f"read_fwf should produce [csv], got {found[0]}"


# ---------------------------------------------------------------------------
# Integration: full script patterns from real team scripts
# ---------------------------------------------------------------------------

def test_integration_apple_launch():
    """AppleLaunch main.py - Selenium + Excel COM + API + CSV write."""
    code = r'''
import subprocess
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import win32com.client
import pandas as pd
import requests
from pathlib import Path

def search_with_chrome_headless_shell(url):
    driver = webdriver.Chrome(options=options)
    driver.get(url)
    return element.text

excel = win32com.client.DispatchEx("Excel.Application")
wb = excel.Workbooks.Open("D:\Projects\AppleLauch/Raw - Copy.xlsx", UpdateLinks=0)
sheet = wb.Sheets("Sheet1")
data = sheet.UsedRange.Value
DF = pd.DataFrame(data)
wb.Close(SaveChanges=False)
DF.to_excel("Raw.xlsx")

wb = excel.Workbooks.Open("D:\Projects\AppleLauch/Raw.xlsx", UpdateLinks=0)
url = 'https://agent.sec.samsung.net/api/v1/run/be21db2b?stream=false'
headers = {'x-api-key': 'sk-secret'}
response = requests.post(url, headers=headers, json=data)
'''
    reads = _extract_file_reads(code)
    writes = _extract_file_writes(code)
    urls = _extract_url_reads(code)
    sql_reads = _extract_read_tables(code)
    sql_writes = _extract_write_tables(code)

    # File reads: two Workbooks.Open calls
    excel_reads = [f for f in reads if "[excel]" in f]
    assert len(excel_reads) >= 2, f"Expected 2+ Excel reads, got {excel_reads}"
    assert any("Raw - Copy.xlsx" in f for f in excel_reads)
    assert any("Raw.xlsx" in f and "Copy" not in f for f in excel_reads)

    # File writes: to_excel
    assert any("Raw.xlsx" in f for f in writes)

    # URLs: agent.sec.samsung.net with web-scraping tag (Selenium present)
    assert any("agent.sec.samsung.net" in u for u in urls)
    assert any("[web-scraping]" in u for u in urls)

    # No SQL operations
    assert len(sql_reads) == 0
    assert len(sql_writes) == 0

    # No generic labels
    for f in reads | writes:
        assert "Excel files" not in f
        assert "CSV files" not in f
    for u in urls:
        assert "Selenium" not in u
        assert "Web scraping" not in u


def test_integration_alert_from_sql():
    """Alert_from_SQL.py - SQL read + Power BI URL."""
    code = '''
from sqlalchemy import create_engine
import pandas as pd
import win32com.client as w

engine = create_engine("postgresql+psycopg2://metomx:123456789@111.101.50.135:5432/postgres")
df = pd.read_sql("SELECT * FROM bi_reporting.agreed_price_alert", engine)
df['Impacted Amount'] = (df['Agreed Price'] - df['Net Price (USD)']) * df['Sales Qty']

dashboard = "https://app.powerbi.com/groups/me/apps/4cc7a6e1/reports/168b8cfc"
outlook = w.Dispatch("Outlook.Application")
mail = outlook.CreateItem(0)
mail.htmlbody = f"<a href={dashboard}>Dashboard</a>"
'''
    sql_reads = _extract_read_tables(code)
    urls = _extract_url_reads(code)

    assert "bi_reporting.agreed_price_alert" in sql_reads
    assert any("app.powerbi.com" in u for u in urls)


def test_integration_etl_pipeline():
    """etl_sales_pipeline.py - full ETL with reads, writes, refresh, truncate."""
    code = '''
import pandas as pd
from sqlalchemy import create_engine

engine = create_engine("postgresql://user:pass@db-host:5432/analytics")

query = """
    SELECT o.*, c.customer_name, c.region
    FROM staging.orders o
    JOIN staging.customers c ON c.id = o.customer_id
    WHERE o.order_date >= CURRENT_DATE - INTERVAL '7 days'
"""
df = pd.read_sql(query, engine)
df["revenue"] = df["quantity"] * df["unit_price"]

df.to_sql("fact_sales", engine, schema="analytics", if_exists="append", index=False)

with engine.connect() as conn:
    conn.execute("REFRESH MATERIALIZED VIEW analytics.mv_sales_summary")
    conn.execute("TRUNCATE TABLE staging.temp_sales_buffer")
'''
    sql_reads = _extract_read_tables(code)
    sql_writes = _extract_write_tables(code)

    # Reads
    assert "staging.orders" in sql_reads
    assert "staging.customers" in sql_reads

    # Writes
    assert "analytics.fact_sales" in sql_writes, f"to_sql with schema= should resolve. Got {sql_writes}"
    assert "analytics.mv_sales_summary" in sql_writes
    assert "staging.temp_sales_buffer" in sql_writes


def test_integration_pdf_reader():
    """PdfREADER.py - Adobe COM automation with PDF path."""
    code = r'''
import win32clipboard
import win32gui
import win32api
import win32con

def extract_text_from_pdf_background(pdf_path, num_pages=28):
    import subprocess
    subprocess.Popen([pdf_path], shell=True)
    hwnd = win32gui.FindWindow(None, "Adobe Reader")
    if not hwnd:
        hwnd = win32gui.FindWindow("AcrobatSDIWindow", None)
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)

def main():
    pdf_path = r"\\METO-MX02\Users\METOMX\Desktop\PSI\test.pdf"
    text = extract_text_from_pdf_background(pdf_path, 28)
'''
    files = _extract_file_reads(code)

    # Should detect the PDF path via Adobe COM pattern
    pdf_files = [f for f in files if "[pdf]" in f]
    assert len(pdf_files) >= 1, f"Expected PDF detection, got {files}"
    assert any("test.pdf" in f for f in pdf_files)

    # No generic "PDF files" label
    for f in files:
        assert "PDF files" not in f


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

    passed = 0
    failed = 0
    errors = []
    for test in tests:
        try:
            print(f"Running {test.__name__}...")
            test()
            print(f"  PASSED")
            passed += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            errors.append((test.__name__, str(e)))
            failed += 1

    print(f"\n{'='*60}")
    print(f"{passed} passed, {failed} failed out of {len(tests)} tests")
    if errors:
        print(f"\nFailures:")
        for name, err in errors:
            print(f"  {name}: {err}")
    sys.exit(1 if failed > 0 else 0)
