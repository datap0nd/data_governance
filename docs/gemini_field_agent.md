# Gemini CLI as the Flow field agent

Flows fail on the work PC in ways that neither CI nor a remote coding session
can see: a share that reports a copy as incomplete, a download that never
starts, a filter whose label changed, a week picker that the recorder cannot
describe. Every release report since
[2026-09-05](testing/releases/2026-09-05-flows/test-report.md) ends with
"automated regression verified; live qualification pending" for that reason.

Gemini CLI running on the work PC closes that gap. It sits next to the
signed-in browser profiles, the network shares, desktop Excel and the live
portals. This document defines its role, the evidence it produces and the
loop that turns that evidence into merged fixes. The model is not the point;
any command-line assistant that reads [GEMINI.md](../GEMINI.md) and runs the
playbooks in `.gemini/commands/metronome/` plays the same role.

## Roles

| Role | Where | Owns | Must not |
| --- | --- | --- | --- |
| Field agent (Gemini CLI) | work PC | Observe live runs, reproduce a failing step headed, probe selectors and date pickers, measure share copies, produce the redacted bundle and findings, retest a merged fix | Edit `app/`, `tools/` or `tests/` on the work PC; run production SQL; put URLs, share paths, cookies, report rows or screenshots into anything that leaves the PC |
| Engineering agent (repository session) | repository, CI | Root cause from the bundle, implementation, tests, PR, CI, head-pinned merge to `main`, release plan and report | Claim live behavior without evidence from a bundle or a retest |
| Owner | both | Update the app from `main`, run the playbooks, relay bundles, decide option designs | |

Local edits on the work PC are replaced by the updater and bypass every test,
so the field agent never changes application code. Every Flow's
`Scripts/run_flow.py` is the sanctioned experiment sandbox: an edited copy is
archived on the next save and never changes scheduled execution
([standalone execution](flow_standalone.md)).

## The evidence contract

The handoff artifact is a **diagnosis bundle** written by
`tools/diagnose_run.py`:

```powershell
.\.venv\Scripts\python.exe tools\diagnose_run.py --run-id 184 --output diagnosis
```

`diagnosis\run-184\` then contains:

- `bundle.json`: the redacted run summary, frozen configuration, worker
  state, recovery preflight, the last 30 events with exception signals and
  traceback tails, artifact metadata, and the comparison with the latest
  earlier successful run of the same Flow. For recorded Flows it also names
  the failing step, its execution contract and the failure screenshot file
  names.
- `steps.json`: the recorded step list with each step's outcome, output and
  range contracts (recorded Flows only).
- `README.md` and a `findings.md` template.

Everything comes from the projections the in-app operations investigator
already uses (`app/ai/operations_tools.py`) and is scrubbed again with the
recording diagnostics allowlist (`app/flow_recording_diagnostics.py`): local
paths, URLs, cookies, tokens, entered values, e-mail addresses, Playwright
call logs and raw browser messages are removed; keys such as `file_path`,
`storage_state`, `cookie`, `query` and `recipient` are dropped before
serialization. Report files, run folders, browser profiles, replay recipes
and stored browser state are never read. A projection that cannot be computed
(for example the recovery preflight of a draft Flow) is recorded as
unavailable rather than failing the bundle. The tool's own test seeds a run
whose events contain a portal URL, a share path, a cookie header, an entered
value and an e-mail address and asserts none of them reach any bundle file.

`findings.md` follows the incident-investigator contract in
[ai_incident_investigator.md](ai_incident_investigator.md): what happened,
impact, one suggested action, every claim tied to a bundle field, explicit
abstention when evidence is insufficient. Screenshots are referred to by file
name and stay in the worker profile's `diagnostics` folder.

## The loop

```text
Flow fails on the work PC
        │
        ▼
Owner runs /metronome:diagnose-run <run id>           (field agent)
        │  bundle + findings; repro/probe/share playbooks when indicated
        ▼
Engineering session reads the bundle, fixes, tests, opens PR, CI, merges
        │
        ▼
Owner updates the app from main
        │
        ▼
Owner runs /metronome:retest-fix <flow> <old run id> <stage>   (field agent)
        │  PASS/FAIL with dates, revision, run id
        ▼
