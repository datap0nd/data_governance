# 2026-11-04: Film removed and media/ kept out of updates: test plan

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

  Unchanged: the update steps themselves. The update merges files and never
  deletes installed ones, so a `media` folder already on a PC stays until the
  owner deletes it.
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
| R-01 | Existing updater contract tests | Unchanged | rest of `test_unattended_update_scripts.py` |

## Automated checks

```bash
python tools/check.py verify --test tests/test_unattended_update_scripts.py
git archive --format=zip -o /tmp/update.zip HEAD
```

Required final-head CI (`Merge ready`) is the full regression.

## Acceptance and cleanup

Accepted when every case passes locally and final-head `Merge ready` passes.
After the next update, the owner may delete the leftover `media` folder in
the installed code folder; nothing reads it. Rollback: revert the PR, which
restores the film and drops the export rule.
