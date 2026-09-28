# In Sync film in GitHub: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: Import the current presentation film, editable composition and local review pages into `media/metronome-in-sync/`; PR link and final-head CI evidence will be recorded in the PR.
- Evidence cutoff (UTC): 2026-09-28 08:43.
- Tested code revision: uncommitted film package and tests on base `2eb94a230f2d822a501d04197617fa562cb45a28`; final-head CI was pending at this cutoff.
- Environment: Windows 10.0.26200, PowerShell 7.6.5, checkout-owned Python 3.13.15, Node 24.19.0, HyperFrames 0.8.81, Chrome local preview.
- Overall finding: Local package, composition, media and synthetic browser checks passed. The imported MP4 is the existing review render; it was not rerendered for this import.

## Executed checks

| Case | Command or procedure | Result | Evidence |
| --- | --- | --- | --- |
| FILM-01 | `tools/check.ps1 -Mode Setup`; `tools/check.ps1 -Mode Preflight`; `tools/check.ps1 -Mode Verify -TestPath tests/test_film_package.py -SyntaxPath media/metronome-in-sync/audio/score.py,media/metronome-in-sync/scripts/build.mjs,media/metronome-in-sync/scripts/serve-review.mjs,media/metronome-in-sync/film.ps1,tests/test_film_package.py` | Setup and preflight passed. Focused verify passed in 3.097 s: 2 tests, 0 failures, 0 errors, 0 skips; all selected syntax checks passed. | Checkout-local `.test-runs/20260928T083456126Z-34376-87c5fc7e/result.json`; setup `20260928T083420530Z-45168-8be9fddd`; preflight `20260928T083446141Z-5008-bce1c4fc`. |
| FILM-02 | Install locked dependencies with the portable npm wrapper, `node scripts/build.mjs`, then `.\film.ps1 check --at '18,30.5,42,53.5,70' --strict --json`. Repeated the composition check after the final builder edit. | Dependency install succeeded with 0 reported vulnerabilities. Eight scenes and 25 supers built for 84 seconds. Strict check passed: 0 lint, runtime, layout or contrast findings; 110/110 contrast checks passed over five scene times. | HyperFrames 0.8.81 command output; generated `assets/build-data.json`. |
| FILM-03 | Serve the repository copy at `127.0.0.1:4392` and inspect it using Codex Chrome controls. Load film, select “Build the pipeline,” open the frame gallery, inspect scene 4, move to scene 5 and close the dialog. | Video loaded with `duration=84`, `readyState=4` and no media error. Chapter selection set playback to 29 s and played. Gallery images and dialog navigation worked; close returned to the gallery. | Browser-observed DOM and video state on 2026-09-28. This is a local fictional preview. |
| FILM-04 | Run FFprobe on `renders/review/animatic.mp4` and SHA-256 hash the imported file. | H.264 1920×1080 at 60 fps, AAC stereo at 48 kHz, duration 84.000000 s, 9,559,885 bytes. SHA-256 `86dd838c1877214d9950be40ebc555f9cd7b96112570b443f83b5ed495c30449` matches the manifest and original reviewed file. | FFprobe output and `Get-FileHash`; manifest integrity test in FILM-01. |
| FILM-05 | Review `tests/test_film_package.py` and `scripts/serve-review.mjs` failure and recovery paths. | Missing referenced files and changed artifact size/hash fail the focused test with the relative path. The server returns 404 for missing files and 416 for unsatisfiable byte ranges. Restore the committed file or regenerate the artifact, then rerun FILM-01. | Focused source review. |

## Unperformed in-scope checks at cutoff

| Check | Status | Next action |
| --- | --- | --- |
| Required final-head `Merge ready` CI | Pending | Record the run URL, exact tested head SHA and result in the PR testing section before merging. |

## Usability evidence

The local review uses the repository film copy and fictional data. Its chapter buttons identify each section and time; choosing the pipeline chapter immediately seeks and plays. The adjacent gallery link opens the eight style frames, the full-size dialog advances to another frame and closes back to the gallery. The review page offers a clear path back to the 84-second film. No application journey changed.

## Findings and limits

No local failures or skips were observed. The committed MP4 was rendered under HyperFrames 0.8.78; the editable project now pins 0.8.81 and passed composition checks, so a future export may differ slightly. The browser check verifies playback and navigation, not a pixel-by-pixel comparison or a newly rendered export.

## Merge evidence

Pending at this report's cutoff. The PR testing section will retain final-head CI evidence and the GitHub merge record once available. A merge is not a deployment check.
