# Revision 13 validation

Wizard concept film for review: data everywhere → hunting for one answer → everything in Wizard → the real Wizard app: ask, timelapse, answer, a second question, checked, safe → every source, one answer.

- **84 seconds / 1:24**, 1080p30 H.264, 2520 frames; stereo AAC 48 kHz. Full decode PASS. −16.14 LUFS / −1.97 dBTP. [Measurements](renders/revision13-validation.json).
- The app scenes are screen recordings of Wizard `main` at `ff4a9e2` over its synthetic fixtures ([capture](capture/README.md)). Question 1's figures match the tool results they cite; its Check my data reports 6 of 6 MATCH.
- 12 narration lines, each the revision 12 recording at speed 1, cut on silence and re-timed (`audio/remix.py`); 34 word cues moved with their lines. [Encoded samples](renders/review/revision13-samples.jpg), taken half a second after each cue, show the matching reveal: GSCM, ASAP, NERP and the attached file light up on their words.
- HyperFrames 0.8.142 `check` at 25 times: pass, 0 errors, 0 warnings; contrast 75/75 AA. [Check output](renders/revision13-check.json).
- Fixed during review: a stage push that clipped the top node, the conversation title showing behind the timelapse pill, the "Real Wizard app" tag covering the question box, and a score 2.9 s short (loudnorm's sidechain copy ended after the last word; the voice is now padded to the film's length, in `score.py` too).

No ASR or human listening pass. Figures are synthetic.

[Plan](../../docs/testing/releases/2026-10-26-film-real-wizard-ui/test-plan.md) · [Report](../../docs/testing/releases/2026-10-26-film-real-wizard-ui/test-report.md).
