# Wizard showcase film — report

[Plan](test-plan.md). Evidence cutoff: 2026-10-08 22:50 UTC. Baseline `97f0abf3b060e9d7292fbc35b329cde3c097fa74`. Final tested SHA and CI URL are recorded in the PR before merge.

Windows 11 Pro, Node 24.21.0, Python 3.13.15, HyperFrames 0.8.142 with its bundled chrome-headless-shell 152, FFmpeg 9.0.1, edge-tts 7.2.8. Synthetic film only.

| Case | Result | Evidence |
| --- | --- | --- |
| WZ-01 | PASS | Narration and screens use plain language; no SQL, query or vendor AI name appears. "Local AI" and "authorized AI" follow the owner's direction; "has learned our portals" stands for Wizard's knowledge of which report holds what. Security lines match Wizard's read-only, permission-aware design. At the owner's request no report counts or dates appear; the lists rushing past and a short scrollbar thumb carry the volume. |
| WZ-02 | PASS | 13 lines, natural 1.91–9.40 s, all inside their slots at speed 1; 37 cues resolved. Pronunciation follows the owner's choices from rendered samples: ASAP as a word, "N-ERP", and GSCM, BDP and PDF as smooth initialisms. Unspaced spellings measured shorter than spaced letters (GSCM 1.0 s against 1.5 s), which is the smoother delivery the owner asked for. |
| WZ-03 | PASS | Snapshots and [encoded samples](../../../../media/metronome-in-sync/renders/review/revision12-samples.jpg) reviewed. Fixed during review: badge letters inheriting span styles, join lines crossing the Wizard name, hunt rows chosen at random, the security card covering the summary, list rows clipping under headers without a divider. |
| WZ-04 | PASS | 27 samples: ok, 0 errors; contrast 241/241; one lint warning (layout measured for the pointer). Report lists and the search shimmer are marked as intentionally layered ([check JSON](../../../../media/metronome-in-sync/renders/revision12-check.json)). |
| WZ-05 | PASS | 81.000 s, 2430 frames, H.264 1920×1080 30 fps, AAC 48 kHz stereo; full decode PASS; −16.08 LUFS / −1.92 dBTP ([measurements](../../../../media/metronome-in-sync/renders/revision12-validation.json)). |
| WZ-06 | PASS | Package tests and syntax checks: run ID recorded in the PR for the pushed head. |
| WZ-07 | NOT RUN at cutoff | Awaiting owner review of the cut. |
| WZ-08 | NOT RUN at cutoff | Final-head CI pending. |

## Method, corrections and limits

The first render attempt failed because HyperFrames timed out probing desktop Chrome; on Windows `chrome.exe --version` starts a browser when none is running. A manual probe opened one Chrome window, which was closed. Renders now use HyperFrames' bundled headless browser. The swap step of that failed attempt emptied the review MP4 before the render existed; later renders replaced it and the swap now runs only after a successful render. The review MP4 is held open by another session's review server on port 4392, so the file is replaced in place through a shared write handle and verified by hash.

No ASR or human listening pass. Figures and report names are illustrative. Rollback: revert the media, docs and test-duration changes together to revision 10.

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/compositions/wizard.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```
