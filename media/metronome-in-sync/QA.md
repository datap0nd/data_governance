# Revision 9 validation

AI access now shows recognizable tool logos, drawing connector lines and a highlighted Metronome MCP node. The prompt and Connected cards are removed.

- **95 seconds / 1:35**, H.264 1920×1080 at 30 fps, 2,850 frames, stereo AAC 48 kHz.
- Connected Chrome review: at 64.25 seconds only the three AI tools are visible; at 64.9 the paths are drawing; at 66 all three paths have dash offset 0 and opacity 1. Screenshots inspected, no console errors/warnings. Video playback checked: duration 95, playing at chapter 57, new `visual-connection-9` source, no media error.
- [Encoded samples](renders/review/revision9-samples.jpg) confirm logos → lines → connected Metronome, with the next scene beginning at 70 seconds. Source is the shared `compositions/ai-connection.js` SVG module. Offline resvg 2.6.2 renders 390 patch frames; FFmpeg overlays only the right access panel between 57 and 70 seconds onto the revision 8 master. No separate browser driver or headless browser was used.
- Full FFmpeg decode PASS. Audio packet SHA-256 matches revision 8 exactly: `SHA256=99a5256c58a458523a9cb9895bdbd448e998244ca5506b9fa2693e6520df3791`. Narration text and source audio unchanged. -16.19 LUFS / -1.95 dBTP. [Measurements](renders/revision9-validation.json).
- Existing two package tests and six syntax checks passed: `20260929T101833466Z-24652-3229fca4`. Manifest hashes and local media references match. No tests/workflows changed.

No new HyperFrames strict check, speech synthesis, ASR or human listening pass is claimed. Browser interaction used connected Chrome only. Historical results remain in their revision-specific files.

[Plan](../../../docs/testing/releases/2026-09-29-film-ai-connection/test-plan.md) · [Report](../../../docs/testing/releases/2026-09-29-film-ai-connection/test-report.md).
