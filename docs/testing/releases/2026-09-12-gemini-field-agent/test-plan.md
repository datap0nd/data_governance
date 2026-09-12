# 2026-09-12 Gemini field-agent workflow and diagnosis bundle tool: test plan

- Change/PR: adds `GEMINI.md`, six playbook commands under `.gemini/commands/metronome/`, `tools/diagnose_run.py` (redacted diagnosis bundle for one Flow run) with `tests/test_diagnose_run.py`, [docs/gemini_field_agent.md](../../../gemini_field_agent.md) and a README link. No application behavior, schema or UI changes.
- Code baseline: `37169efe0cb33eb675224798fc9a1a33fde052b7` (main after #117)
- Related report: [test-report.md](test-report.md)
- Intended environments: repository checkout with the CI Python dependencies (synthetic SQLite fixtures only). Work-PC use of the playbooks is a later, owner-run activity and is not part of this plan.

## Prerequisites and test data

All cases use an isolated SQLite database created by the `flow_db` fixture.
The seeded run deliberately contains a portal URL, a UNC share path, a cookie
header, an entered form value, an e-mail address and a Playwright call log so
the redaction can be observed. No live portal, credentials, browser or share
is involved.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | Seed a recorded Flow with a saved recording, one succeeded run and one failed run whose events carry the private values above; call `diagnose_run.write_bundle(run_id, folder)`. | `run-<id>/` contains exactly `README.md`, `bundle.json`, `findings.md`, `steps.json`. `bundle.json` has the failed run summary, recovery preflight, 3 events, the comparison with the succeeded baseline and a server-issued `deep_link`; `recording.context.failing_step_id` is the failed step and `screenshot_files` lists only the PNG file name; `steps.json` lists the three steps with the output contract and outcomes. None of the seeded private values, the host name, `token=hidden`, the share server, the Windows profile path, `Call log:` or `raw_message` appear in any file. | `tests/test_diagnose_run.py::test_bundle_keeps_structure_and_excludes_private_values` |
| T-02 | Seed a catalog (non-recorded) failed run with no earlier success; write the bundle. | `recording` is `null`, `comparison` reports "No earlier successful run is available for comparison." and no `steps.json` is written. | `::test_bundle_for_catalog_run_has_no_recording_section` |
| T-03 | Write the bundle, overwrite `findings.md`, write the bundle again. | The investigator's `findings.md` is preserved. | `::test_findings_template_survives_regeneration` |
| T-04 | Run `main(["--run-id", "424242", "--output", <folder>])` for a run that does not exist. | Exit code 2, "Flow run not found" on stderr, no output folder created. | `::test_unknown_run_exits_with_code_two` |
| T-05 | Run `python tools/diagnose_run.py --help` as a subprocess without a database. | Exit code 0 and the `--run-id` option is described. | `::test_command_line_help_runs_without_a_database` |
| T-06 | Parse every `.gemini/commands/metronome/*.toml` with `tomllib`. | Each file has exactly the `description` and `prompt` keys. | Documentation check recorded in the report |
| T-07 | Follow every relative link in `docs/gemini_field_agent.md`, `GEMINI.md` and the new README paragraph. | Every target exists in the checkout. | Documentation check recorded in the report |

## Automated checks

```powershell
.\tools\check.ps1 -Mode Verify `
  -TestPath tests/test_diagnose_run.py `
  -SyntaxPath tools/diagnose_run.py,tests/test_diagnose_run.py
```

Final-head CI (`Merge ready`) is the authoritative full Python regression
because a tool and a test module are added.

## Acceptance and cleanup

Accepted when T-01 to T-07 pass and final-head CI is green. Nothing is
installed or configured on any machine by this change; the playbooks only
take effect when Gemini CLI is started inside the checkout on the work PC.
Rollback is a revert of the PR; no data or schema is affected.
