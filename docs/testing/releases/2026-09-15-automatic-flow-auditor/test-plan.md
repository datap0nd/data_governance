# Automatic Flow auditor: test plan

- Change/PR: automatic default-on auditing for every registered Flow, managed restricted-reader installation, local-only controls, evidence-bound Local AI review, and the approved simplified page.
- Code baseline: `0758b2f460a3ce759e57f9178fbe97a5cb3682fb`; the PR records its final tested head.
- Related report: [test-report.md](test-report.md)
- Intended environments: isolated Windows/Python fixtures, synthetic Chrome browser, final-head Ubuntu Python/frontend CI, Windows verifier contracts, and disposable PostgreSQL 14/18 CI.

## Prerequisites and test data

Use the locked test dependencies and an isolated `DG_DB_PATH`. Synthetic tests
create fictional Flows, completed run metadata, normalized CSVs, managed host
and reader configuration, deterministic model responses, and temporary ACL
fixtures. Set `AUDITOR_EVIDENCE_DIR` to this package's `evidence` folder for the
browser run. No external model, production database, application database,
portal, authentication session, or business export is used.

The PostgreSQL job supplies `METRONOME_TEST_POSTGRES_DSN` for its disposable
service. Tests create restricted roles and tables and clean them up. SQL cases
may skip outside that dedicated job; they must execute on PostgreSQL 14 and 18
in final CI.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| A-01 | Run `tests/test_auditor_managed.py`: provision twice; inspect host/reader files; provide both probe and uploader fixture credentials; try another file in the same folder; inspect setup contracts. | Generated tokens and category key persist, DSN stays reader-only, uploader credentials never appear, host config contains no DSN/model key, only the exact registered artifact is readable, and setup uses a loopback virtual service identity plus deny ACL. | pytest result and source assertions |
| A-02 | Run the Windows ACL fixture in `tests/test_check_command.py`: add a real deny ACL to a disposable host-config file, attempt read/write, remove ACL, reread. | Both denied operations fail and the unchanged fixture becomes readable after cleanup. | Windows verifier result |
| A-03 | Publish runs containing unrelated secret fields; add a second Flow; enumerate scope again; compare the first dataset; try the app SQLite path as the manifest. | Manifest omits private fields, includes every registered Flow automatically, preserves the existing dataset definition when an unrelated Flow joins, and refuses the application database. | `tests/test_auditor_manifest.py` |
| A-04 | Migrate a legacy untouched default-off row and an explicit pause; claim a Dubai 02:00 run twice; simulate a long outage, restart, Stop and Pause/Resume. | Untouched install becomes on with a due time; identifiable pause persists; one run is claimed; no backlog flood; stop retains the schedule; pause cancels and fences late work. | `tests/test_data_auditor.py`, `tests/test_auditor_lifecycle.py` |
| A-05 | Seed four stable same-weekday profiles and a 30% shortfall; change only catalog revision; test missing periods, invalid values, SQL mismatch boundaries, and baseline insufficiency. | Computed warning and evidence are stable/deduplicated; unrelated Flow/catalog change does not reset the dataset baseline; incompatible scope/schema does; unsupported evidence stays a gap. | auditor unit/lifecycle suites |
| A-06 | Return model write tools, SQL/path arguments, out-of-scope IDs, commands, fake findings, invented numbers and late responses. Then run with Local AI absent. | Every hostile response is rejected; no protected state changes; model has only `read_profile`; metrics still complete without inference and the model gap is explicit. | auditor unit/lifecycle suites |
| A-07 | Send auditor controls/evidence from loopback same-origin, remote IP, forged forwarding headers and cross-origin browser requests. Change the saved AI destination through the settings route. | Only direct local requests succeed; remote/forged access returns 403; the local AI write updates the non-secret trust pin without copying its key. | API and AI-settings suites |
| A-08 | In disposable PostgreSQL 14/18, inspect an approved table; add write, schema, membership, function, sequence and owner privileges; change table/RLS/type/index definition; try DELETE in the reader transaction. | Fixed aggregate reads return correct values without mutation; unsafe identity/privilege/definition cases fail closed; rollback leaves rows unchanged. | dedicated PostgreSQL CI jobs |
| U-01 | Open the synthetic page at 1440px and 390px; verify default On/all-Flows; Change time to 03:15; inject save failure, retry; inject Run-now outage, retry; Stop; Pause/Resume; navigate away/back. | No setup form, Flow selector or operator key; failed edit is retained beside the action; run/stop/pause feedback is visible; Stop hides when inactive; schedule/navigation state persists; layout has no page overflow. | `save-recovery.png`, `narrow-controls.png`, `browser.json` |
| U-02 | Publish partial coverage and script-like finding text; open and close evidence. | Per-Flow coverage/gaps are visible, evidence is escaped, and the next action is clear. | `finding-evidence.png`, browser metadata |
| R-01 | Required final-head CI on the PR. | Complete non-overlapping Python shards, frontend syntax/contracts, Windows verifier contracts, PostgreSQL 14/18 and Merge ready pass on one final SHA. | PR Testing section and CI run |

## Automated checks

Run this one affected set locally; the PostgreSQL selectors are reserved for
their non-overlapping CI services:

```text
python tools/check.py verify --test tests/test_data_auditor.py --test tests/test_auditor_manifest.py --test tests/test_auditor_lifecycle.py --test tests/test_auditor_managed.py --test tests/test_auditor_browser.py --test tests/test_ai_settings.py --syntax app/main.py --syntax app/ai/router.py --syntax app/local_access.py --syntax app/auditor/config.py --syntax app/auditor/manifest.py --syntax app/auditor/store.py --syntax app/auditor/detection.py --syntax app/auditor/engine.py --syntax app/auditor/router.py --syntax auditor_reader/policy.py --syntax auditor_reader/profiles.py --syntax auditor_reader/postgres.py --syntax auditor_reader/service.py --syntax auditor_reader/__main__.py --syntax tools/provision_auditor.py --syntax app/static/data_auditor.js
```

CI PostgreSQL command:

```text
python -m pytest tests/test_sql_ownership_postgres.py tests/test_auditor_postgres.py -q -ra
```

## Usability evidence

The supplied implementation plan records the owner's approval of the revised
default-on journey with “Yes, implement.” The synthetic browser fixture uses
the real page JavaScript, CSS and API against fictional services. Walk every
changed control, its injected failure and recovery, evidence navigation, and
the narrow layout. Retain only the three fictional screenshots and metadata.

## Acceptance and cleanup

Accept when the focused local check and final-head CI pass and the PR records
the final SHA/run. Fixture servers, browser contexts, databases, ACLs and files
must be removed by teardown. A failure must retain its original result and be
followed by a revision-specific retest. Rollback is a code/service rollback;
do not delete audit history, Flow outputs, generated configuration or Alerts.
