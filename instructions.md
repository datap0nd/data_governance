# How to Run — Step by Step (Windows)

## 1. Install Python

- Go to https://www.python.org/downloads/
- Download Python 3.12 or 3.13
- Run the installer
- **Check the box "Add Python to PATH"** at the bottom of the first screen
- Click "Install Now"

To verify, open PowerShell and type:
```
python --version
```
You should see something like `Python 3.12.x`.

## 2. Download the project

- Go to https://github.com/datap0nd/data_governance
- Click the green **"<> Code"** button
- Click **"Download ZIP"**
- Extract the ZIP to `C:\Users\r.cunha\documents\Home\projects\data_governance\`

This creates the project at:
```
C:\Users\r.cunha\documents\Home\projects\data_governance\data_governance-main\
```

**Note:** In all commands below, replace `r.cunha` with your actual Windows username.

## 3. Install dependencies

Open PowerShell and run:
```
cd C:\Users\r.cunha\documents\Home\projects\data_governance\data_governance-main
pip install -r requirements.txt
```

You only need to do this once (or again if `requirements.txt` changes).

## 4. Run the tests

```
cd C:\Users\r.cunha\documents\Home\projects\data_governance\data_governance-main
python tests/test_scanner.py
```

You should see 6 tests pass. This confirms the TMDL scanner works.

## 5. Start the app

```
cd C:\Users\r.cunha\documents\Home\projects\data_governance\data_governance-main
$env:DG_TMDL_ROOT = "C:\Users\r.cunha\documents\Home\projects\data_governance\data_governance-main\test_data"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

When you see `Uvicorn running on http://0.0.0.0:8000`, the app is running.

## 6. Open the panel

Open your browser and go to:
```
http://localhost:8000
```

## 7. Run a scan

- Click **Scanner** in the top nav
- Click **Run Scan Now**
- It should find 3 reports and 4 sources

Then click through the other pages:
- **Dashboard** — overview with source/report counts
- **Sources** — all detected data sources (SQL Server, Excel, CSV)
- **Reports** — all Power BI reports found
- **Lineage** — which sources feed which reports

## 8. Stop the app

Press `Ctrl+C` in PowerShell.

## 9. Let other people access the panel

While the app is running, other people on the same network can open it at:
```
http://YOUR_COMPUTER_IP:8000
```

To find your IP, run `ipconfig` in PowerShell and look for your IPv4 address.

## Updating the project

When there's a new version:
1. Delete the old `data_governance-main` folder
2. Download the new ZIP from GitHub
3. Extract to the same `data_governance\` folder
4. Run `pip install -r requirements.txt` again (in case dependencies changed)
5. Start the app as in step 5

## Using with your real reports

Once testing works, point the app at your real TMDL exports:
```
cd C:\Users\r.cunha\documents\Home\projects\data_governance\data_governance-main
$env:DG_TMDL_ROOT = "C:\Users\r.cunha\documents\Home\projects\data_governance"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Your reports should be in:
```
C:\Users\r.cunha\documents\Home\projects\data_governance\reports\
├── Report_Name_1\
│   └── Report_Name_1.SemanticModel\
│       └── Definition\
│           └── Tables\
│               ├── TableA.tmdl
│               └── TableB.tmdl
├── Report_Name_2\
│   └── ...
└── ...
```
