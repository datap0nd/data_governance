# Two-column narrated closing comparison — report

[Plan](test-plan.md). Evidence cutoff: 2026-09-29 09:50 UTC. Baseline `926a6408c6c64862e5b75d0e836b8223c1dba1ca`; branch `codex/film-synced-closing`. Final tested commit and CI run are recorded in the PR before merge.

Environment: Windows ARM64 host, x64 Python 3.13.7, Node 24.19, Chrome 153.0.8010.53, HyperFrames 0.8.81, FFmpeg 7.1. Synthetic film only; no app/backend or live-system changes. No native computer use.

| Case | Result | Evidence |
| --- | --- | --- |
| CLOSE-01 | PASS | Two equal columns with four paired rows and readable headers. Strict 11 sampled times, zero lint/runtime/layout/contrast findings, 127 contrast checks. [Output](../../../../media/metronome-in-sync/renders/revision8-check.json). |
| CLOSE-02 | PASS | Cues 87.511, 88.761, 90.862, 91.944 sec use cached WordBoundary offsets minus exact leading PCM trim. Before/after encoded samples show rows hidden before their cue and appearing cumulatively after their cue. [Cue provenance](../../../../media/metronome-in-sync/assets/closing-cues.json), [encoded samples](../../../../media/metronome-in-sync/renders/review/revision8-samples.jpg). Git diff confirms narration text and entire audio directory unchanged from baseline. |
| CLOSE-03 | PASS | Full decode; 95.000 sec, 2,850 H.264 1080p30 frames, stereo AAC 48 kHz, -16.19 LUFS / -1.95 dBTP. Two package tests and five syntax checks passed, zero failures/errors/skips. Run `20260929T094938426Z-10896-a9ed49bb` (2026-09-29T09:49:38.427801+00:00 to 2026-09-29T09:49:41.578144+00:00), fingerprint `c241f970c539b8c2d5a9f2f824d94cf97399f3291695dc108fcca4fd19911c4f`. [Media measurements](../../../../media/metronome-in-sync/renders/revision8-validation.json). HTTP playback results recorded in PR. |
| CLOSE-04 | NOT RUN at cutoff | Fresh final-head CI pending. Record exact tested head and successful Merge ready URL in PR before pinned merge. |

## Commands and findings

```text
film.ps1 check --at '86.9,87.2,87.49,87.8,88.74,89.05,90.84,91.15,91.92,92.25,94.8' --strict --json
film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic-v8.mp4 --quiet
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```

The initial strict check exposed five small-text contrast warnings at 86.9 seconds in the preceding ChatGPT scene (Projects, Pro, create_flow). Three foreground colors were darkened, and the identical check passed. No exemptions, tests or workflows were changed. Render logged non-blocking 404 messages; strict runtime check, media references and full encoded decode passed.

Timing is derived from unchanged synthesis events and exact source trimming; no new ASR or human listening pass is claimed. The comparison is illustrative and does not establish measured token savings or guarantee accuracy. Prior revision evidence remains historical. No deployment claim.
