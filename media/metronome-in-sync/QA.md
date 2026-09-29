# Narrated film validation — 29 September 2026

The [current MP4](renders/review/animatic.mp4) is a newly rendered 84-second, 1920×1080, 30 fps H.264/AAC film, produced with HyperFrames 0.8.81 and Chrome 153.0.8010.53. It contains 2,520 frames and stereo 48 kHz audio. Full FFmpeg decoding passed. Encoded audio measured −16.17 LUFS integrated, −1.99 dBTP and 5.00 LU loudness range; no clipped peak was measured.

The [eight encoded samples](renders/review/encoded-samples.jpg) were inspected at 3, 17, 25, 35, 51, 59, 70 and 81 seconds. They show the left-to-right source flow, organized Excel dataset, scheduled Flow, overnight auditor, evidence and sourced dashboard. Korean captions are readable below the application surfaces. All 14 cues are ordered, non-overlapping and inside the film duration; the embedded font covers every Korean caption character.

The [strict HyperFrames result](renders/revision-check.json) has zero lint, runtime, layout or contrast errors/warnings at those eight times (260 contrast samples). [Encoded media measurements](renders/encoded-validation.json) provide codec, timing and audio evidence. The render log reported two non-blocking resource 404 messages without URLs; the strict source check and complete encoded-media validation passed.

The review page was opened in Chrome, the overnight chapter button sought to 42 seconds, and playback visibly advanced with Korean captions. Subtitle/frame-page HTTP requests returned 200 and the MP4 byte-range request returned 206. Existing film-package tests passed (2 tests, no skips), as did the four applicable syntax checks.

Chrome is reconstructed in code; pixel identity to a specific Chrome build has not been established. Excel uses ribbon pixels from the owner's Scribble tour and a newly authored worksheet. Data and the AI reporting dashboard are illustrative. See the [asset ledger](assets/LEDGER.md) for provenance and music credit.

The [release test plan](../../../docs/testing/releases/2026-09-29-narrated-film/test-plan.md) and [report](../../../docs/testing/releases/2026-09-29-narrated-film/test-report.md) record local evidence. Final-head CI evidence is recorded in the PR before merging.
