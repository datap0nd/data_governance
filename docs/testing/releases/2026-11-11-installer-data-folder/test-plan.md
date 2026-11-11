# 2026-11-11: Setup run from the data folder stops and names the code folder: test plan

- Change/PR: link in the PR's testing section. `setup.ps1` keeps the database
  one folder above the folder it is run from. Run from Metronome's data
  folder, beside `governance.db`, it looked one folder too high, found no
  database and started an empty Metronome. It now recognises the data folder
  and stops, naming the `setup.ps1` in the code folder inside it.
- Code baseline: `main` at `a26bb04`
- Related report: [test-report.md](test-report.md)
- Intended environments: the pull request's `windows-latest` job (Windows
  PowerShell 5.1), where every case below runs; Linux cloud container with the
  checkout-owned Python 3.13 `.venv` for collection

## Prerequisites and test data

No live system and no real service. Each case builds a data folder in a
temporary directory: a `governance.db` file, a `logs` folder and a
`data_governance-main` subfolder with `setup.ps1` and `app\main.py`. D-03
places the real `setup.ps1` beside the database and starts it; it runs only
on the disposable GitHub Windows runner (`GITHUB_ACTIONS=true`).

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| D-01 | Ask the check about the data folder | `stop`; the message names the database in that folder and the `setup.ps1` of the code folder inside it | `tests/test_setup_install_guard.py::test_setup_run_from_the_data_folder_is_sent_to_the_code_folder_inside_it` |
| D-02 | Ask about the code folder itself, with a database file inside it; and about the data folder after its database is removed | `continue` both times | `...::test_a_code_folder_is_not_mistaken_for_the_data_folder` |
| D-03 | The real `setup.ps1`, its helpers and a stand-in `app\main.py` copied beside the database; no installed service; run `powershell.exe -File setup.ps1 -Unattended` | Exit code 1; `SETUP STOPPED: this folder holds Metronome's data`; the code folder's `setup.ps1` is named; `Nothing was changed.`; the folder above holds nothing new; the data folder and the database are exactly as before | `...::test_setup_itself_stops_in_the_data_folder_and_creates_nothing_above_it` |
| D-04 | Every `.ps1` in the repository through the PowerShell parser | No parse errors | `tests/test_setup_fast_path.py::test_powershell_parses_every_script` |
| D-05 | The earlier installer cases | Unchanged results | The rest of `tests/test_setup_install_guard.py` |
| C-01 | The browser-install script with stand-ins for name resolution, the install command and the browser start: mirrors answer; mirrors do not resolve; the install with system packages fails; a browser does not start; nothing can be installed; a system other than Linux | With packages when the mirrors answer; the browsers alone when they do not or when the first attempt fails; exit code 1 when a browser does not start or nothing installs; unchanged behaviour off Linux | `tests/test_ci_browser_install.py` (7 cases) |
| C-02 | The pull request's own CI run, which uses the changed workflow | Every job gets past the browser-install step and runs its tests | The final CI run |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_setup_install_guard.py --test tests/test_setup_fast_path.py \
  --test tests/test_unattended_update_scripts.py --test tests/test_ci_browser_install.py \
  --test tests/test_ci_sharding.py --test tests/test_ci_merge_gate.py
```

On Linux D-01 to D-05 are skipped: there is no PowerShell. The pull request's
`Windows verifier contracts` job runs them. Required final-head CI is the full
regression.

## Acceptance and cleanup

Accepted when D-01 to D-05 pass on the pull request's Windows job, C-01 and
C-02 pass, and `Merge ready` passes on the final head. The tests remove their temporary
folders.

Not covered by any test: the recovery on the affected PC, where setup is run
from the code folder and `SWITCH` is typed.
