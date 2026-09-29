# Revision 10 validation

Opening: portals → downloaded reports → extract, transform and load → Metronome pipelines. Build, Schedule, Run now and Run history appear on narration cues. Top-right **In testing** remains throughout overnight agents and findings, 52–72 sec.

- **110 seconds / 1:50**, 1080p30 H.264, 3300 frames; stereo AAC 48 kHz. Full decode PASS. −16.14 LUFS / −1.91 dBTP. [Measurements](renders/revision10-validation.json).
- Connected Chrome scenes and canonical playback checked: duration 110, source `why-metronome-10`, playback advanced to 9.627 sec. No scene-review console errors/warnings. [Encoded samples](renders/review/revision10-samples.jpg) confirm cuts and clear badge placement.
- Three new voice phrases recognized correctly by ASR, including ASAP/GSCM. Ten later voice caches reused, shifted 15 sec. All 13 fit at speed 1 without overlap. [Speech evidence](audio/pronunciation-review.json).
- Two package tests/seven syntax checks PASS: `20260929T110441221Z-5492-5eb82518`. Duration assertions updated to 110. HTTP 200 and range 206.

Offline resvg/FFmpeg composition; connected Chrome is the only browser-control surface. No new HyperFrames or human listening pass claimed. Product readiness was not audited.

[Plan](../../../docs/testing/releases/2026-09-29-film-problem-statement/test-plan.md) · [Report](../../../docs/testing/releases/2026-09-29-film-problem-statement/test-report.md).
