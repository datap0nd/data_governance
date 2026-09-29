# English film captions — report

[Plan](test-plan.md). Baseline `97f0abf3b060e9d7292fbc35b329cde3c097fa74`; branch `codex/film-english-captions`. Evidence cutoff 2026-09-29 11:50 UTC. Final tested SHA and CI run recorded in PR before merge.

| Case | Result | Evidence |
| --- | --- | --- |
| CAP-01 | PASS | 38 phrase cues cover all 13 narration segments exactly after punctuation normalization. Original synthesis word events corrected for PCM trimming. No overlaps, max 53 characters, shortest cue 0.953 sec. Acronyms displayed ASAP/GSCM/MCP. |
| CAP-02 | PASS | Twelve encoded samples across opening, builder, agents, access and closing inspected. Complete image at 1760×990 above a 90px strip with 36px English text. In testing preserved. Chrome: duration 110, playing, no media error, source english-captions-11; screenshot confirms visible captions. Editable HTML at 16 sec matches, no console warnings/errors. |
| CAP-03 | PASS | 110 sec, 3300 frames, H.264 1080p30, full decode PASS. Copied AAC hash equals clean master: 080b81e182935ed283e755d361380dc1edb41d0cc9b259cb85fd59bd61378c9f. Two existing package tests and three syntax checks pass, no failures/errors/skips. HTTP page/SRT 200; range 206, bytes 0-1023/6949673. |
| CAP-04 | NOT RUN at cutoff | Final-head Merge ready pending; tested SHA/run required in PR before pinned merge. |

Local run `20260929T114731612Z-16828-0f224ecd`, Python 3.13.7 on Windows ARM64; fingerprint `45904f9fac9a8a17a634be4c46f828a40071a6e65d2f8e5fba29263487c575f1`. [Measurements](../../../../media/metronome-in-sync/renders/revision11-validation.json), [samples](../../../../media/metronome-in-sync/renders/review/revision11-samples.jpg), [cue provenance](../../../../media/metronome-in-sync/captions/en.json).

## Corrections and limits

FFmpeg/libass warned that the font directory also contains an unsupported WOFF2 and a license text file. Outfit TTF loaded and captions rendered correctly. An early probe ran before encoding completed and reported missing MP4 metadata; the probe and full decode passed after encoder exit. The render script now writes a temporary MP4 and replaces the canonical file after success. The initial Chrome tab had closed; a new tab in the same connected Chrome was used. No alternate browser or native automation.

No new narration synthesis, ASR or human listening pass. Audio packet identity proves no audio changes. No HyperFrames render, live-system check or deployment claim. Tests reused without changes. Rollback: revert this commit; clean master retained in renders/review/animatic-clean.mp4.

Command: `python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/scripts/render-captions.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/runtime.js`.
