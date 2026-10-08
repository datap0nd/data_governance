# Wizard showcase film — plan

Baseline `97f0abf3b060e9d7292fbc35b329cde3c097fa74` (revision 10). [Report](test-report.md).

Replace the Metronome film with a concept film about Wizard for a non-technical audience (revision 12, after a first Metronome + Wizard draft in this PR). Story: our data is spread across ASAP, GSCM, BDP, NERP, Excel, email, presentations and PDFs; finding one answer means hunting by hand; Wizard brings every source together, so you ask in plain words and get a checked, visual answer from authorized local AI. Plain language only (no SQL, queries or vendor AI names). A simplified, more visual Wizard screen: one question box, sources, reports found, a dashboard with summary, key numbers, bar chart and donut. Metronome scenes and assets are removed. HyperFrames 0.8.81 → 0.8.142.

| Case | Procedure | Expected |
| --- | --- | --- |
| WZ-01 | Review the narration and on-screen text against the owner's brief and Wizard's repository. | Plain language; "database", "local AI" and "authorized AI", never SQL or vendor names. Claims stay within Wizard's behaviour: it finds reports across approved sources, links numbers to their source report, rechecks them, shows only what the user may see and never changes data. Counts and figures marked illustrative where they are placeholders. |
| WZ-02 | Synthesize all 13 lines; inspect budgets and `assets/cues.json`. | Every line fits at speed 1; every cue word, including repeated letters (`P@3`), resolves in its line's word boundaries. |
| WZ-03 | Snapshots across every scene and the encoded samples after each cue. | Each reveal lands on its word; no clipped or overlapping text except the deliberate window cascade and the security card; the hunt highlights the reports a person would need. |
| WZ-04 | `hyperframes check` at 27 sampled times on 0.8.142. | Check passes with no errors. |
| WZ-05 | Render; probe and fully decode the MP4; measure loudness. | 84 s, 2520 frames, H.264 1080p30, stereo AAC 48 kHz; clean decode. |
| WZ-06 | `tests/test_film_package.py` plus syntax checks for score.py, build.mjs, runtime.js, wizard.js and serve-review.mjs. | Pass; manifest hashes match; review pages resolve local media. |
| WZ-07 | Owner review of the cut before merge. | Owner approves or requests changes; no merge before that. |
| WZ-08 | Final-head CI; record tested SHA/run in the PR. | Merge ready passes before the head-pinned merge. |

Synthetic film only; no live portal, AI account, database or deployment involved. No human listening claim.
