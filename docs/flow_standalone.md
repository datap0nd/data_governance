# Standalone Flows

Managed saves, adoption and layout repair generate `Scripts/run_flow.py` and a
versioned `flow-config-<hash>.json`. The launcher imports the installed Metronome
code. It is an offline entry point, not a self-contained executable or backup of
the application. Keep installed Python and dependencies available. More actions
in the Flows list can check its status or regenerate it. Old configurations stay
on disk; user-owned `run_flow.py` files are never overwritten.

Run with the installed Python:

```
python "<flow folder>/Scripts/run_flow.py" --dry-run
python "<flow folder>/Scripts/run_flow.py" --headed
python "<flow folder>/Scripts/run_flow.py" --no-transform
python "<flow folder>/Scripts/run_flow.py"
python "<flow folder>/Scripts/run_flow.py" --no-sql
```

Dry-run reads the frozen configuration, prints a redacted summary and creates
nothing. Normal runs recheck on-disk path containment and folder ownership.
They use a separate `.metronome/standalone-profile`; initial interactive SSO may
be necessary. They never copy a running worker profile or take its browser lock.
Portal access, Outlook interaction and installed credential providers still need
to work under the caller's Windows account.

All saved execution settings are honored by default, including SQL,
transformations and browser mode. `--no-sql` and `--no-transform` are explicit
per-run overrides; `--headed`/`--headless` override the saved browser mode.
The worker and launcher call the same `execute_flow` runner for acquisition,
publication, transformation, SQL, artifact roles and source outcomes. Metronome
adds scheduling, progress transport, receipts and history around that runner.

SQL uses the same installed `DG_UPLOAD_*` configuration and loader as Metronome,
with credentials supplied by the launch environment. The bundle contains no
credentials or cookies. Use the same environment/account as the worker to reach
the same systems. A commit whose outcome is unknown requires inspection before
repeating an append; neither entry point silently repeats unknown commits.

Both offline and upgraded scheduled workers acquire process-lifetime locks for
the flow, physical output folder and exact SQL target. The default lock directory
is `%ProgramData%/Metronome/execution-locks` on Windows (the system temporary
directory on other hosts). All participating accounts must be able to use that
same directory. `DG_FLOW_LOCK_ROOT` may override it only if set identically for
every participant. An inaccessible/busy lock refuses the run. Locks release when
the process exits; do not delete lock files to bypass another process.

Offline runs use UUID-sized IDs outside the server sequence. Their JSONL logs are
under `Scripts/standalone-logs`; downloads and transformed artifacts follow the
saved output policy. Local/Outlook are explicit manual reprocesses. Server history,
source receipts, schedules and retention are not updated; offline runs perform no
retention deletion and their files require separate operator housekeeping.

Period windows, paths and filters are frozen when generated. Regenerate after
changing discovery or Paths settings. To resolve current configuration/periods
from an available local database without contacting the server, pass
`--refresh-db "<path to governance.db>"`; the database is opened read-only.
This option cannot be combined with dry-run. The launcher continues to require
compatible installed code. Reverting code does not remove generated artifacts.
