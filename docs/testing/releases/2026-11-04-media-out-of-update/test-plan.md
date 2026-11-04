# 2026-11-04: Film removed, media/ kept out of updates, quiet setup: test plan

- Change/PR: branch `claude/metronome-asap-data-limit-wulqmq`; PR link in the
  PR's testing section. The owner asked for the video to be removed and for
  nothing under `media/` to be downloaded.
  1. **Film removed.** `media/metronome-in-sync` (the In Sync film project:
     audio, video, renders and its HyperFrames sources, 135 files, 62 MB) is
     deleted from the repository. It remains in git history.
     `tests/test_film_package.py` checked only that package and is deleted
     with it.
  2. **media/ never in the update download.** A new `.gitattributes` marks
     `/media export-ignore`. The updater downloads GitHub's archive of
     `main`; GitHub builds it with `git archive`, which leaves export-ignored
     paths out, so anything placed under `media/` again is not downloaded.
  3. **No progress bars during setup.** The owner reported that both the
     download and "Extracting update over existing code" crawl. Windows
     PowerShell 5.1 slows `Invoke-WebRequest` and `Expand-Archive` badly
     while it draws their progress bars, and `setup.ps1` never turned them
     off. It now sets `$ProgressPreference = 'SilentlyContinue'` right after
     elevation; its own `[n/5]` step messages still show. An update runs the
     installed `setup.ps1` for its download and extraction, so this speeds up
     the update after the one that installs it.

  Unchanged: the order and content of the update steps. The update merges
  files and never deletes installed ones, so a `media` folder already on a PC
  stays until the owner deletes it.
- Code baseline: `main` at `f0defc8`.
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux with the checkout-owned Python 3.13 `.venv`
  and git; final-head CI on Ubuntu and Windows.

## Prerequisites and test data

A disposable git repository built by the test from fictional files. Nothing
reaches GitHub, a portal or a database.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| M-01 | Fixture repo with files under `media/` (nested and top level), `app/main.py`, `app/media.py`, `docs/media/guide.md` and the repository's `.gitattributes`; commit and `git archive --format=zip HEAD` | No entry under `media/`; the three other files are present | `test_unattended_update_scripts.py::test_update_archive_never_contains_anything_under_media` |
| M-02 | Run M-01 with an empty `.gitattributes` | The test fails, so it detects a missing rule | Recorded in the report |
| M-03 | `git archive --format=zip HEAD` of this branch | About 12.6 MB with no `media` entry, against 70.5 MB for `main` at `f0defc8` | Recorded in the report |
| P-01 | Read `setup.ps1` | `$ProgressPreference = 'SilentlyContinue'` is set once, before the first `Invoke-WebRequest -Uri` and before `Expand-Archive -Path`, and never set again | `test_unattended_update_scripts.py::test_setup_hides_progress_bars_before_downloading_and_extracting` |
| P-02 | Run P-01 against `setup.ps1` without the change | The test fails | Recorded in the report |
| R-01 | Existing updater and installer contract tests that read `setup.ps1` | Unchanged | rest of `test_unattended_update_scripts.py`, `test_auto_update.py`, and the 20 `setup.ps1` tests in `test_flows.py`, `test_gscm.py`, `test_auditor_managed.py`, `test_env_file.py`, `test_flow_capacity.py`, `test_flow_desktop_session.py`, `test_flow_worker_startup.py` and `test_setup_sso_password.py` (listed in the report) |

## Automated checks

```bash
python tools/check.py verify --test tests/test_unattended_update_scripts.py \
  --test tests/test_auto_update.py <the 20 setup.ps1 test ids in the report>
git archive --format=zip -o /tmp/update.zip HEAD
```

No PowerShell parse of `setup.ps1` runs in this container (no PowerShell)
or in PR CI; the change is one plain assignment. The Windows regression that
runs after each push to `main` executes the Windows-only installer tests.
Required final-head CI (`Merge ready`) is the full regression.

## Acceptance and cleanup

Accepted when every case passes locally and final-head `Merge ready` passes.
After the next update, the owner may delete the leftover `media` folder in
the installed code folder; nothing reads it. Rollback: revert the PR, which
restores the film, drops the export rule and shows the progress bars again.
