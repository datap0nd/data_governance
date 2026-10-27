# Revision 13 validation

Wizard concept film for review: data everywhere → hunting for one answer → everything in Wizard → the real Wizard app: ask, Wizard at work, answer, a second question, checked in the same window, safe → every source, one answer.

- **109 seconds / 1:49**, 1080p30 H.264, 3270 frames; stereo AAC 48 kHz. Full decode PASS. −16.15 LUFS / −1.93 dBTP. [Measurements](renders/revision13-validation.json).
- The app scenes are screen recordings of Wizard `main` at `ff4a9e2` over its synthetic fixtures ([capture](capture/README.md)). Each answer's figures match the tool results they cite; the second answer's Check my data reports 6 of 6 MATCH.
- Real time everywhere except Wizard's working stretch of each run: question 1 from 44.4 to 54.3 (2×, then 5×), question 2 from 69.5 to 73.3 (6×), its check from 86.1 to 90.4 (2×). Each starts about 3 s after Wizard appears and ends when the answer is done; the Timelapse pill (speed and real run clock) and a blue frame edge show for exactly those stretches.
- The check runs in the window that shows the answer it checks: the scroll to Check my data, the click and the check's first seconds play in real time, and "Every number is checked against its source report" plays over the six MATCH rows and the Sources drawer, both in real time.
- 12 narration lines, each the revision 12 recording at speed 1, cut on silence and re-timed (`audio/remix.py`); 34 word cues moved with their lines. [Encoded samples](renders/review/revision13-samples.jpg), taken half a second after each cue, show the matching reveal.
- HyperFrames 0.8.142 `check` at 35 times: pass, 0 errors, 0 warnings; contrast 79/79 AA. [Check output](renders/revision13-check.json).
- The 87.6 s music bed is extended by two bar-aligned crossfades, at 0:45 and 1:36, both under narration; the bed's level stays within the track's own beat-to-beat range through each.
- Fixed during review (owner feedback): the first cut also sped up typing, clicks, scrolling and the answers landing; the second cut sped up the check (5×) and slid back to the first answer for it. Now only Wizard's working stretch is sped up, the check plays at 2× in the second answer's own window, and the security card holds its third line before it closes.
- Open: the owner's new security line ("It's secure by design: authorized AI that only shows what you're allowed to see, and never changes your data.") is not voiced yet; the speech service is unreachable from this session. The card still plays under the revision 12 line.

No ASR or human listening pass. Figures are synthetic.

[Plan](../../docs/testing/releases/2026-10-26-film-real-wizard-ui/test-plan.md) · [Report](../../docs/testing/releases/2026-10-26-film-real-wizard-ui/test-report.md).
