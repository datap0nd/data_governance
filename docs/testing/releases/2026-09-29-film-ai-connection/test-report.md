# Visual AI connection — report

[Plan](test-plan.md). Evidence cutoff: 2026-09-29 10:19 UTC. Baseline `a79d924325edbbac3e31a7117ffad9a6c8bfcc2e`; branch `codex/film-ai-connection`. Final tested SHA/CI URL are recorded in the PR before merge.

Environment: Windows ARM64 host, Python 3.13.7, Node 24.19, connected Chrome, resvg 2.6.2, FFmpeg 7.1. Synthetic film only; no backend/live-system changes.

| Case | Result | Evidence |
| --- | --- | --- |
| LINK-01 | PASS | Connected Chrome screenshots at 64.25/64.9/66 show tools first, then partial lines, then complete convergence. At 66, all three path offsets are 0 and opacity 1; labels are Through your AI, ChatGPT, Claude, Gemini and Metronome MCP. No console errors/warnings. Playback started at 57, duration 95 and no media error. |
| LINK-02 | PASS | Shared SVG source used in HTML and resvg compositor. [Encoded stages](../../../../media/metronome-in-sync/renders/review/revision9-samples.jpg). 95 sec, 2850 frames, full decode PASS; audio stream packet hash identical to baseline. [Measurements](../../../../media/metronome-in-sync/renders/revision9-validation.json). |
| LINK-03 | PASS | Two package tests and six syntax checks passed, zero failures/errors/skips. Run `20260929T101833466Z-24652-3229fca4` (2026-09-29T10:18:33.467039+00:00 to 2026-09-29T10:18:35.488188+00:00), fingerprint `7c384eb3cca1e9b4edad506b835f11206795a8192c67bd5cbc18dbbb43e3ae9d`. HTTP playback/range recorded in PR. |
| LINK-04 | NOT RUN at cutoff | Fresh final-head CI pending. Exact SHA and successful Merge ready URL required in PR before pinned merge. |

## Method and limits

Offline resvg renders the shared SVG module for 57–70 seconds over a clean background crop from the revision 8 master. FFmpeg overlays the 800×800 patch at (1080,210), re-encodes video with libx264 CRF 18 and copies the AAC stream. This preserves prior animation/timing and avoids any alternate browser-control method. The editable HTML renders the same SVG directly.

Connected Chrome briefly timed out during export; after the encoder completed, a fresh DOM snapshot succeeded and the same documented Chrome action completed. No fallback browser or desktop control was used. A scratch measurement helper initially hit Windows text decoding; rerunning in UTF-8 completed all media checks.

No HyperFrames strict run for this revision; no new narration synthesis/ASR/listening claim. Existing banded background gradient comes from the prior master. The MCP outcomes remain illustrative, not a measured performance/accuracy guarantee. No application deployment claim.

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/compositions/ai-connection.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```