Result appended to the release test report as live evidence
```

## Playbooks

Custom commands live under `.gemini/commands/metronome/` and are invoked as
`/metronome:<name>` inside Gemini CLI started from the application checkout.
Each ends in a fixed report template so a fast model answers consistently.

| Command | Use it when | Produces |
| --- | --- | --- |
| `diagnose-run <run id>` | Any failed or suspicious run | Bundle plus completed `findings.md` |
| `repro-step <flow folder> <step id>` | A recorded step failed with a Playwright signal | What the screen showed, which signal was true, next playbook |
| `probe-selector <flow folder> <step id>` | Locator timed out or matched several elements | Candidate table and one Repair target value for the recording editor |
| `capture-date-range <flow folder> <step id>` | A week/date picker is hard to record | Draft `select_range` contract for the step's Advanced panel |
| `verify-share-copy <run id>` | `copy_verification_warning` or "not copied completely" | Size/hash comparison and measured settle time |
| `retest-fix <flow folder> <old run id> <stage>` | After the app was updated | Dated PASS/FAIL retest for the release report |

Reproductions always run a copy of the Flow's `run_flow.py` with
`--headed --no-sql --no-transform` and, except for the retest, a private
`--output-root` and `--profile-dir`. The exact command-file format is the one
documented for the installed Gemini CLI version; verify `{{args}}`, `!{...}`
and `@{...}` behave as expected on first use.

## Failure classes and what each side does

| Failure class | Field agent establishes | Engineering changes with that evidence |
| --- | --- | --- |
| Copy to network fails or a run sticks at "Downloading 1 of 1" | Whether the share file was complete, measured settle time, sanitized error text | Settle and verification policy in `app/flow_worker.py` (`_copy_with_checksum`, `_verify_copied_file`) or the direct-publish reconcile path in `app/flow_publish.py` |
| Download never starts or never completes | Whether a native download event fires, whether staging grows, which completion contract the portal honours | Honour the step's Download completion for every adapter; adjust wait policy from measured timings |
| Filter not found | Live prompt labels and control types | Replace label-text strategies with per-filter capabilities discovered by the scan |
| Date range hard to record | The picker's real cell selector, selected-state marker and navigation | Populate the `select_range` contract; later import the probe output as a suggested repair in the recording editor |
| Portal navigation changed | The new path, captured as an allowlisted codegen recording | Owner re-records; no code change unless the importer rejects something |

## Hidden rules to convert into visible options

The engine carries behavior keyed to a portal adapter or leaked from one
report rather than chosen in the Flow. None of it keys on a Flow name. Each
item becomes an option using the established patterns: a nullable JSON column
with a small `default_config`/`normalize_config` module and a `FlowWrite`
validator (see `app/flow_email_delivery.py` and `app/flow_view_refresh.py`),
or a per-step control under Options/Advanced in the recording editor
(`app/static/flow_recording_editor.js`, validated in `app/flow_recording.py`).
The default reproduces today's behavior, and the option text says what it
does.

| # | Hidden rule today | Visible option it becomes |
| --- | --- | --- |
| 1 | The ASAP adapter forces staged download completion over the step's own setting (`app/flow_recording_runtime.py`) | Step "Download completion" honoured as saved; ASAP defaults to Verified staging and shows it |
| 2 | CSV preamble stripping chosen by adapter with no user control (`app/flow_recording_runtime.py`, `app/flow_worker.py`) | Per-step output option "Strip report preamble" |
| 3 | Header-row detection ranks rows by Regional FOTA's thirteen column names for every Flow (`XLSX_HEADER_LABEL_HINTS` in `app/flow_worker.py`, duplicated in `transforms/asap_fota_unpivot_v1.py`) | Use the step's expected `output.headers` as the hint; remove the frozen list |
| 4 | Multi-week matrix recognized only when the descriptor value is `sell_out` or `fota` (`app/flow_worker.py`) | Per-step "Weekly matrix" option with a descriptor column name |
| 5 | Category prompt accepts only weekly/daily; Flagship Measure prompt special case (`app/flow_worker.py`) | Per-filter "member list is visual-only" flag set by discovery and shown in the builder |
| 6 | Interaction strategy chosen by prompt label text (dimension, category, measure) and a literal "Date" slider fallback (`app/flow_worker.py`) | Per-filter control type and strategy stored in `flow_report_filters.automation_json`, editable |
| 7 | GSCM-only retry count and blind 60 s / 120 s load buffers (`app/flow_worker.py`) | Per-portal settings under Flows > Settings |
| 8 | `network_replay` per-Flow switch only by hand-editing JSON (`app/flow_replay.py`, [network replay](network_replay.md)) | Column and checkbox in Edit Flow |
| 9 | GSCM DOM hint lists where the docs say "edit the constant" (`app/flow_gscm.py`, [GSCM portal](gscm_portal.md)) | Portal-level editable hint lists in Settings |
| 10 | User-facing message names the "Sell-out Week" filter (`app/routers/flows.py`) | Message uses the Flow's configured filter label |

Migrations that seed adapters by portal host name are data, not behavior, and
stay. NASCA handling is content-sniffed and stays; its `excel_trim` block gets
an explanatory state in the UI. Items 1 to 7 only misbehave live, so each
conversion ships with a `retest-fix` run on the affected production Flows
with SQL disabled, before and after.

## Phases

1. **Foundation (this release):** `GEMINI.md`, the six playbooks,
   `tools/diagnose_run.py` with its redaction test, this document and the
   release test package.
2. **Observation:** the owner runs `diagnose-run` on the recent failed runs
   and the share or reproduction playbooks where the findings point to them.
   Bundles are ranked by frequency. No code changes.
3. **Fixes:** one PR per finding, plus hidden rules 1 to 4 and 8 first. Each
   PR follows the standard workflow and carries the retest evidence as
   opaque references.
4. **Optional:** a browser tool for the field agent (a Playwright or Chrome
   DevTools MCP server in `.gemini/settings.json`) so selector and date-range
   probes inspect the live DOM directly; an "Import suggested repair" control
   in the recording editor; pointing the in-app operations investigator at an
   OpenAI-compatible Gemini endpoint so Alert analysis works without a
   self-hosted model.

## Boundaries that do not change

Live testing stays opt-in: the playbooks run only when the owner runs them,
and their results are appended to a release report as dated, revision-specific
evidence with opaque references. The field agent never approves a Pipeline,
declares data fresh, or reports an e-mail as delivered; Metronome's own
checks remain authoritative.
