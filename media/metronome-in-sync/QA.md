# Revision 14 validation

Wizard concept film for review: data everywhere → hunting for one answer → everything in Wizard → the real Wizard app: ask, Wizard at work, answer, a second question, checked in the same window, safe → every source, one answer.

**Draft cut.** The eleven new narration lines are written, placed and timed, but not voiced: the film's voice comes from `speech.platform.bing.com`, which the build environment's network policy refuses (HTTP 403 at the proxy). In this render those lines are silent; their lengths and word cues are estimates (`audio/remix.py --draft`). The owner asked to keep the same voice, so no other voice stands in.

- **122 seconds / 2:02**, 1080p30 H.264, 3660 frames; stereo AAC 48 kHz. Full decode PASS. −16.25 LUFS / −1.96 dBTP. [Measurements](renders/revision14-validation.json).
- **No sound effects** (owner request): `audio/sfx.py` and the `sfx` list are gone; the score is voice and music only.
- **Narrated app scenes** (owner request): the question is read as it is typed ("In this example, we're going to ask: …"; "Next, let's ask: …"), each question's first word within 0.25 s of the first typed letter, and the narration says what Wizard does while it works, what question 2 found, why we click Check my data, what the check does and where the sources are. With the estimated lengths, the longest pause from 0:33 to the end card is 2.7 s (revision 13: 28.7 s with music only, from 1:01 to 1:30).
- **Stage held still while Wizard works** (owner: "it jitters … especially in the timelapses"): Wizard's auto-scroll moved its stage about 23 px at every tool start and end. `scripts/screen.py` follows the stage frame by frame (checked against the recorder's browser log) and moves each frame back. Frame-to-frame motion in the rendered timelapses: in revision 13, 40 of 524 frames jumped 15 px or more vertically (±33 px every 0.5–1 s); in revision 14, 1 of 563 does, at 55.9, where Wizard's own diagram re-lays out as the attached file joins it.
- **Calm camera** (owner: "too all over the place"): a few eased keyframes per clip, no stops between them, no overshoot, never the window's edge in close-up. Revision 13's runtime clamped 1,033 of its 2,058 app frames, which jerked the camera at the clamp; its fastest frame moved 552 px. Revision 14 clamps none and moves at most 27 px a frame. The sideways whip to question 2 is a cross-fade; the zoom out to each answer spans its dissolve.
- The app scenes are screen recordings of Wizard `main` at `ff4a9e2` over its synthetic fixtures ([capture](capture/README.md)), re-recorded with the question typed before the file is attached. Real time everywhere except Wizard's working stretch: question 1 from 48.3 to 59.7 (2×), question 2 from 76.1 to 81.2 (4×), its check from 92.8 to 95.6 (2×), with the Timelapse pill (speed and real run clock) and blue edge for exactly those stretches.
- HyperFrames 0.8.142 `check` at 59 times: pass, 0 errors, 0 warnings, 2 informational notes (a file card's label during its drop, the app's pull-away off canvas); contrast 56/56 AA. [Check output](renders/revision14-check.json).
- The 87.6 s music bed is extended by three bar-aligned crossfades, at 1:19, 1:36 and 1:53, each at the track's 79 s point and under narration.

No ASR or human listening pass. Figures are synthetic.

[Plan](../../docs/testing/releases/2026-10-27-film-narrated-calm-camera/test-plan.md) · [Report](../../docs/testing/releases/2026-10-27-film-narrated-calm-camera/test-report.md).
