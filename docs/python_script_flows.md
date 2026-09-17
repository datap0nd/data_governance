# Python-script Flows

Choose **Create flow > Python scripts** to run one or more Python scripts in
order on the Flows worker and keep what the last one writes. The Flow stores
the ordered list of absolute script paths, the final file type (CSV or Excel
`.xlsx`), a filename template, the same **Output files** mode as Outlook Flows,
an optional SQL handoff with the existing **Refresh materialized views** step,
and the shared owner, schedule and email settings. There is no browser, portal,
mailbox or source file: the scripts are the acquisition and the transformation.

The Flow appears in the **Python** group of the Flows list with its managed
folder under `<Flows root>/Python/<flow name>`. Its source adapter is
`python_script`; only workers that advertise that adapter claim its runs, so
an older worker never picks up a Python job.

## Script contract

Every script runs in place, never copied, with the worker's own interpreter
(`sys.executable`) and the current working directory set to the script's own
folder. The worker calls the scripts strictly in the saved order:

```text
python <script.py> --output <reserved-path>                          # first script
python <script.py> --input <previous-output> --output <reserved-path> # every later script
```

Required behavior for each script:

- Create a non-empty file at exactly the `--output` path and exit with code
  `0` only after it is written completely. Do not choose another directory or
  filename.
- From the second script on, read the previous step's output from `--input`.
- Return a non-zero exit code and a useful `stderr` message when the step
  cannot finish. The run fails with the script name, the step number and the
  last 4000 characters of `stderr`/`stdout`.
- Finish within 3600 seconds. A step that exceeds the timeout fails the run.

Intermediate outputs are reserved as
`<run folder>/steps/step-<n>-<script-stem>.csv`. The last script's `--output`
is the final deliverable `<run folder>/<rendered filename template>`, so the
final script must write the configured file type (`csv` or `xlsx`); earlier
scripts exchange whatever the next script expects, CSV by convention.

Before any script runs or a run folder is created, the worker checks that
every configured script exists and ends in `.py`; a missing or renamed script
fails the run closed. At most 20 scripts can be chained. Each step records the
script's SHA-256, exit code, duration, `stdout` and `stderr` in the run's
progress events for auditability. Every finished step emits its own
`python_step_complete` event, and a step that exits non-zero, writes no output
or times out emits `python_step_failed` with the same fields plus the error,
so a broken chain still leaves a structured record for each step that ran.

## Arguments and values

Each script row has an optional **Arguments** line and an optional **Values**
list. The worker adds the arguments to the command exactly as typed, right
after the script path and before Metronome's own `--input`/`--output` flags:

```text
python run_download.py -sheet T --output <reserved-path>                       # first script
python clean.py -sheet T --input <previous-output> --output <reserved-path>    # later script
```

Splitting follows Windows rules and involves no shell: whitespace separates
tokens, a double-quoted span keeps its spaces (`-in "C:\data\my file.xlsx"`),
a doubled quote inside a quoted span (`""`) is a literal quote, and
backslashes are ordinary characters. An unclosed quote is rejected when the
Flow is saved. Arguments are one line of at most 2000 characters. Argument
names must not collide with `--input` and `--output`, which Metronome always
appends after them. The tokens `{flow}` (the Flow name), `{run_id}` and
`{date}` (the run's Dubai calendar date, `YYYY-MM-DD`, the same date the
filename template renders) are replaced before splitting; quote a token whose
value may contain spaces (`-n "{flow}"`). Unknown `{...}` spans are passed
through as typed.

**Values** run the same script once per value, in order. Paste one value per
line (at most 200 per script, each a single line of up to 500 characters;
duplicates are kept because a repeat run may be wanted). For each run the
value replaces `{value}` in the arguments (`-sheet {value}`, or
`-sheet "{value}"` when values may contain spaces) or, when the arguments
carry no `{value}` token, is appended as one extra token after them, quoted
automatically when it contains spaces. With the rows

| Script | Arguments | Values |
| --- | --- | --- |
| `run_download.py` | `-sheet {value}` | `T`, `U`, `V` |
| `clean.py` | `-mode strict` | *(none)* |

the run expands to four script runs, `run_download.py -sheet T`,
`run_download.py -sheet U`, `run_download.py -sheet V` and
`clean.py -mode strict`, reported as "Running script 2 of 4: run_download.py
-sheet U". Step numbers, `METRONOME_FLOW_STEP` and `METRONOME_FLOW_STEPS`
count runs, not rows. `--input` is always the previous run's output (the
second run of a row receives the first run's file), and
`METRONOME_FLOW_INPUTS` lists every output of the previous row separated by
the platform path separator (`;` on Windows), so `clean.py` above can read all
three sheets. `METRONOME_FLOW_VALUE` carries the run's value, empty when the
row has none. A value that contains a double quote cannot sit inside a quoted
`{value}` span; that run fails closed with the value and arguments in its
`python_step_failed` event. The Flows list and the alert and delivery emails
label such a row `run_download.py -sheet (3 values)`.

Every run of the **last** row writes a deliverable, so values on the last row
produce one final file per value. For a file destination the filename
template must then contain `{value}` (the value with characters outside
`A-Z a-z 0-9 . _ -` replaced by `_`) or `{index}` (the run's 1-based position
within the last row); Save refuses a template without them, and a name that
still repeats gets a numbered suffix. All final files are validated, published
together under **Fixed file path**, inserted into the SQL table together,
listed in the run's email and counted in the progress bar. The run summary
reads `Ran 4 Python script run(s) and saved 3 file(s): ...`.

## Environment variables

Every step receives the worker's environment plus the variables below. Scripts
inherit the whole worker process environment, including any credentials the
worker service was started with (for example `DG_UPLOAD_PG*`), so only trusted
scripts should be configured.

