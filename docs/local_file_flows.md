# Local-file Flows

Metronome Flows can use one exact CSV or Excel file as their source. Choose
**From file** when creating a Flow, enter an absolute local or UNC path, and
configure the same schedule, owner, transformation, and SQL append/replace
options used by other Flows.

The background worker reads the path with its Windows service account. There
is an optional path policy in System > Paths: when enforcement is enabled the
source must be under the root's Local directory. The account must also be able
to read the configured file. A missing, inaccessible, changing, encrypted, malformed,
or incorrectly labelled file fails before transformation or SQL starts.

## Supported files

- CSV: `.csv`. The worksheet field is not used.
- Legacy Excel: `.xls`, `.xlt`.
- Binary Excel: `.xlsb`.
- OOXML Excel: `.xlsx`, `.xlsm`, `.xltx`, `.xltm`.

Excel Flows require one worksheet title. Matching is exact and preserves case
and whitespace. Only that worksheet is loaded; physical row 1 supplies the
headers, headers must be non-blank and unique ignoring case, and at least one
data row is required. The declared extension must match the detected content
family. In particular, HTML or text renamed to `.xls` is rejected.

## Snapshots and unchanged files

A producing run keeps the exact source bytes under `source/<original-name>`
and writes a separate normalized CSV for transformation and SQL. These are
worker-private artifacts, not files published to a user-selected output
folder. The newest three producing snapshots are retained.

Scheduled and full-pipeline runs skip transformation and SQL when the source
identity is unchanged. A manual **Run** always forces another snapshot and
reprocesses it. An unchanged check creates no run folder and advances the last
execution-success timestamp without changing the last producing-success
timestamp.

Proving that a file is unchanged requires two complete reads whose byte count
and SHA-256 agree. Size and modification time can suggest that a file changed,
but cannot prove a no-op. Large workbooks on network shares therefore incur at
least two complete network reads on every scheduled check.

## Recovery

File-source runs cannot be resumed because each producing run is a single
atomic snapshot. If transformation or SQL fails after the normalized CSV is
saved, **Retry SQL** can reuse that private CSV while the receipt still matches
the Flow's current path, worksheet, configuration revision, and latest
successful identity. Changing the path or worksheet invalidates older retries.

## Appliance verification

After updating the Windows appliance, validate both a UNC CSV and workbook
using the real worker service account: scheduled processing, unchanged no-op,
manual force, SQL insertion, and an intentionally inaccessible-path failure.
