# Wizard film, revision 14: narrated app scenes, a calm camera, no whooshes — plan

Baseline: revision 13, `c6a35cd` ([#162](https://github.com/datap0nd/data_governance/pull/162), not merged, in owner review). [Report](test-report.md).

Owner feedback on revision 13's fourth cut (27 October 2026): "There's too many whoosh sounds, remove the whoosh sounds. Also, too much silence sometimes. Also the camera panning to the sides and zooms needs work, it's too all over the place. I like that it moves, but sometimes it jitters or moves all over the place, especially in the timelapses. Regarding the silence, when we're typing a question, the voiceover should naturally say: In this example, we're going to ask X (X being the question). And then it explains what wizard is doing. It cant just sit there in silence." Asked whether a different voice could stand in while the speech service is blocked, the owner said to keep the same voice.

Revision 14 removes every synthesized sound effect; adds eleven narration lines so the app scenes are narrated throughout (the question read as it is typed, what Wizard does while it works, question 2's answer, the check and its sources, and the owner's chosen security line); re-records both takes from Wizard `main` `ff4a9e2` with the question typed before the file is attached; holds Wizard's stage still while it works (its own auto-scroll bounced it about 23 px at every tool start and end, which the timelapse sped up into jitter); and replaces the camera with a few eased keyframes per clip that never stop between them, never overshoot and are checked never to show the window's edge (revision 13 clamped them in the runtime, which jerked the camera), plus a cross-fade instead of the sideways whip between the questions.

The new lines need the film's voice from `speech.platform.bing.com`, which this session's network policy first refused (HTTP 403 at the proxy); the first cut was a draft with those lines silent (`audio/remix.py --draft`).

Owner feedback on the draft (27 October 2026): "the camera still jitters a lot inside Wizard" and "entire voice is quite robotic". The owner then allowed the speech host and, from samples, chose `en-US-AndrewMultilingualNeural` (a newer model of the same Andrew voice) for the whole film. The second cut re-voices all 22 lines in it and re-places them by their key words; fixes the camera's zoom, which was stored to two decimals per frame and so stepped 1% about every second on slow push-ins; smooths Wizard's two real-time page scrolls, which the 40 fps screencast sampled at 30 fps turned into uneven steps; and starts the check's stage lock as soon as its card lands.

| Case | Procedure | Expected |
| --- | --- | --- |
| RF-01 | List the sounds in `timeline.json` and the mixers; compare the final mix with the voice-and-music mix. | No `sfx` list, no `audio/sfx.py`; `audio/remix.py` and `audio/score.py` write `audio/score.wav` from voice and music only. |
| RF-02 | Record both takes from Wizard `ff4a9e2` with `capture/record.cjs`; review frames; run the transcripts' figures as in revision 13. | Real app, same hidden development labels; question typed, then (question 1) the file attached, then sent; `geometry.json` logged for both takes; answers and Check my data unchanged (6 of 6 MATCH). |
| RF-03 | `scripts/screen.py --report` and its checks; measure frame-to-frame motion in the rendered timelapses against revision 13's render. | Every lock tracked against the browser log; in the render, no single-frame vertical jumps on the stage during any timelapse (revision 13: about ±33 px every 0.5–1 s). |
| RF-04 | `scripts/screen.py` camera checks; measure the render's frame-to-frame motion across the app scenes. | No close framing shows the window's edge or, under a lock, a fixed bar; no clamping in the runtime; camera speed changes smoothly (no stops between keyframes, no reversals); question 2 cross-fades in. |
| RF-05 | `audio/remix.py --draft`; compare each line's window with its estimate and its neighbours; list silent stretches in the app scenes. | Lines in order, no overlaps; the question lines start as typing begins, with the question's first word at the first typed letter (±0.3 s); no narration gap over about 2.5 s from 0:33 to the end card. |
| RF-06 | Voice every line into bank `rev14` with `audio/voice.py rev14` (en-US-AndrewMultilingualNeural, −3%); `audio/remix.py` without `--draft`; re-run RF-05 with the real lengths. | All 22 lines in the chosen voice; every line fits its window; the question's first word with its first typed letter; systems light up on their names; "click" just before the click. |
| RF-12 | Measure whole-picture motion in the render while Wizard works (three bands, phase correlation), before and after the zoom fix. | No periodic whole-picture jumps during slow camera moves. |
| RF-13 | `screen.py --report` glides; measure the render's motion through both page scrolls. | Each scroll tracked frame by frame; the conversation moves in even steps; the pointer does not jump. |
| RF-07 | `hyperframes check` at about 60 times across the film. | No errors. |
| RF-08 | Render; probe and decode; measure loudness. | 121 s, 3630 frames, H.264 1080p30, stereo AAC 48 kHz; clean decode; about −16 LUFS, true peak below −1 dBTP. |
| RF-09 | `tests/test_film_package.py` plus syntax checks for the changed scripts. | Pass; manifest hashes match; review pages resolve local media. |
| RF-10 | Owner review of the cut. | Owner approves or asks for changes; no merge before that. |
| RF-11 | Final-head CI. | Merge ready passes on the reviewed head. |

Synthetic data only; no live portal, AI account, database or deployment involved. No human listening claim.
