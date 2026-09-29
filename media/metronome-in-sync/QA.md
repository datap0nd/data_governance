# Revision 7 validation

**95 seconds / 1:35**, 1920×1080, 30 fps, 2,850 frames; English narration, background music and zero narration subtitles.

- Explicit **Without Metronome / Write code** and **With Metronome / No code** headings remain visible throughout the comparison, with aligned panel tops and a divider.
- The entire nine-second HTML dashboard scene and its sentence are removed. Findings cut directly to browser/MCP access at 57 seconds. The AI comparison begins at 70 seconds.
- [Strict check](renders/revision7-check.json): 13 sampled times, zero lint/runtime/layout/contrast findings; 152 contrast checks passed. Motion analysis disabled; before/after and cut-boundary frames inspected visually.
- [Encoded samples](renders/review/revision7-samples.jpg) and [measurements](renders/revision7-validation.json): full FFmpeg decode PASS; H.264 1080p30 with stereo AAC 48 kHz; -16.19 LUFS, -1.95 dBTP.
- Twelve remaining narration phrases reuse the identical cached revision 6 audio. The corresponding prior recognition/word-boundary evidence remains in [pronunciation review](audio/pronunciation-review.json). No new ASR or human listening pass claimed. Speech is not sped up, truncated or overlapped.
- Two package tests and five syntax checks passed: `20260929T092502038Z-30376-eac6ce75`. Duration assertions now expect 95 seconds; all media/hash/reference coverage retained.

[Release plan](../../../docs/testing/releases/2026-09-29-film-comparison-labels/test-plan.md) · [Report](../../../docs/testing/releases/2026-09-29-film-comparison-labels/test-report.md). Historical revision 6 results remain in their original release package. Synthetic UI/product-direction scope remains unchanged.
