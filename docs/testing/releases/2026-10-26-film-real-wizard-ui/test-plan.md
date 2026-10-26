# Wizard film, revision 13: the real Wizard app — plan

Baseline: revision 12, `dd70255` ([#155](https://github.com/datap0nd/data_governance/pull/155), not merged, in owner review). [Report](test-report.md).

The owner asked (26 October 2026) to change the film's logo to the Wizard logo and to replace the recreated app screens with timelapses of business questions in **the actual current Wizard UI**, "not a recreation or a simple design", keeping the film dynamic. Revision 13 keeps the drawn opening (data everywhere, hunting, everything in Wizard), puts Wizard's pixel logo in the hub and the end card, and from 0:33 shows screen recordings of the Wizard web app (`datap0nd/wizard` `main` at `ff4a9e2`) running in replay mode over its synthetic fixtures: question 1 with an attached targets workbook, its run as a timelapse synced to the narration, the answer, question 2 as a music-only timelapse, Check my data and the Sources drawer. The voice service is unreachable from the build environment, so every line reuses its revision 12 recording; the line with the Gulf figures is dropped because the real answer does not match it.

| Case | Procedure | Expected |
| --- | --- | --- |
| RU-01 | Run the three capture transcripts through Wizard's API; compare each answer's figures with the tool results; run Check my data. | Every figure in the answer text is in a tool result it cites; Check my data reports 6 of 6 MATCH and marks the answer CHECKED. |
| RU-02 | Record both takes; review frames across each take. | The real app (sidebar, composer, systems stage, answer, chart, Sources drawer). Replay and synthetic development labels and demo suggestions hidden; nothing else altered. Synthetic sign-in "Executive". |
| RU-03 | Remix the narration from `audio/voice-rev12.flac`; check cut points and cues. | 12 lines, each the identical revision 12 recording at speed 1, cut on silence; cue times move with their line; no overlap. |
| RU-04 | Build the screen clips; review encoded frames at each cue. | GSCM, ASAP, NERP and the attached file light up within half a second of "sell-out", "share", "spend" and "targets"; the answer, table and chart are framed on "summary", "numbers" and "charts"; the MATCH rows on "checked"; the security card on "safe". No window edge at zoom; no clipped stage. |
| RU-05 | Logo review. | Hub and end lockup are Wizard's own `brand/logo` SVGs, unchanged, at whole-number scales; the old "W" mark is gone. |
| RU-06 | `hyperframes check` at 25 times. | No errors. |
| RU-07 | Render; probe and decode the MP4; measure loudness. | 84 s, 2520 frames, H.264 1080p30, stereo AAC 48 kHz; clean decode; about −16 LUFS. |
| RU-08 | `tests/test_film_package.py` plus syntax checks for remix.py, screen.py, build.mjs, runtime.js, wizard.js, capture scripts. | Pass; manifest hashes match; review pages resolve local media. |
| RU-09 | Owner review of the cut. | Owner approves or asks for changes; no merge before that. |
| RU-10 | Final-head CI. | Merge ready passes on the reviewed head. |

Synthetic data only; no live portal, AI account, database or deployment involved. No human listening claim.
