# Outlook attachment flows

Choose **Create flow > Outlook** to acquire emailed flat files through the
signed-in user's desktop Outlook profile. The flow stores a free-text subject
substring, an existing target folder, an optional transformation, the existing
daily/weekly/monthly schedule, and the normal optional SQL handoff.

## Message selection

- Only the default profile's top-level Inbox is searched. Subfolders and other
  mailboxes are not included.
- Subject matching is a case-insensitive substring match.
- Messages are inspected newest first. Messages with no supported attachment
  are skipped. Supported files are `.csv`, legacy `.xls`/`.xlt`, binary
  `.xlsb`, and OOXML `.xlsx`/`.xlsm`/`.xltx`/`.xltm` workbooks.
- The newest message with supported data must have exactly one supported
  attachment. More than one fails with an actionable error. Other attachment
  types do not count.
- Excel add-ins (`.xla`, `.xlam`, `.xll`) and password-protected/encrypted
  workbooks are unsupported. Macro-capable workbooks are read as stored cell
  values only; Metronome never executes VBA.

The attachment identity is a SHA-256 receipt over the Outlook store, message,
attachment position, and original attachment name. Metronome advances that
receipt only after a producing run succeeds, including its SQL transaction. A
failed run remains eligible for safe reprocessing. An SQL-only retry cannot
roll receipt state backward past a newer successful message.

## No-op and storage behavior

A scheduled run succeeds as a no-op when no qualifying message exists or the
newest qualifying attachment matches the last successful receipt. A no-op does
not create/register a run folder, execute retention work, transform a file,
invoke SQL, or advance `last_success_at`. Pending retention work waits until the
next producing run. Clicking **Run** is an explicit force-reprocess operation,
including when it re-pokes an existing queued scheduled run.

After a new attachment is acquired locally, the worker creates the normal
`#<run_id>_<dd-mm-yyyy>` folder, registers it, applies the keep-newest-three
protocol, and uses the shared verified storage path. The original safe filename
is retained with collision suffixes. CSV is normalized in place; every Excel
format keeps the verified original and adds a `_normalized.csv` sibling used by
transformations and SQL.

## Flat-file validation

Outlook files do not use ASAP's preamble detection. The physical first row is
the header. A one-column file is valid, but blank or case-insensitively
duplicated headers, data beyond the declared headers, and header-only files are
rejected. Every populated Excel worksheet follows the same rule, and populated
worksheets must resolve to the same normalized header list before their rows
are streamed into one CSV.

## Desktop execution

The resident headless Flow worker still claims Outlook jobs and continues to
hold its persistent Edge profile lock. For acquisition it creates a distinct
interactive task named `Metronome_Outlook_Flow_<run_id>`, exchanges request and
result JSON with `tools/outlook_flow_attachment.ps1`, validates the returned
path and identity, and deletes only that task. The helper reads Outlook and
saves the selected attachment; it does not move, delete, flag, or mark mail as
read. The per-run task name cannot clobber the separate failure-alert email
task.
