# Clear comparison labels and dashboard removal — report

[Plan](test-plan.md). Evidence cutoff: 2026-09-29 09:25 UTC. Baseline `eb4f17be4ea6266eecf2a05280574d2e1882b51b`; working branch `codex/film-clear-comparison`. Final tested head/CI URL are recorded in the PR before merge.

Environment: Windows ARM64 host, x64 Python 3.13.7, Node 24.19, Chrome 153.0.8010.53, HyperFrames 0.8.81 and FFmpeg 7.1. Synthetic film only; no app or live-system changes and no computer/browser UI automation in this revision.

| Case | Result | Evidence |
| --- | --- | --- |
| FILM-01 | PASS | Both comparison labels visible/readable at 22 and 35.8 seconds; panel tops aligned. Strict 13 samples: zero lint/runtime/layout/contrast findings and 152 contrast checks. [Strict output](../../../../media/metronome-in-sync/renders/revision7-check.json). |
| FILM-02 | PASS | Seven scene definitions; dashboard markup and spoken sentence absent. Encoded boundary samples 56.9/57.2 and 69.9/70.2 show findings → access → AI comparison, without a missing-scene interval. Twelve cached voice phrases retain exact speech, fit their slots and do not overlap. [Encoded frames](../../../../media/metronome-in-sync/renders/review/revision7-samples.jpg). |
| FILM-03 | PASS | 95.000 sec, 2,850 H.264 frames at 1080p30, stereo AAC 48 kHz. Full decode PASS. -16.19 LUFS / -1.95 dBTP. Two package tests passed, zero failures/errors/skips; five syntax checks passed. Run `20260929T092502038Z-30376-eac6ce75` (2026-09-29T09:25:02.039239+00:00 to 2026-09-29T09:25:04.203400+00:00); fingerprint `d9a42c11cbd57032ccda1f81f78c5fa6a018631d1f59f904bb3ca1482a5d702a`. [Media measurements](../../../../media/metronome-in-sync/renders/revision7-validation.json). Manifest hashes match; HTTP results recorded in PR. |
| FILM-04 | NOT RUN at cutoff | Fresh final-head CI pending. Record Merge ready, tested SHA and run URL in PR before head-pinned merge. |

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
film.ps1 check --at '16.1,22.4,24.5,29,35.8,54,56.9,57.2,64,69.9,70.2,84,92' --strict --json
film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic-v7.mp4 --quiet
```

## Limits and retained evidence

The labels are requested comparison headings, not speech subtitles. Existing intentional dropdown layering/scroll annotations are unchanged. Twelve voice files were reused by exact spoken-text/voice/rate hash; prior recognition/word-boundary evidence was remapped after removing the eighth sentence. No new ASR run or human listening pass is claimed. Review navigation and scene-frame cards omit the removed dashboard. Historical image assets/results remain for prior revisions but are not referenced by the current review or film.

The duration test changes 104 to 95 seconds to match the requested deletion; assertions for hashes, audio encoding and page references remain unchanged. No tests/workflows were weakened or skipped. The illustrative MCP comparison is not a measured token-saving or accuracy guarantee. No deployment or application-feature claim is made.

An initial package run (`20260929T092354279Z-28396-bf65ad48`) was started before render finalization and failed because the manifest still reported 104 seconds. After the 95-second render was finalized and its manifest regenerated, the same affected set passed as recorded above.

CI run 36549160082 stopped at the scope gate because this new report had an extra blank line at EOF. The formatting was corrected before the final-head rerun.
