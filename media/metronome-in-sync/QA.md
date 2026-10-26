# Revision 13 validation

Wizard concept film for review: data everywhere → hunting for one answer → everything in Wizard → the real Wizard app: ask, Wizard at work, answer, a second question, checked, safe → every source, one answer.

- **102 seconds / 1:42**, 1080p30 H.264, 3060 frames; stereo AAC 48 kHz. Full decode PASS. −16.15 LUFS / −1.94 dBTP. [Measurements](renders/revision13-validation.json).
- The app scenes are screen recordings of Wizard `main` at `ff4a9e2` over its synthetic fixtures ([capture](capture/README.md)). Question 1's figures match the tool results they cite; its Check my data reports 6 of 6 MATCH.
- Real time everywhere except Wizard's working stretch of each run: question 1 from 44.4 to 54.3 (2×, then 5×), question 2 from 69.0 to 72.8 (6×), the check from 82.6 to 84.4 (5×). Each starts about 3 s after Wizard appears and ends when the answer is done; the Timelapse pill (speed and real run clock) and a blue frame edge show for exactly those stretches.
- 12 narration lines, each the revision 12 recording at speed 1, cut on silence and re-timed (`audio/remix.py`); 34 word cues moved with their lines. [Encoded samples](renders/review/revision13-samples.jpg), taken half a second after each cue, show the matching reveal: GSCM, ASAP, NERP and the attached file light up on their words.
- HyperFrames 0.8.142 `check` at 31 times: pass, 0 errors, 0 warnings; contrast 76/76 AA. [Check output](renders/revision13-check.json).
- The 87.6 s music bed is extended by a bar-aligned crossfade at 0:45, under narration; the bed's level is continuous through it.
- Fixed during review (owner feedback): the first cut also sped up typing, clicks, scrolling and the answers landing; only Wizard's working stretch is sped up now. Also fixed: a stage push that clipped the top node, the conversation title showing behind the timelapse pill, the "Real Wizard app" tag covering the question box, and a score 2.9 s short (loudnorm's sidechain copy ended after the last word; the voice is now padded to the film's length, in `score.py` too).

No ASR or human listening pass. Figures are synthetic.

[Plan](../../docs/testing/releases/2026-10-26-film-real-wizard-ui/test-plan.md) · [Report](../../docs/testing/releases/2026-10-26-film-real-wizard-ui/test-report.md).
