# Revision 6 validation

**104 seconds, 1080p30, 3,120 frames, zero subtitles.** The previous cut was 144 seconds. Gaps outside trimmed narration phrases fall from 63.067 to 31.154 seconds (50.6% reduction). This measures inter-sentence, lead and tail gaps, not pauses within a spoken sentence. Music remains continuous.

- [Strict check](renders/revision6-check.json): 19 sampled times, zero lint/runtime/layout/contrast errors, warnings or information findings; 248 contrast checks passed. Motion analysis was disabled; sampled motion states were visually inspected.
- [Encoded scene samples](renders/review/revision6-samples.jpg): code continues while the Flow completes, inspectors occupy different stages and lanes, generic dashboards, two access modes, ChatGPT retries versus guided creation, final comparison table.
- [Media measurements](renders/revision6-validation.json): full FFmpeg decode passed; H.264 1920×1080 30 fps, stereo AAC 48 kHz, 104.000 seconds, -16.19 LUFS and -1.95 dBTP.
- [Narration review](audio/pronunciation-review.json): all 13 final phrases checked with local Whisper base.en and synthesis word boundaries. No time compression, truncation or overlapping speech. ASAP/GSCM, two AM and MCP recognized. Recognition homophones and a trailing artifact are retained. No human listening pass claimed.
- Two existing package tests and five syntax checks passed: `20260929T090032959Z-30724-74bb9ae8`. Duration assertions changed from 144 to 104 seconds to match the requested shorter film; hash, media and reference checks remain intact.

Dropdowns retain narrowly scoped markers for their intentional layering and four scrollable offscreen choices. Robot inspectors now share the fleet coordinate frame; no robot overflow exemption is used. Chrome and ChatGPT are recreated in code from visual references, with synthetic content. The MCP comparison is illustrative, not a benchmark or accuracy guarantee. [Plan](../../../docs/testing/releases/2026-09-29-narration-agent-motion/test-plan.md) · [Report](../../../docs/testing/releases/2026-09-29-narration-agent-motion/test-report.md).
