# Flow Transformation Script Contract

Metronome can run one optional transformation script after Flow downloads and
before SQL insertion. The script runs once for each normalized CSV. ASAP HTML
and Plain text exports are download-only and cannot enable transformation or
SQL handoff. ASAP CSV supplies its normalized primary file, while its byte-exact
`_raw.csv` sibling remains an original artifact and is never passed to a script.

## Invocation

Metronome invokes the selected script with:

```text
script --input <downloaded-csv> --output <reserved-result-csv>
```

The same paths are available as environment variables:

- `METRONOME_FLOW_INPUT`
- `METRONOME_FLOW_OUTPUT`
- `METRONOME_FLOW_RESULTS_DIR`
- `METRONOME_FLOW_PERIODS` - JSON array containing the exact ordered period
  values assigned to the current artifact, for example
  `["2026-W22", "2026-W23", "2026-W24"]`.

Supported entry points are `.py`, `.ps1`, and `.exe`. Python and executable
entry points receive `--input` and `--output`. Python scripts run with the same
Python runtime as the Flows worker. PowerShell scripts run with
`powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass` and receive
equivalent named parameters `-InputPath` and `-OutputPath`.

## Required Behavior

- Read only the file identified by `--input` for the current invocation.
- Create exactly one non-empty CSV at the exact `--output` path.
- Exit with code `0` only after the output has been written completely.
- Return a non-zero exit code and a useful stderr message when transformation
  cannot finish.
- Do not delete or overwrite the input file.
- Do not choose a different output directory or filename.

Metronome reserves a collision-safe output under the download folder's
`script_results` subfolder. It validates and normalizes that CSV, records the
script duration, stdout, stderr, and resulting artifact, and passes only the
transformed artifacts to SQL when SQL handoff is enabled. With Direct-file
output, the download folder is in the worker-private artifact store and script
results are never published into the configured target; publication contains
only the validated configured download deliverables and happens before the
script runs.

## Minimal Python Example

```python
from argparse import ArgumentParser
from pathlib import Path

parser = ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()

source = Path(args.input)
target = Path(args.output)

# Replace this copy with the real transformation.
target.write_bytes(source.read_bytes())
```

## Minimal PowerShell Example

```powershell
param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputPath
)

# Replace this copy with the real transformation.
Copy-Item -LiteralPath $InputPath -Destination $OutputPath
```
