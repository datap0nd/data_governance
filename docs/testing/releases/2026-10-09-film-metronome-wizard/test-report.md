# Metronome + Wizard showcase — report

[Plan](test-plan.md). Evidence cutoff: 2026-10-08 21:40 UTC. Baseline `97f0abf3b060e9d7292fbc35b329cde3c097fa74`. Final tested SHA and CI URL are recorded in the PR before merge.

Windows 11 Pro, Node 24.21.0, Python 3.13.15, HyperFrames 0.8.142 with Chrome (headless capture), FFmpeg 9.0.1, edge-tts 7.2.8. Synthetic film only; no live portal, Gemini account, PostgreSQL server or backend changes.

| Case | Result | Evidence |
| --- | --- | --- |
| MW-01 | PASS | Narration checked against Metronome's AGENTS.md/PRODUCT.md and Wizard's README, AGENTS.md, `docs/decisions/2026-10-06-live-postgresql.md` and `fixtures/transcripts/ceo-investment-efficiency.json`. Flows load PostgreSQL; Wizard reads approved sources under the user's own Gemini sign-in, queries PostgreSQL in a read-only transaction, links numbers to evidence and re-runs them with Check my data. The measure is named an investment-efficiency proxy; "ROI" appears only in the user's question and in Wizard's "Not ROI" disclaimers. |
| MW-02 | PASS | 13 lines synthesized; natural durations 3.43–9.67 s, each inside its slot, speed 1. 26 cues resolved from word boundaries, e.g. extract 14.168, NERP spend 77.097, Metronome 80.370, Egypt 86.775, Check 95.213, decision 112.038 ([cues.json](../../../../media/metronome-in-sync/assets/cues.json)). |
| MW-03 | PASS | Snapshots at all scenes and Wizard phases inspected. Fixed during review: right-aligned typed text (placeholder kept its width), bar labels drawn inside the bars, a wrapped PostgreSQL tile caption, an uncentred closing diagram. Wizard screens were compared with its replay demo run locally in fixture mode. |
| MW-04 | PASS | `hyperframes check` at 22 times: ok, 0 errors; contrast 257/257 AA after darkening Wizard's muted grey for video. Remaining lint warnings by design: DOM measurement inside the draw callback (chat scroll and pointer) and single-file size. Drawer and scroll layering opt-outs are toggled only while that layering is on screen ([check JSON](../../../../media/metronome-in-sync/renders/revision11-check.json)). |
| MW-05 | PASS | 120.000 s, 3600 frames, H.264 1920×1080 30 fps, AAC 48 kHz stereo; full decode PASS; −16.16 LUFS / −1.88 dBTP / LRA 6.8. [Measurements](../../../../media/metronome-in-sync/renders/revision11-validation.json), [encoded samples](../../../../media/metronome-in-sync/renders/review/revision11-samples.jpg). |
| MW-06 | PASS | Local run `20261008T213635189Z-40304-b4c2fd70`: 2 tests passed, 0 failures/errors/skips, 7 syntax checks. Review pages, scene preview and builder demo HTTP 200; MP4 200 and range 206 `bytes 0-1023/13081785`. |
| MW-07 | NOT RUN at cutoff | Final-head CI pending; tested SHA and Merge ready run recorded in the PR before the pinned merge. |

## Method, corrections and limits

`audio/score.py` now reports every line's budget before failing and writes word cues; `scripts/build.mjs` embeds them, so builds run before and after synthesis. The HyperFrames pin moved from 0.8.81 to 0.8.142 (scripts and installed dependency); `check` passes on the new version. 0.8.142 reserves `data-link`, so the closing paths use `data-close-link`.

The first render completed but could not replace the old MP4: a review server started earlier by another session (PID 26192, port 4392) held it open. The render was repeated to an ignored temporary file and copied over the locked file through a shared write handle; hashes matched before the temporary file was removed. The other session's server was not stopped.

No ASR or human listening pass is claimed; revision 10's pronunciation evidence is removed because every line changed. Wizard figures are synthetic; the PostgreSQL step and its SQL show the intended joint workflow rather than a recorded run. No accuracy or time-saving claim. Rollback: revert the media, docs and test-duration changes together to revision 10.

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/compositions/introduction.js --syntax media/metronome-in-sync/compositions/wizard.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```
