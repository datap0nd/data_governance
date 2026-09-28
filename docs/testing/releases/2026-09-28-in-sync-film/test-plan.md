# In Sync film in GitHub: test plan

- Change/PR: Add the current 84-second Metronome film and editable HyperFrames project under `media/metronome-in-sync/`; no application behavior changes.
- Code baseline: `origin/main` at `2eb94a230f2d822a501d04197617fa562cb45a28`.
- Related report: [test-report.md](test-report.md).
- Intended environment: Windows checkout with Python 3.13, Node.js 24, npm and Chrome; all browser content is the local fictional film review.

## Prerequisites and test data

Use a clean worktree based on current `origin/main`. The film project has its own `package-lock.json` and review MP4. Run `tools/check.ps1 -Mode Setup` once for the checkout-owned Python test environment, then `-Mode Preflight`. For film composition checks, run `npm ci` inside `media/metronome-in-sync/`. No application service or real data is needed.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| FILM-01 | Run `tools/check.ps1 -Mode Verify -TestPath tests/test_film_package.py` with syntax selectors for the film scripts. | The MP4 and score match the manifest, the score is 84-second 48 kHz/24-bit stereo, and every local review-page media/document link resolves. | Check result JSON and pytest count. |
| FILM-02 | In the film folder, run `npm ci`, `node scripts/build.mjs`, then `.\film.ps1 check --at '18,30.5,42,53.5,70' --strict --json`. | Eight scenes build; HyperFrames reports no lint, runtime, layout or contrast failures on representative changed scenes. | Command results, HyperFrames version and sample counts. |
| FILM-03 | Run `node scripts/serve-review.mjs`; open `http://127.0.0.1:4392/animatic-review.html` in Chrome. Play the film, select “Build the pipeline,” then open the style-frame gallery and one full-size frame. | The video loads for 84 seconds without media error, chapter seek plays at 29 seconds, and the current flat-illustration frame opens. | Browser-observed duration, ready state, playback/seek, image display. |
| FILM-04 | Inspect the committed MP4 with FFprobe and compare SHA-256 with `manifest.json`. | H.264 1920×1080/60 fps, 48 kHz stereo AAC, 84.000 seconds; hash matches the reviewed cut. | FFprobe output and checksum. |
| FILM-05 | Inspect the package test and review server's failure paths for a missing asset, a changed MP4 and an invalid media range. | A missing link or changed byte fails the package test with the affected path; the server returns 404 or 416 as appropriate. Recover by restoring the committed file, rebuilding if the source changed, and rerunning FILM-01. | Focused source review and any observed negative response. |

## Automated checks

```powershell
.\tools\check.ps1 -Mode Setup
.\tools\check.ps1 -Mode Preflight
.\tools\check.ps1 -Mode Verify -TestPath tests/test_film_package.py `
  -SyntaxPath media/metronome-in-sync/audio/score.py,media/metronome-in-sync/scripts/build.mjs,media/metronome-in-sync/scripts/serve-review.mjs,media/metronome-in-sync/film.ps1,tests/test_film_package.py
```

This is the smallest affected Python test set. Required final-head CI is the authoritative full Python regression; do not repeat that suite locally.

## Usability evidence

The review site is a local synthetic preview of the film. Check that the video and chapter buttons are labeled, the chosen chapter gives immediate playback feedback, navigating to style frames is predictable, the dialog closes, and the page offers a clear next action.

## Acceptance and cleanup

Accept when the review MP4, source project, page links, focused tests, HyperFrames check and required final-head CI all pass. Stop the local review server after inspection. Keep only the committed artifact and release evidence; `node_modules/`, Python environments and temporary render files remain ignored. If the import causes trouble, revert the film-package and test-plan commit without changing the application.
