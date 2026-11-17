# 2026-11-17: HTML report Flows point at any repository: test plan

- Change/PR: link in the PR's testing section. The owner asked to drop the
  list of allowed report IDs: anyone creating an HTML report Flow points it at
  whatever repository they have access to. **Settings > Flows > HTML reports**
  loses **Allowed report IDs**. The Flow builder's **Report ID** becomes a
  typed field that fills in from the repository's name.
- Code baseline: `main` at `148b1c0`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux container, Python 3.13 checkout-owned `.venv`,
  Node 22, Playwright with Chromium; synthetic fixtures only.

## What stays safe without the list

- The report ID is one plain word (letters, digits, `_`, `-`, `.`). It is
  checked in the browser and again on the server, and it can never add an
  argument of its own.
- The server still builds the whole command from Settings, never from the
  request.
- The controller decides whether it knows the report, and its exit code
  decides the run.
- One refresh per report ID at a time, dry runs and live confirmation are
  unchanged.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| A-01 | Configure the controller; create a Flow with a report ID no list ever named (`team-sales.v2_html`); create one with `two words` | The first is saved and passed as one `--report` argument; the second is refused with the message about the repository's name | `test_a_flow_names_any_report_once_the_controller_is_configured` |
| A-02 | Clear the controller script, make a scheduled run due, then damage a Flow's saved block | Manual and scheduled runs are refused with the reason kept on the Flow; the damaged Flow is refused as damaged | `test_each_run_rebuilds_the_command_and_fails_closed` |
| A-03 | The builder's preview with the server's exact command, for a report ID typed in the browser | The preview is the server's command with the typed ID in place of the stand-in | `test_each_placeholder_becomes_exactly_one_argument`, `node tests/test_html_reports_display.mjs` |
| A-04 | Node: the builder has a typed Report ID and no list; repository names `…/smp_sales_html.git`, `…/smp_sales_html/`, `…/two%20words` and `not a link` | The ID field is an input that keeps a saved ID. The names give `smp_sales_html`, `smp_sales_html`, empty and empty | `node tests/test_html_reports_display.mjs` |
| A-05 | Browser walk on fictional data: **Create flow > HTML report**, paste a repository link, open Controller, type `stock cover`, then a valid ID and another link, save | The ID fills in from the repository and the command follows it. The browser blocks the space. A typed ID is kept when the link changes. "Flow saved" appears, with no page errors | Console output; screenshot kept outside the repository |
| R-01 | HTML report, settings, Python Flow and remote control suites | All pass | The `verify` command below |

## Automated checks

```bash
python tools/check.py verify --test tests/test_html_reports.py --test tests/test_settings_registry.py --test tests/test_settings_flows.py --test tests/test_flow_python.py --test tests/test_remote_flow_control.py --syntax app/html_reports.py --syntax app/static/app.js --syntax app/settings_catalog_flows.py --syntax tests/test_html_reports_display.mjs
```

```bash
node tests/test_html_reports_display.mjs
```

Final-head CI is the full regression.

## Usability evidence

A-05 is the walk of the changed control. The decision a user makes moved from
picking an ID from a list to pasting the repository link, with the ID filled in
for them. The owner asked for this change in this task.

## Acceptance and cleanup

Accepted when A-01 to A-05 and R-01 pass and `Merge ready` is green. The start
of the app deletes the old **Allowed report IDs** value. Saved Flows keep their
report IDs and run unchanged. Rollback: revert the PR; Flows whose report ID
the restored list does not hold would then refuse to run until it is listed.
