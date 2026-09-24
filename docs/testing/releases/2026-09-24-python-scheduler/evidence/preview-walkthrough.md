# Fictional Python scheduler preview walkthrough

Executed 2026-09-24 about 08:16–08:22 UTC in the local in-app browser, using
`app/static/recording-preview/python-scheduler.html` served from this checkout.
The preview replaced the API with in-memory fictional data; no script ran.

| Case | Viewport | Action and observed state |
| --- | --- | --- |
| U-01 | 1280×900 | New Python Flow selected **Just run the scripts**. The visible builder steps were **Python scripts** and **Schedule and owner**. Entered a name, script path and arguments; **Check script and Python** reported `1 of 1 script(s) readable` and the `py` launcher. **Next** opened Schedule and owner. Save created a fictional Flow. |
| U-01 recovery | 1280×900 | With the preview's validation-error setting, Save showed `Flow not saved: Python scripts must be .py files.`, focused Script 1 path and preserved its value and Flow name. Switching the fictional outcome to saved and clicking Save succeeded. |
| U-02 | 1280×900 | Run history showed Flow and Status filters, exit code and last stage. Selecting `failed` left only failed run #52. The run log showed stage **Loading stock**, progress 45%, a parent and two child scripts with PID/CPU/memory, and separate stdout/stderr console lines. Stop showed **CANCELLED** and said the worker remains available; Show failure showed **FAILED** and an edit/retry next action. |
| U-03 | 390×844 | `window.innerWidth` was 390 and document scroll width was 380. Run mode showed only Python scripts and Schedule and owner. Switching to file-producing mode restored Output and Email, making four visible steps; document scroll width remained 380. |

The production builder and list are rendered by `app/static/app.js` in this
preview. The script log panel in the preview uses fictional data; the separate
`tests/test_flow_run_log_live.mjs` exercises production console rendering and
incremental loading.
