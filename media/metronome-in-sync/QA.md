# Revision 12 validation

Wizard concept film for review: data everywhere → hunting for one answer → everything in Wizard → ask → Wizard finds the reports → a complete, visual answer → checked and safe → every source, one answer.

- **81 seconds / 1:21**, 1080p30 H.264, 2430 frames; stereo AAC 48 kHz. Full decode PASS. −16.08 LUFS / −1.92 dBTP. [Measurements](renders/revision12-validation.json).
- All 13 narration lines fit at speed 1, with the owner's pronunciation (ASAP as a word, N-ERP, smooth GSCM, BDP and PDF). 37 word cues come from the synthesizer's boundaries ([cues](assets/cues.json)); [encoded samples](renders/review/revision12-samples.jpg), taken half a second after each cue, show the matching reveal.
- No report counts or dates appear; the lists rush past on "packed with reports".
- HyperFrames 0.8.142 `check` at 27 times: pass, no errors; contrast 241/241 AA. One lint warning by design: the draw function measures layout for the pointer. [Check output](renders/revision12-check.json).

No ASR or human listening pass. Figures are illustrative.

[Plan](../../docs/testing/releases/2026-10-09-film-wizard-showcase/test-plan.md) · [Report](../../docs/testing/releases/2026-10-09-film-wizard-showcase/test-report.md).
