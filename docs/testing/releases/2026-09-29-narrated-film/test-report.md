# Narrated Metronome film — test report

## Revision and environment

Evidence cutoff: 29 September 2026, 06:30 UTC. Working changes on base `c16de7cf775a212b89ac463c1b5537b91cd6159f`, branch `codex/narrated-data-platform-film`. This report accompanies the implementation commit; final tested head and CI run are recorded in the PR testing section.

Windows ARM64 host, checkout-owned Python 3.13.7 x64 with locked dependencies, Node.js 24.19, HyperFrames 0.8.81, Chrome 153.0.8010.53, FFmpeg 7.1. Visuals were produced through code, reference assets and Chrome. Browser checks used synthetic illustrative pages. No native desktop capture or live portal testing is part of this revision.

## Results

| Case | Status | Actual result |
| --- | --- | --- |
| FILM-01 | PASS | `node scripts/build.mjs`; `film.ps1 check --at '3,17,25,35,51,59,70,81' --strict --json`: zero lint/runtime/layout/contrast warnings or errors, 260 contrast samples. [Machine-readable evidence](../../../../media/metronome-in-sync/renders/revision-check.json). |
| FILM-02 | PASS | `film.ps1 render --fps 30 --quality looks --workers 2 --output renders/review/animatic-v2.mp4 --quiet`; FFprobe verified H.264, 1920×1080, 30 fps, 2,520 frames, 84.000 s; AAC stereo 48 kHz, 84.000 s. `ffmpeg -v error -i animatic-v2.mp4 -f null -` decoded fully. Loudnorm measured −16.17 LUFS, −1.99 dBTP, 5 LU range. [Measurements](../../../../media/metronome-in-sync/renders/encoded-validation.json). Validated cut replaced canonical `animatic.mp4`. |
| FILM-03 | PASS | 14 cue intervals checked for order, overlap and 0–84 s bounds. FontTools cmap comparison found no missing Korean-caption glyphs. Encoded frames visually show readable two-line captions below the UIs. |
| FILM-04 | PASS | Inspected all [eight encoded scene samples](../../../../media/metronome-in-sync/renders/review/encoded-samples.jpg). Static icon, sources left and organized database right, light UIs, Chrome controls, Excel ribbon/grid, overnight audit and evidence occupy the intended scenes. Exact pixel identity to a specific Chrome build remains unverified. |
| FILM-05 | PASS | Chrome review-page chapter button sought to 42 s; playback visibly advanced in the overnight audit with Korean captions. English/Korean SRT and frame review requests returned HTTP 200; `Range: bytes=0-1023` returned 206 and `Content-Range: bytes 0-1023/7485071`. |
| FILM-06 | PASS | Repository verifier: 2 tests passed, 0 failures/errors/skips; 4 syntax checks passed. Run `20260929T062548782Z-11128-740b229f`, UTC 06:25:48–06:25:57, source fingerprint `528dbcd850faa9df714548e564be89819e52c579ce218bbfb78fa7a28819a11f`. |
| FILM-07 | PASS | `git diff --check` passed. Changed-file and provenance review: film/docs only, illustrative data and `.example` domains, licensed music with credit, no app/test/workflow changes. Git emitted normal LF-to-CRLF checkout notices. |
| FILM-08 | NOT RUN | Final-head GitHub checks pending at report cutoff. Required `Merge ready` success, run URL and exact head SHA must be recorded in the PR before a head-pinned merge. |

Verifier command:

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs --syntax media/metronome-in-sync/audio/score.py
```

Subtitle files were normalized to LF and pinned with local `.gitattributes` so manifest hashes survive Windows/Linux clones. After this packaging fix, the two film tests and audio-script syntax check passed again in run `20260929T062822352Z-16592-6860bb9b` (06:28:22 UTC).

## Findings resolved and limits

Initial ARM64 Python setup could not install locked binary wheels; the checkout was rebuilt using official Python 3.13.7 x64 and setup/verification passed. Initial strict checks identified a missing audio ID, faint labels and a portal overflow; these were corrected before final render. An initial render stalled locating a managed browser; selecting the installed Chrome executable completed the render. Its log included two non-blocking resource 404 messages without URLs; no lint/runtime/layout/contrast issues remained in the strict check and full encoded decoding passed.

Chrome is an HTML/CSS reconstruction, not a verified pixel-perfect native capture. Excel's ribbon is reused from the owner's linked Scribble tour; worksheet data are synthetic. Narration is synthetic English, Korean text is an original translation. The background music is a separately licensed instrumental, not the Scribble audio track. The nightly audit is read-only and evidence-based; the final dashboard is explicitly an illustrative AI reporting workflow. No source-script change affects the application or existing tests.
