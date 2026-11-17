# HTML report refresh Flows

An **HTML report** is a static report kept in its own Git repository and
published as a site (for example GitHub Pages on the company's GitHub). Choose
**Create flow > HTML report** to refresh one on a schedule. The Flow appears in
the **HTML** group of the Flows list, and the report appears in **Reports** and
**Pipelines** next to the Power BI reports.

Metronome and the separate **dashboard refresh controller** split the work:

| Metronome | The controller |
| --- | --- |
| Holds the Flow: the report it refreshes, dry run or live, schedule, owner and alerts | Knows each report: where its data comes from and how it is built |
| Builds the command and starts the controller through the existing Python run-only worker | Extracts, validates and builds the report |
| Shows the controller's stages live and in run history, and keeps the original console | Commits and pushes to the report's existing GitHub repository |
| Lists the report in Reports, with its health, links, owner and schedule | Uses the Git sign-in of the Windows account it runs as |

Metronome never clones, pulls, builds or pushes a repository, never runs code
it fetched from GitHub, and holds no GitHub credentials.

## Setting up the worker PC

Do these once, in order, before creating a production Flow. Each step is done
by a person; Metronome automates none of it.

1. **Update Metronome** from `main` as usual. The database changes are
   additive and apply on start: `flows.html_report_json`,
   `flow_runs.stages_json`, and `reports.report_kind` (`powerbi` for every
   existing report), `refresh_flow_id`, `repository_url` and `site_url`.
   Existing Flows and reports are unchanged.
2. **Install the controller** on the worker PC, signed in as the BI desktop
   account: clone its repository into a folder of your choice (for example
   `D:\Tools\dashboard-refresh-controller`) and install its requirements into
   the Python the worker uses (**Settings > Folders > Default Python for script
   Flows**; set that to the controller's virtual environment if it has one).
3. **Clone the report repositories** under one folder, the *report root* (for
   example `D:\Reports\html`), and check that this account can push to them
   with its own Git sign-in (Git Credential Manager). The sign-in stays with
   Windows and Git.
4. **Fill in Settings > Flows > HTML reports** on the host PC (only the host
   PC can change them):
   - **Controller repository**: the link shown beside every HTML report Flow.
   - **Controller script on the worker**: the controller's `.py` file, for
     example `D:\Tools\dashboard-refresh-controller\refresh.py`.
   - **Report root on the worker**: the folder from step 3.
   - **Allowed report IDs**: the report identifiers from the controller's
     README. A Flow can refresh only a report listed here.
   - **Controller arguments**: how the controller takes a report, from its
     README. The default is `--report {report} --report-root {report_root}`.
   - **Added for a dry run**: the argument that builds and validates without
     publishing. The default is `--dry-run`; it cannot be empty.
   - **Added for a live run**: optional, for a controller that publishes only
     when asked (for example `--publish`).
5. **Validate before production.** Create the Flow under **Draft flows** as a
   **Dry run**, run it, and check its expanded log: every stage, the exit code
   and the console. Only then switch it to **Live**, move it to Production and
   schedule it.
6. **Updating the controller** is a `git pull` on the worker PC by a person,
   reviewed like any other code change. Metronome runs whatever copy is
   installed there.

## Creating a Flow

The builder has three steps:

1. **HTML report**: the Flow name (also the report's name in Reports), the
   **Report ID** from the allowed list, optional links to the **report
   repository** and the **published site**, **Dry run** or **Live**, and the
   time limit (1–1440 minutes, default 60).
2. **Controller**: read-only. The controller link and installed path, the
   report root, the Python used, the exact command each run starts, **Check
   controller and Python**, and anything missing in Settings.
3. **Schedule and owner**: the shared schedule, extra triggers, owner and
   failure alerts.

The form never sends a script path, arguments or a Python path. The server
builds the command from Settings and the chosen report when the Flow is saved,
and again when each run is queued.

## What a run does

```text
<worker's Python> <controller script> <controller arguments> <dry-run or live arguments>
```

`{report}` and `{report_root}` are filled in as one argument each, whether or
not the pattern already puts them in quotes; `{flow}`, `{run_id}` and `{date}`
are filled in by the worker as in every Python Flow and always stay one
argument. No shell is involved. The run is an ordinary **Just run the scripts** Python run (see
[python_script_flows.md](python_script_flows.md)): it starts in the signed-in
BI desktop session, gets the `METRONOME_FLOW_*` variables, honours the time
limit and **Stop**, and streams its console live.

- **Dry run** Flows always add the dry-run arguments. A **Live** Flow's
  **Run · publishes** button asks for confirmation first, and its **More >
  Dry run · no publishing** queues a one-off test that never publishes. A
  signed remote test of any HTML report Flow is always a dry run.
- Only one run per report ID can be queued or running at a time, across all
  Flows, because two controllers pushing one repository would race.
- A run that is still waiting for a worker is started again by **Run** only
  in the mode the Flow has now: a live run queued before the Flow became a dry
  run must be stopped first, and the other way round.
- A run does not start, and a scheduled run records why on the Flow, when the
  controller script is not set, the report is no longer allowed, the arguments
  need a report root that is not set, or the dry-run arguments are empty.
  Metronome never guesses another report or command.

## The controller's contract

The process **exit code decides the result**: `0` succeeded, anything else
failed. Stopping, a time limit or a worker failure also fail the run. Stage
events describe progress only; no event can make a run succeed.

The controller may report stages as **stdout** lines that begin with
`METRONOME_STAGE`, a space and one JSON object:

```text
METRONOME_STAGE {"version":1,"report":"smp_trade_in_value_html","stage":"Extract from meto_cloud","status":"started"}
METRONOME_STAGE {"version":1,"report":"smp_trade_in_value_html","stage":"Extract from meto_cloud","status":"succeeded","duration_seconds":42.4}
```

| Field | Rule |
| --- | --- |
| `version` | The number `1`. Another number is ignored and counted; anything else is malformed. |
| `report` | The run's report ID. An event for another report is ignored and counted. |
| `stage` | The stage name, at most 200 characters once cleaned. |
| `status` | `started` (or `running`) opens a stage; `succeeded` or `failed` completes it. |
| `duration_seconds` | Required on `succeeded` and `failed`: a number from 0 to 604800. |

Every field is untrusted. Only the five fields above are read; any other field
is ignored and never shown. Stage names lose control characters and anything
shaped like a credential (a user name or password in a URL, GitHub tokens,
`password=`, `token=`, `Bearer …`). A stage's start time is when the worker
captured the line, not a time the controller states. A completion without a
start is placed at its duration before the completion and marked so. A repeated
start is ignored and counted; a stage that starts again after it completed is
shown twice.

Lines on stderr, lines that do not begin with `METRONOME_STAGE`, JSON that
cannot be read, unknown statuses and completions without a valid duration are
not events. Malformed lines are counted and the run log says how many; the
original lines stay in the console. Never print a credential: the worker masks
known secret environment values, but the console keeps what was printed.

## Stages, history and timings

The expanded run log has an **HTML report refresh** section above the script
activity: the report ID, **Live · publishes to GitHub** or **Dry run · nothing
is published**, the site, repository and controller links, the result from the
exit code, the total duration of the run, and a table of each stage with its
status, start time (Dubai) and elapsed time. While the run is active the table
updates every two seconds and a running stage counts up. When the run ends, a
stage still open is shown as **Did not finish**.

The run log adds a notice when malformed or other-report events were ignored,
when console lines were dropped (stage events may be missing), when no stage
was reported, when the run succeeded but a stage reported failure or never
finished, and when every stage succeeded but the run failed or was stopped. A
successful live run says that GitHub Pages may still be deploying: a push is
not a deployment, so open the site to confirm the new version. Run history
shows one line per run, for example `Live · publishes · 3 of 4 stage(s)
succeeded · failed at Validate data`.

The parsed record is stored once per run in `flow_runs.stages_json` as lines
arrive (at most 100 stages), so it survives the console's own retention.

## Reports and lineage

Saving an HTML report Flow creates or updates its entry in **Reports**: kind
**HTML**, the Flow's name, repository and site links, the Flow's schedule in
the **Schedule** column and the Flow owner as report owner when none is set.

- **Data health** is the latest finished refresh (succeeded is healthy, failed
  or stopped is degraded), or unknown before the first one.
- **Last refresh** is the latest finished refresh; **last published** is the
  latest successful **live** refresh. Dry runs publish nothing. Both count only
  runs of the report ID the Flow refreshes now, so a Flow moved to another
  report does not lend it its earlier history.
- A Flow name that another report already uses is refused, so a Power BI report
  and an HTML report are never merged. Deleting the Flow archives its report
  with its links, documentation and tasks; a new Flow of the same name takes
  that archived entry back.
- The Power BI sync never matches, refreshes or archives an HTML report, Power
  BI usage is never attributed to one, and the report scanner skips a Power BI
  report whose name an HTML report holds (the scan log says so).
- **Pipelines** shows the chain *controller → Flow → HTML report → published
  site*. **Refresh report** and **Refresh full pipeline** are for Power BI
  reports; an HTML report is refreshed by its Flow. Metronome does not read the
  controller's own data sources, so an HTML report's lineage starts at its
  Flow.

## Access

Everyone can read Settings; only the host PC changes the HTML report settings.
As for every Flow, a signed-in user creates one for themselves, only its owner
or the host PC can edit, pause or delete it, and anyone may run it or read its
history.

## Rollback

Pause every HTML report Flow before reverting: older code treats one as a plain
run-only Python Flow and would run the command saved with it, including a live
publish. Their entries in Reports would be treated as Power BI reports, so the
next Power BI sync would archive them. The added columns are inert for older
code; the controller and the report repositories are untouched.
