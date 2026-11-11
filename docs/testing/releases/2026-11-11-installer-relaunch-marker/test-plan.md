# 2026-11-11: A reused PowerShell window no longer makes setup skip its first half: test plan

- Change/PR: link in the PR's testing section. `setup.ps1` resumed after
  extraction whenever the variable `DG_SETUP_RELAUNCHED` was set. A copy that
  relaunches itself set it in the PowerShell window it was run from, where it
  stayed. The next run in that window skipped the download, never stopped
  Metronome and never replaced the code. The relaunch is now verified by the
  process that started it, and nothing is left in the window. Setup's opening
  lines about the database are also reworded: they read as two findings
  ("Database:" and "DB exists:") about what is one file.
- Code baseline: `main` at `28e5d1b`
- Related report: [test-report.md](test-report.md)
- Intended environments: the pull request's `windows-latest` job (Windows
  PowerShell 5.1), where every case below runs; Linux cloud container with the
  checkout-owned Python 3.13 `.venv` for collection and the tests that need no
  PowerShell

## Prerequisites and test data

No live system and no real service. The function cases pass values directly or
start one child PowerShell process. R-03 and R-04 start the real `setup.ps1`
in a temporary folder and run only on the disposable GitHub Windows runner
(`GITHUB_ACTIONS=true`). In R-03 the update address points at a local port
nothing listens on, so the download fails by design and setup ends there,
before it would stop or install anything.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| R-01 | Ask whether a copy is the relaunched one with: marker and matching recorded parent; marker only; marker and another process id; recorded parent without marker | True only for the first | `tests/test_setup_install_guard.py::test_only_the_copy_a_relaunching_setup_started_counts_as_relaunched` |
| R-02 | One PowerShell process sets what a relaunching setup sets, starts a child the way setup does, then asks the same question itself | `child=True here=False`: the started copy is recognised, the window that keeps the variables is not | `...::test_a_marker_left_in_the_window_is_true_for_the_started_copy_only` |
| R-03 | The reported situation: real `setup.ps1`, `DG_SETUP_RELAUNCHED=1` left in the environment, an archive from an earlier update on disk, Python present; run unattended | No "already downloaded and extracted"; `[3/5] Downloading latest version` appears; setup ends with the download failure and exit code 1 | `...::test_a_marker_left_in_the_window_does_not_make_setup_skip_the_stop_and_download` |
| R-04 | Real `setup.ps1` in a second copy beside an installed Metronome, in three situations: no database here; another database here; another database here with a marker and a `SWITCH` answer left in the environment | Exit code 1 and `SETUP STOPPED` each time; an answer from an earlier run is not accepted; nothing created, both databases byte-identical | `...::test_setup_itself_stops_before_it_creates_or_replaces_anything` (3 cases) |
| R-05 | Every `.ps1` in the repository through the PowerShell parser | No parse errors | `tests/test_setup_fast_path.py::test_powershell_parses_every_script` |
| R-06 | Ask for the size text of 300 bytes, 1.3 MB and 1.3 GB | `1 KB`, a figure in MB, a figure in GB | `tests/test_setup_install_guard.py::test_sizes_are_shown_in_the_unit_that_tells_them_apart` |
| R-07 | In R-04, read what setup prints about the database | With no file: `No file at this path.` With a file: `This file exists: 1 KB`. One statement under the `Database:` line, not a second label | Asserted inside R-04 |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_setup_install_guard.py --test tests/test_setup_fast_path.py \
  --test tests/test_unattended_update_scripts.py
```

On Linux R-01 to R-07 are skipped: there is no PowerShell. The pull request's
`Windows verifier contracts` job runs them. Required final-head CI is the full
regression.

## Acceptance and cleanup

Accepted when R-01 to R-07 pass on the pull request's Windows job and
`Merge ready` passes on the final head. The tests remove their temporary
folders and registry key.

Not covered by any test: a complete update on a PC with real services, with a
real relaunch from one copy of `setup.ps1` to the next.
