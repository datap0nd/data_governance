# Gemini simple setup and database-wide reads: test plan

- Scope: extension setup asks server, reader username and masked password
  separately; automatic local Metronome address; database/table discovery uses
  database grants, with no manual relation list. Application logic is unchanged.
- Baseline: `dd2a1747cd3da777706426042c5f6395878dde3d` (`origin/main`).
- Related report: [test-report.md](test-report.md).
- Environments: Windows PowerShell fixture, Node 22+, synthetic local browser,
  disposable PostgreSQL 14/18 CI services.

## Prerequisites and data

Install the extension's locked dependencies with `npm ci --ignore-scripts` in
`integrations/metronome-gemini`. PowerShell 7 (`pwsh`) is needed by the isolated
installer fixture. It injects fake Gemini/npm/service operations and a temporary
user folder; it never changes the operator's real Gemini registration.

PostgreSQL tests require `METRONOME_TEST_PG_URL` pointing only to the disposable
loopback database `metronome_test_ownership`, with the CI fixture administrator.
The test rejects other destinations and creates random fixture roles, schemas
and a second database. All records are fictional Korean labels and amounts.

## Cases

| ID | Actions | Expected result/evidence |
| --- | --- | --- |
| S-01 | `node --test test/setup.test.mjs` | Fresh install skips generic settings prompts, then asks exactly server/user/password; password is sensitive. Existing links are reused, copies updated. Prior flow/site scopes survive. Nine injected setup cases cover skip SQL, offline service, dependency failure, interruption, blank username and quoted whitespace. |
| S-02 | `node --test test/read-tools.test.mjs` | Qualified new/Korean tables require no registration. Mutation, roles, multiple statements and unsafe functions denied. Ambient/upload credentials ignored. Raw punctuation in individual credentials preserved. Database selection keeps host/account fixed. Missing-database recovery omits secrets. Catalog pages/filters bound and labeled. |
| S-03 | `node --test test/transport.test.mjs` | Actual MCP SDK discovery and tool transport work with the revised settings. |
| S-04 | `node --test test/sql-postgres.mjs` under the explicit fixture URL | Actual database privileges deny ungranted tables and writer accounts; a new table/view is discoverable after grant without configuration. Catalog pagination is complete. Same reader discovers and queries a second database; later write privileges there block analysis, while the original database remains usable. Revoked CONNECT removes the second database from discovery and prevents reading it. |
| S-05 | Serve repo with `python -m http.server 8768 --bind 127.0.0.1`; open `/docs/previews/gemini-setup.html`. Click through server, empty username/recovery, password, Finish, rerun, offline service, interruption/restart and blank-server skip. | One field at a time, masked password, preserved completed fields, clear local status and next command. Fictional browser observations and preview hash recorded. Obtain owner feedback before completing the changed journey. |
| S-06 | `node --check` for changed `.mjs`; PowerShell Parser for installer/setup/fixture; JSON parse manifest/package files; validate skill; `git diff --check` | Syntax and packaging valid; docs match three prompts and permission-driven discovery; no application changes or secrets. |
| S-07 | Required final-head CI | Full Python/frontend/PostgreSQL regression and Merge ready pass. Record run URL and head SHA in PR before a head-pinned merge. |

Run the smallest affected local set once; rerun only failures or newly changed
cases. CI supplies the full regression, including the real PostgreSQL fixture.

## Acceptance and cleanup

Accept after owner preview feedback, affected checks and required final-head CI.
The PowerShell fixture deletes only its resolved temporary directory. PostgreSQL
fixture cleanup drops its random database/schema/roles. Stop the local preview
server after review. Keep test evidence and proposal receipts. Rollback is an
extension-only revert/reinstall from a reviewed revision; never restore a table
allowlist by silently changing database grants or using upload credentials.