| Variable | Value |
| --- | --- |
| `METRONOME_FLOW_OUTPUT` | The file the script must create (same as `--output`). |
| `METRONOME_FLOW_INPUT` | The previous run's output (same as `--input`); unset for the first run. |
| `METRONOME_FLOW_INPUTS` | Every output of the previous script row, joined with the platform path separator; unset for the first row. |
| `METRONOME_FLOW_VALUE` | The run's value from the Values list; empty when the row has none. |
| `METRONOME_FLOW_RESULTS_DIR` | The run's `steps` folder, where a script may write extra files. |
| `METRONOME_FLOW_STEP` | The 1-based number of this script run. |
| `METRONOME_FLOW_STEPS` | The number of script runs in the chain (rows expanded by their values). |
| `METRONOME_FLOW_OUTPUT_FORMAT` | `csv` or `xlsx`, the configured final file type. |
| `METRONOME_FLOW_NAME` | The Flow name. |
| `METRONOME_FLOW_RUN_ID` | The run ID. |
| `PYTHONIOENCODING` | `utf-8`. |

Script output is captured as UTF-8 text with undecodable bytes replaced, and
no console window is opened on Windows.

## Outputs

The Output section offers two destinations:

- **Final file · Excel or CSV** keeps the last script's file. Choose the file
  type, the filename template (tokens `{flow}`, `{date}`, `{index}` and
  `{value}`; the extension follows the file type; default `{flow}.csv` or
  `{flow}.xlsx`; `{value}` or `{index}` is required when the last script has
  several values) and the **Output files** mode. **Separate runs · keep last 3** keeps
  `#<run>_<dd-mm-yyyy>` folders under the Flow's `Downloads` folder;
  **Fixed file path · replace previous output** publishes only the validated
  final file into the target folder and keeps the run folder, its `steps`
  folder and recovery files in the private artifact store.
- **SQL table** inserts the final CSV into PostgreSQL with the existing
  append/replace modes, target and uppercase options. The file type is forced
  to CSV because SQL only loads CSV, and the output mode is forced to
  **Separate runs** so the CSV stays in the run folder for **Retry SQL** and
  is never published to a fixed file path. The optional **Refresh materialized
  views** step runs after PostgreSQL confirms the insertion commit, exactly as
  for other Flows, and **Retry view refresh** stays available when a view
  fails.

The final file is validated before delivery: its detected format must match
the configured type. A CSV bound for SQL is normalized in place with a strict
header row (the physical first row, no preamble, no blank or duplicated
headers, at least one data row); a BOM or a `;` delimiter is accepted and
normalized to UTF-8 comma-separated rows. A CSV kept as a file is delivered as
written. An `.xlsx` file must be a valid workbook container. A header-less CSV
bound for SQL fails the run.

## Scheduling, owner and alerts

The schedule (daily, weekly or monthly day-of-month, Dubai time), the owner
and the failure-alert and email-delivery settings are the shared blocks used
by every Flow. Runs go to the headless worker; no browser is opened, and
**Browser mode** does not apply.

## Recovery

- **Resume** is not available: a Python run is one ordered chain and is never
  partially replayed. Use **Run** to execute the scripts again.
- **Retry SQL** works because the final CSV is a saved artifact. When SQL
  insertion fails after the final CSV was validated, Retry SQL reuses that
  file without running the scripts again, subject to the usual size and
  checksum verification of the matching artifact store.
- **Retry view refresh** refreshes only the unfinished views from the frozen
  plan after a committed insertion.

## Path policy

System > Paths lists a `Python` subfolder beside ASAP, GSCM, Outlook, Local and
Web. While enforcement is off, scripts may live anywhere the worker service
account can read. When **Enforce paths** is on, every configured script must be
inside `<root>/Python`, and the Flow's target folder must be inside its own
managed folder; Save and queued jobs reject other locations. Scripts uploaded
through **Browse...** in the builder are staged under `<root>/Python/.uploads`
in unique directories, so enforcement accepts them as configured.
Scripts kept inside the Flow's own managed folder (for example its `Scripts`
subfolder) follow that folder when the Flow is renamed; the rename is refused
while another Flow still uses scripts from it.

## Minimal two-script example

Script 1 (`fetch_orders.py`) produces the first CSV from nothing:

```python
from argparse import ArgumentParser
from csv import writer
from pathlib import Path

parser = ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()

rows = [("order_id", "amount"), ("1001", "25.00"), ("1002", "40.50")]
with Path(args.output).open("w", newline="", encoding="utf-8") as handle:
    writer(handle).writerows(rows)
```

Script 2 (`clean_orders.py`) reads `--input`, adds a column and writes the
final file to `--output`:

```python
from argparse import ArgumentParser
from csv import DictReader, DictWriter
from pathlib import Path

parser = ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

with Path(args.input).open(newline="", encoding="utf-8") as source:
    rows = list(DictReader(source))
for row in rows:
    row["region"] = "AE"

with Path(args.output).open("w", newline="", encoding="utf-8") as target:
    out = DictWriter(target, fieldnames=["order_id", "amount", "region"])
    out.writeheader()
    out.writerows(rows)
```

Add both scripts in that order, choose **SQL table** or a final CSV file, and
Run. To fetch several sheets, type `-sheet {value}` as the first script's
arguments and one sheet name per line as its values; `clean_orders.py` then
receives the last fetched file as `--input` and all of them in
`METRONOME_FLOW_INPUTS`. A successful run reports
`Ran 2 Python script run(s) and saved 1 file(s): <file>` and, with SQL, the
committed row count and any refreshed views.
