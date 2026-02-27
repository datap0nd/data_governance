# Pending Steps (Manual)

## For each of your ~60 Power BI reports:

### 1. Add Business Owner and Report Owner tables
- Open the report in Power BI Desktop
- Go to Transform Data (Power Query Editor)
- New Source > Blank Query
- Paste in formula bar: `= #table({"Business Owner"}, {{"Person Name"}})`
- Rename the query to `Business Owner`
- Repeat for Report Owner: `= #table({"Report Owner"}, {{"Person Name"}})`
- Rename to `Report Owner`
- Close & Apply
- Save the report

### 2. Place .pbix files in the reports folder
Copy (or save) all your .pbix files to:
```
C:\Users\r.cunha\documents\Home\projects\data_governance\BI Report Originals\
```
The scanner reads .pbix files directly — no TMDL export needed.

### 3. Run the scanner
- Start the app (run update.ps1)
- Go to Scanner page
- Click "Run Scan Now"
- Verify all reports, sources, and owners appear correctly
