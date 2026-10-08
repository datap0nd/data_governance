# Revision 12 validation

Wizard concept film for review: data everywhere → hunting for one answer → everything in Wizard → ask → Wizard finds the reports → a complete, visual answer → checked and safe → every source, one answer.

- **84 seconds / 1:24**, 1080p30 H.264, 2520 frames; stereo AAC 48 kHz. Full decode PASS. −16.05 LUFS / −1.93 dBTP. [Measurements](renders/revision12-validation.json).
- All 13 narration lines fit at speed 1. 36 word cues come from the synthesizer's boundaries ([cues](assets/cues.json)); [encoded samples](renders/review/revision12-samples.jpg), taken half a second after each cue, show the matching reveal.
- HyperFrames 0.8.142 `check` at 27 times: pass, no errors; contrast 317/318 AA, the one warning on a report row still fading in. One lint warning by design: the draw function measures layout for the pointer. [Check output](renders/revision12-check.json).
- Two package tests and five syntax checks PASS: `20261008T221231111Z-22832-68679680`.

No ASR or human listening pass. Figures are illustrative; BDP and NERP report counts are placeholders.

[Plan](../../docs/testing/releases/2026-10-09-film-wizard-showcase/test-plan.md) · [Report](../../docs/testing/releases/2026-10-09-film-wizard-showcase/test-report.md).
