# Why Metronome exists — report

[Plan](test-plan.md). Evidence cutoff: 2026-09-29 11:10 UTC. Baseline `cc50a1388feeb76f3420ecc52fa80085f39265d4`; branch `codex/film-why-metronome`. Final tested SHA/CI URL recorded in PR before merge.

Windows ARM64, Python 3.13.7, connected Chrome, resvg 2.6.2, FFmpeg 7.1. Synthetic film only; no live portal/backend changes.

| Case | Result | Evidence |
| --- | --- | --- |
| WHY-01 | PASS | Chrome and encoded frames show portals, downloaded reports, date-format standardization, destination database and four pipeline controls. Three new ASR phrases match intended words, including ASAP/GSCM. Exact synthesis boundaries drive Extract/Transform/Load and Build/Schedule/Run now/Run history. |
| WHY-02 | PASS | Badge at x1610,y30 visible throughout 52–72 sec. Samples at 51.95/52.1/55/64.2/68/72.1 confirm before/during/after states and no browser-control overlap. |
| WHY-03 | PASS | 110.000 sec, 3300 frames, H.264 1080p30, stereo AAC 48 kHz. Full decode PASS, −16.14 LUFS / −1.91 dBTP. All 13 phrases fit, speed 1; later ten caches reused and shifted +15 seconds. Two package tests and seven syntax checks pass, no failures/errors/skips. HTTP 200, range 206 `bytes 0-1023/6979890`. Chrome canonical source `why-metronome-10`, duration 110; playback advanced to 9.627 sec. |
| WHY-04 | NOT RUN at cutoff | Final-head CI pending; exact tested SHA and successful Merge ready run required in PR before pinned merge. |

Local run: `20260929T110441221Z-5492-5eb82518`, fingerprint `b3a4a45100ac7b615006710118b6d67021768ddd9b28f9d0139203d89f8d615e`.

Evidence: [media measurements](../../../../media/metronome-in-sync/renders/revision10-validation.json), [encoded samples](../../../../media/metronome-in-sync/renders/review/revision10-samples.jpg), [speech review](../../../../media/metronome-in-sync/audio/pronunciation-review.json), [opening cues](../../../../media/metronome-in-sync/assets/opening-cues.json).

## Method, corrections and limits

Shared introduction SVG renders in HTML and offline resvg. FFmpeg extends prior opening visuals, inserts ETL and controls, shifts later scenes +15 sec, and overlays the badge. Only the first three narration phrases are regenerated. Score retains the credited music. Explicit CFR export has 110-second video and audio.

First longer portal phrase exceeded its budget and was shortened without speeding speech. ASR initially used Python 3.12 against CP313 libraries; repo Python 3.13 succeeded. An initial encode stream duration ended one frame short despite 3300 frames; explicit final CFR corrected it. Badge initially touched the findings browser frame; y30 resolved overlap. A Chrome locator evaluation timed out during playback; fresh snapshot/read-only DOM evaluation succeeded. No fallback browser/native control was used.

No HyperFrames strict run or human listening pass is claimed. ASR validates words, not subjective voice quality. Product scenes/MCP outcomes remain illustrative. This task does not establish production readiness or measured accuracy/token benefits. No deployment changes. Rollback: revert media/docs/test-duration changes together to revision 9.

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/compositions/ai-connection.js --syntax media/metronome-in-sync/compositions/introduction.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```
