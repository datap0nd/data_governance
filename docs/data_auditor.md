# Automatic Flow auditor

Open **Data Quality → AI Auditor** on the computer running Metronome. The
auditor is on by default for every registered Flow and runs nightly at 02:00
Dubai time. Use **Run now**, **Change time**, **Pause/Resume auditor**, and
**Stop audit** directly; there is no Flow selector, auditor password, pairing,
or separate setup form.

## Installation and prerequisites

`setup.ps1` provisions and preserves two generated credentials outside the
repository and the application database. It installs
`MetronomeAuditorReader` on loopback under a Windows virtual service account,
publishes sanitized scope through a private exchange folder, and applies ACLs
that deny the reader access to `governance.db` and the host credential. Updates
preserve the reader token and category-hashing key.

The existing **System → AI** Local AI connection supplies the Qwen-compatible
inference endpoint. A local same-origin settings change updates the auditor's
non-secret endpoint/model trust pin. The model credential is never copied into
auditor settings, the manifest, policy, evidence, or prompts.

SQL inspection is available only when the existing `PGHOST`, `PGPORT`,
`PGDATABASE`, `PGUSER`, and `PGPASSWORD` identity is complete and passes the
reader's read-only privilege checks. Uploader credentials are never reused. The
recorded Flow server and database must match before the reader opens a
connection. Metronome does not alter database permissions; a write-capable
probe identity leaves that SQL output unverified.

## What is inspected

At the start of each audit, Metronome freezes a snapshot of every registered
Flow and up to 36 recent completed runs. It publishes only:

- hashed acquisition/configuration identity and bounded reporting-period data;
- exact registered normalized CSV paths, checksums, period keys, and transform
  checksums;
- recorded SQL server/database/schema/table identity and replace-mode facts.

The reader accepts only registered dataset/run/source IDs. It does not accept
SQL, expressions, URLs, code, commands, or file paths from Qwen. CSVs are
opened through canonical, non-link file handles and rehashed after reading.
PostgreSQL uses fixed quoted templates, read-only repeatable-read transactions,
strict privilege and definition checks, statement limits, cancellation, and
rollback-only cleanup.

Supported file evidence is retained normalized CSV. Missing files, unsupported
formats, schema drift, unsafe SQL identities, and exhausted work budgets remain
visible coverage gaps. No replacement file or table is guessed.

## Findings and limits

Trusted code computes row counts, missing periods, nulls, duplicates,
numeric/date validity, totals, bounded category counts, stable baselines, and
eligible same-run file/SQL comparisons. Qwen receives sanitized Flow context
and aggregates and may return evidence-linked hypotheses. Its response is
schema-, scope-, evidence-, and numeric-reference validated. Hypotheses remain
unconfirmed; only independent computed measurements can create Alerts.

The model has no write, delete, execute, repair, or settings-changing tool.
That application boundary does not prove the model server itself is isolated:
filesystem/network restrictions and disabled prompt persistence remain managed
deployment responsibilities and are stated in the UI.

Stopping or pausing fences late reader/model responses from profiles, findings,
and Alerts. Pausing persists across restarts and updates. Stopping retains the
nightly schedule. A long shutdown produces at most one bounded catch-up window,
not an audit backlog.
