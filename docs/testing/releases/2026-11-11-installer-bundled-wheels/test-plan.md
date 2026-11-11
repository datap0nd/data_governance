# 2026-11-11: Installer no longer needs pandas before it installs pandas: test plan

- Change/PR: link in the PR's testing section. `setup.ps1` installs the three
  wheels kept in `vendor/` (pbixray, xpress9, kaitaistruct) without asking pip
  to resolve their dependencies offline.
- Code baseline: `main` at `c52d565`
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux cloud container with the checkout-owned Python
  3.13 `.venv`; the pull request's `windows-latest` job, where the same test
  also installs the Windows-only xpress9 wheel

## Prerequisites and test data

No live system. The functional case creates a new virtual environment in a
temporary folder and runs pip with the exact arguments read from `setup.ps1`
against the wheels in `vendor/`. It needs no network. `setup.ps1` itself is not
executed.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| B-01 | Read the bundled install line of `setup.ps1` and the metadata of every wheel in `vendor/` | The wheels need pandas and apsw, which are not in `vendor/`; the line is offline (`--no-index`) and does not resolve dependencies (`--no-deps`); `requirements.txt` names pbixray and its step runs afterwards, so the index supplies what pbixray needs | `tests/test_setup_fast_path.py::test_bundled_wheels_are_installed_without_looking_for_what_is_not_bundled` |
| B-02 | A new Python with nothing installed; run pip with the arguments the installer used before, then with the arguments it uses now | Before: pip stops with "No matching distribution found". Now: exit code 0, pbixray and kaitaistruct are installed, pandas is not (the requirements step installs it) | `tests/test_setup_fast_path.py::test_the_bundled_step_succeeds_on_a_python_with_nothing_installed` |
| B-03 | The two new tests against `setup.ps1` from `c52d565` | Both fail | Console output in the report |
| B-04 | Existing installer tests | Unchanged behaviour still passes, including the brace balance of `setup.ps1` | `tests/test_setup_fast_path.py`, `tests/test_unattended_update_scripts.py`, `tests/test_setup_worker_health.py` |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_setup_fast_path.py --test tests/test_unattended_update_scripts.py \
  --test tests/test_setup_worker_health.py
```

On Linux B-02 leaves xpress9 out, because only Windows wheels of it are
bundled. The pull request's `Windows verifier contracts` job runs the same
file on Windows with Python 3.13 and includes it. Required final-head CI is
the full regression.

## Acceptance and cleanup

Accepted when B-01 to B-04 pass, B-02 passes on the pull request's Windows
job, and `Merge ready` passes on the final head.

Not covered by any test: `setup.ps1` running end to end, and the later
requirements step reaching the package index through the office proxy.
