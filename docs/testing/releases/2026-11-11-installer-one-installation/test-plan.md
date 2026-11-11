# 2026-11-11: Setup refuses to start a second, empty installation: test plan

- Change/PR: link in the PR's testing section. `setup.ps1` checks where the
  installed service keeps its database before it changes anything. It stops
  when it is run from a different copy of the code that has no database of its
  own, and asks before switching Metronome between two existing databases.
  `update_app.ps1` updates the installed copy wherever the file sits.
- Code baseline: `main` at `ab1b09f`
- Related report: [test-report.md](test-report.md)
- Intended environments: the pull request's `windows-latest` job (Windows
  PowerShell 5.1), where every case below runs; Linux cloud container with the
  checkout-owned Python 3.13 `.venv` for the tests that need no PowerShell

## Prerequisites and test data

No live system and no real service. Each case builds two folders in a
temporary directory: an "installed" Metronome with a database file, and a
second copy of the code elsewhere. The installed service is described either
directly to the function under test or under a throwaway registry key in
`HKCU:\Software\MetronomeSetupTest`, which the test removes. The real service
key under `HKLM` is never read or written by a test.

G-09 starts the real `setup.ps1`. It is expected to stop at the new check. It
runs only on the disposable GitHub Windows runner (`GITHUB_ACTIONS=true`), so
that a regression in the check could never go on to install services on
somebody's PC.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| G-01 | Installed service has a database; setup is run from a second copy whose folder has none | `stop`; the message names the installed database, the `setup.ps1` to run instead and this folder | `tests/test_setup_install_guard.py::test_a_second_copy_with_no_database_is_refused_and_told_where_to_run` |
| G-02 | Setup is run from the installed folder; the same database path also given in capitals | `continue` with no message both times | `...::test_the_installed_folder_continues_without_comment` |
| G-03 | The installed database reached through a directory junction, so its path differs | `continue`: one file, not a second database | `...::test_one_database_reached_through_a_junction_is_not_a_second_database` |
| G-04 | Services point at one copy; setup is run from another folder that has its own database (how a PC is pointed back after a second copy was installed by mistake, and how the mistake would be repeated) | `ask`; both databases are named, each with its size and last-changed time | `...::test_two_folders_with_a_database_each_are_shown_side_by_side_for_a_person_to_choose` |
| G-05 | No installed service; and a service whose recorded database file no longer exists | `continue`: an ordinary first install | `...::test_no_service_or_a_database_that_is_gone_is_an_ordinary_first_install` |
| G-06 | As G-01 with `DG_SETUP_NEW_DATABASE=1` | `notice` that a new empty database is started on request and the other one is left where it is | `...::test_a_new_empty_database_beside_an_installed_one_has_to_be_asked_for` |
| G-07 | A registry key shaped as nssm writes it: `AppDirectory` and a multi-line `AppEnvironmentExtra` with `DG_DB_PATH` among other values | The code folder and database are read back exactly; a service that is not installed gives empty values | `...::test_the_installed_location_is_read_from_the_service_settings` |
| G-08 | Ask for services that do not exist to be started again | Nothing is started and nothing fails | `...::test_restarting_services_that_are_not_installed_does_nothing` |
| G-09 | The real `setup.ps1` and `tools\*.ps1` copied into the second copy; service settings as G-07; run `powershell.exe -File setup.ps1 -Unattended` in three situations: no database beside the copy; another database beside the copy; no database, started as the relaunched half of an older copy (`DG_SETUP_RELAUNCHED=1`) | Exit code 1 and `SETUP STOPPED` each time. First: `Nothing was changed.` Second: an unattended update never switches databases. Third: says the first part of the run stopped Metronome. In all three the folder beside the copy and the copy itself hold exactly what they held before, and both databases are byte-identical | `...::test_setup_itself_stops_before_it_creates_or_replaces_anything` (3 cases) |
| G-10 | Every `.ps1` in the repository, including the new helper, through the PowerShell parser | No parse errors | `tests/test_setup_fast_path.py::test_powershell_parses_every_script` |
| G-11 | The Windows job's command line | Names `tests/test_setup_install_guard.py`, so G-01 to G-09 run before a merge | `tests/test_setup_fast_path.py::test_installer_scripts_are_checked_on_windows_before_merge` |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_setup_install_guard.py --test tests/test_setup_fast_path.py \
  --test tests/test_unattended_update_scripts.py --test tests/test_ci_sharding.py
```

On Linux G-01 to G-10 are skipped: there is no PowerShell. The pull request's
`Windows verifier contracts` job runs them. Required final-head CI is the full
regression.

## Acceptance and cleanup

Accepted when G-01 to G-11 pass on the pull request's Windows job and
`Merge ready` passes on the final head. The tests remove their temporary
folders and their registry key.

Not covered by any test: a complete run of `setup.ps1` on a PC with real
services; typing `SWITCH` at the prompt and the run that follows; starting
real services again after a refused run; and the changed folder lookup in
`update_app.ps1` beyond parsing (G-10).
