# Read-only data auditor

Open **Data Quality → AI Auditor**. Unlock with the dedicated operator key,
choose approved flows, enable auditing and save. **Run now** works independently
of the optional overnight schedule. Dates and schedule times use Dubai time.
Disable cancels active work; **Stop audit** cancels this audit and leaves the
schedule enabled. An interrupted process does not resume old model requests.

The feature is **disabled by default**. Administrator setup is required before
the controls unlock. This release does not provision database access, start a
model server, or change permissions on an existing database.

## What it checks

The host inspects completed successful runs, independently counts the retained
final CSV records, verifies checksums, and collects approved column aggregates.
It checks row drops/increases, missing planned periods/categories, growing empty
values, invalid numeric/date values, new duplicate keys, totals and date
regressions. SQL row/total comparisons require an approved, exclusive replace
target, a matching source identity and a stable completed run. A newer run,
append target, or standby without a proven replay boundary makes that comparison
**unverified**. Current SQL aggregates may still be inspected.

History needs four different Dubai dates on the same weekday, with matching
reporting scope, schema and reader policy. Initial row counts must all fall
within 15% of their median. That baseline stays pinned: repeated bad data does
not become normal. A 30% decrease or 100% increase raises a possible
inconsistency. These thresholds are explicit heuristics, not proof of an error.
Changing the administrator policy creates a new baseline namespace. Review that
change; it can invalidate historical comparisons. Pinned baselines do not learn
holidays, business changes or seasonality beyond weekday matching.

Qwen can request only `read_profile(dataset_id, run_id, source)`. The host checks
every ID against this audit's approved scope. There is no model SQL, expression,
file path, URL, shell, code, browser or write tool. Candidate IDs must reference
findings validated by numerical checks. Model prose and invented figures never
become alerts. Verified findings remain available when the model review fails.
This is a constrained investigator; it is not general autonomous SQL analysis.

Findings appear in the existing Alerts/Actions view and the auditor's evidence
view. Unchanged findings are deduplicated across runs. Human acknowledgement and
resolution are preserved; Qwen cannot close alerts. A separate deduplicated
coverage alert identifies incomplete inspections. This release creates in-app
alerts, with no new email, chat recipient or external notification integration.

## Security boundary

| Component | Authority |
| --- | --- |
| Qwen inference service | Receives approved aggregates and returns text/native tool-call JSON. No Metronome credentials, files, database connection, or callable application tools. |
| Trusted Metronome coordinator | Publishes sanitized manifests, verifies tool requests/evidence and records its own audit profiles, history and alerts. These bounded host writes are required to retain findings. |
| Separate reader service | Reads only its sanitized manifest, approved CSV files and approved PostgreSQL aggregates. No imports of the Metronome application or uploader configuration. |
| Operator | Can enable, select approved flows, schedule and stop using a separate key over HTTPS or loopback. Existing unrestricted local-access helpers are not used. |

The reader never receives the application's SQLite database. The trusted host
publishes a separate manifest with opaque scope hashes and necessary file
metadata, excluding operational secrets, report URLs and raw log output. The
reader opens this database with SQLite `mode=ro`, `query_only=ON` and
`trusted_schema=OFF`. It refuses the application database's schema. CSV paths
come only from this manifest. Canonical handle paths, roots, links, hard links,
file identity, byte/row budgets and checksums are checked. Hashing observes the
same byte stream as parsing. Workbook macros, links and programs are never run.

PostgreSQL uses a **separate explicitly configured identity**. Missing host,
port, database, username or password fails closed. No uploader or service-file
fallback exists. Remote PostgreSQL requires `sslmode=verify-full`. Before every
inspection it rejects elevated roles, any role membership (including paths to
`SET ROLE`), ownership, effective table/column write grants, PUBLIC or inherited
CREATE/TEMP, sequence powers and executable user functions. This strict policy
can require a separately prepared database: revoking PUBLIC privileges in a
shared database can affect other applications, so provisioning belongs to its
administrator.

