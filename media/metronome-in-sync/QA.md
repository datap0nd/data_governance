# Revision 8 validation

**95 seconds / 1:35**, H.264 1920×1080 at 30 fps, 2,850 frames; stereo AAC 48 kHz, narration/music unchanged, zero narration subtitles.

- Closing screen rebuilt as two equal columns: **Without Metronome MCP** / **With Metronome MCP**. Four paired rows are revealed cumulatively with the spoken points.
- [Cue evidence](assets/closing-cues.json): 87.511, 88.761, 90.862, 91.944 seconds, aligned to “Fewer”, “Less”, “Accurate”, “in your own repeatable Flow”. Word offsets are corrected by the same 0.104770833-second PCM lead trim as `audio/score.py`. Reveal duration is 0.22 seconds. Exact audio files and narration text are unchanged; no regenerated speech, new ASR or human listening claim.
- [Strict check](renders/revision8-check.json): 11 sampled times, zero lint/runtime/layout/contrast findings, 127 contrast checks passed. Motion analysis disabled. Before/after encoded frames show zero, one, two, three and four visible paired rows.
- [Encoded frames](renders/review/revision8-samples.jpg) · [media evidence](renders/revision8-validation.json). Full decode PASS; -16.19 LUFS / -1.95 dBTP. Two package tests and five syntax checks passed in `20260929T094938426Z-10896-a9ed49bb`.
- Initial strict sampling at 86.9 seconds exposed five low-contrast small labels in the preceding recreated ChatGPT frame. Three text colors were darkened; the identical strict sample set then passed. No check exemptions added.

[Release plan](../../../docs/testing/releases/2026-09-29-film-synced-closing/test-plan.md) · [Report](../../../docs/testing/releases/2026-09-29-film-synced-closing/test-report.md). Synthetic comparison remains illustrative, not a measured benchmark or an accuracy guarantee.