Only ordinary heap tables and built-in scalar types are supported. Views,
foreign/partitioned/inherited tables, RLS, rules, generated columns, custom
types, expression/partial indexes and custom index operator classes are rejected.
Approved definitions are pinned and checked again under an AccessShare lock.
All queries are fixed templates, identifiers come from protected policy, values
are parameterized, and the transaction is read-only with a fixed `pg_catalog`
search path. There is no commit path. Read-only transactions supplement the
privilege and template restrictions; PostgreSQL read-only mode alone permits
some temporary-object operations. See [PostgreSQL transactions](https://www.postgresql.org/docs/current/sql-set-transaction.html)
and [privileges](https://www.postgresql.org/docs/current/sql-grant.html).

**OS and network isolation remain deployment requirements.** Run the reader as
a distinct service account/container with read-only mounts/ACLs on the manifest,
policy and approved artifacts, no application database or credential-directory
access, no writable shared directories, no container-engine socket, no elevated
capabilities and no permission to start other processes. Give it network access
only to its PostgreSQL server; accept requests only from the coordinator. Run
inference under another identity with model weights mounted read-only, no
application mounts or credentials, prompt logging/persistent prompt caches off,
and no network route to the reader, Metronome, databases or the internet. Permit
the coordinator to call the fixed inference endpoint. Enforce these restrictions
with the host firewall/container network policy, not a model instruction.
The application cannot impose isolation on a separately managed model server.
Database and operating-system administrators remain trusted.

## Administrator configuration

Create a dedicated manifest directory writable by Metronome and readable by the
reader. Use canonical absolute paths. Do not point it at an existing application
database. The reader needs the same artifact paths represented in the manifest;
unavailable remote/private files are reported as unverified.

Set these in **Metronome's protected service environment**, then restart:

| Variable | Value |
| --- | --- |
| `METRONOME_AUDIT_MANIFEST_PATH` | Absolute path to a new dedicated `audit-manifest.sqlite` file. |
| `METRONOME_AUDIT_FLOW_IDS` | JSON array of approved numeric Flow IDs, e.g. `[12, 19]`. |
| `METRONOME_AUDIT_READER_URL` | Fixed reader origin; HTTPS except for loopback. |
| `METRONOME_AUDIT_READER_TOKEN` | Random secret of at least 32 characters, shared only with the reader. |
| `METRONOME_AUDIT_OPERATOR_TOKEN` | Different random secret of at least 32 characters for UI operators. |
| `METRONOME_AUDIT_MODEL_URL` | Fixed complete chat-completions URL, e.g. `http://127.0.0.1:8000/v1/chat/completions`. |
| `METRONOME_AUDIT_MODEL` | Served model name; default `Qwen/Qwen3.8-27B`. |
| `METRONOME_AUDIT_MODEL_TOKEN` | Separate inference-service credential when required. |

Settings are independent of the general AI settings page and cached until
restart. Tokens must be distinct. The operator key stays in tab memory and is
cleared from its input immediately; it is never saved in browser storage.
Configure trusted reverse-proxy forwarding correctly when terminating HTTPS.

Set these in the **reader's separate environment**:

| Variable | Value |
| --- | --- |
| `METRONOME_AUDIT_READER_POLICY` | Absolute path to the administrator-owned policy JSON below. |
| `METRONOME_AUDIT_READER_TOKEN` | The coordinator/reader secret. |
| `METRONOME_AUDIT_CATEGORY_KEY` | Another random secret, at least 32 characters, for pseudonymous category tokens. Rotating it invalidates historical comparisons. |
| `METRONOME_AUDIT_READER_DSN` | Explicit restricted PostgreSQL DSN, needed only for SQL inspection. Never use the uploader identity. |

Example policy (fictional paths and IDs; adjust for the reader's host):

```json
{
  "manifest_db": "/srv/audit/audit-manifest.sqlite",
  "artifact_roots": ["/srv/retained-exports"],
  "datasets": [{
    "id": "weekly_sales",
    "flow_id": 12,
    "columns": [
      {"name": "region", "csv_header": "Region", "kind": "text"},
      {"name": "units", "csv_header": "Units", "kind": "number"}
    ],
    "schema_name": "reporting",
    "table_name": "weekly_sales",
    "source_server": "warehouse.example.test:5432",
    "fingerprint": "",
    "exclusive_replace_target": false,
    "period_comparison": "exact",
    "transformation_revision": ""
  }]
}
```

An empty fingerprint leaves SQL inspection disabled. With the dedicated reader
identity configured, `python -m auditor_reader weekly_sales` prints a safety-
checked fingerprint; it saves nothing. An administrator reviews the target and
places that fingerprint in the policy. Set `exclusive_replace_target=true` only
when that Flow is the sole writer, and `source_server` exactly matches the frozen
Flow server identity. An append target or lagging replica never gets this
equivalence assumption from the model.

`period_comparison="exact"` requires the same reporting periods.
`"completed_windows"` additionally allows equally shaped windows of completed
ISO weeks; partial/unrecognized weeks retain exact matching. Source filters
remain part of scope. Version transformed datasets with `transformation_revision`
covering dependencies/environment as well as the recorded script checksum.
Legacy transformed runs without a checksum cannot initialize a baseline.
The normal worker records this optional checksum; it does not add a quality gate.

Run one reader process from this checkout and its locked Python environment:

```text
python -m uvicorn auditor_reader.service:app --host 127.0.0.1 --port 8091 --workers 1 --no-access-log
```

For separate hosts, place the reader behind authenticated TLS and firewall the
listener to the coordinator. Do not expose it publicly. No example secret is
provided or committed. Model-side native function parsing must support Qwen's
tool-call format; see the [Qwen model card](https://huggingface.co/Qwen/Qwen3.8-27B).

## Limits and recovery

- One audit and one reader inspection at a time. At most 120 reader calls,
  16 MiB retained evidence in a run, eight model turns and four calls per turn.
  Model output is bounded to 4,096 tokens per turn and 512 KiB per response.
- Manual audits have a 15-minute wall budget; overnight audits have two hours.
  Reader operations have a 120-second ceiling; the entire SQL transaction has
  a 30-second ceiling and a one-second lock wait. A cancelled host request also
  cancels the reader query; late results cannot save profiles or findings.
- Read queries still consume I/O and may briefly delay writers. Use an isolated
  reporting database/replica for expensive datasets. Without a replay watermark,
  replica results cannot prove insertion completeness for a specific run.
- CSV input is capped at 512 MiB and five million rows per bundle by default,
  with at most 64 files and 12 approved columns. Ragged rows, changed checksums,
  missing files, mixed schemas and unsupported formats remain unverified.
- Exact distinct values stop being reported above 100,000 CSV categories.
  Missing-category checks require complete groups (at most 256). Categories use
  keyed tokens; raw labels and records are not sent to Qwen. Numeric parsing
  does not infer locale/currency conversions.
- A missed overnight time is caught up once within two hours. Longer outages
  advance to the next night; use Run now to inspect immediately. A restart marks
  unfinished work interrupted. Enabling alone never starts a run.
- Correct counts/totals cannot establish overall data accuracy. Swapped values,
  business-rule errors, bad historical baselines and unapproved fields may go
  undetected. Synthetic tests exercise behavior and containment; they are not
  an accuracy benchmark for real Qwen weights.

After a reader/model outage, repair its protected configuration and use Run now.
For definition drift, have the administrator review and repin the changed table;
never bypass the check. Audit profiles remain in Metronome for historical
comparison even after normal artifact retention removes the CSV. Capacity and
retention of this audit metadata belong to the host administrator.
