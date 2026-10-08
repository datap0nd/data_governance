# Metronome + Wizard showcase — plan

Baseline `97f0abf3b060e9d7292fbc35b329cde3c097fa74` (revision 10). [Report](test-report.md).

Turn the film into a joint showcase. Keep the Metronome opening (portals, extract/transform/load, pipeline controls, no-code Flow builder) with PostgreSQL as the destination. Replace overnight agents, findings, browser/MCP access and the ChatGPT comparison with a bridge (leader questions across NERP, GSCM, ASAP and Metronome's PostgreSQL tables), a recreation of Wizard (ask, streamed steps, answer and chart, Check my data, evidence drawer with the read-only query) and a closing that joins the two products. Regenerate all narration; drive speech-linked reveals from synthesizer word boundaries. Bump HyperFrames 0.8.81 → 0.8.142.

| Case | Procedure | Expected |
| --- | --- | --- |
| MW-01 | Review narration against both repositories: Metronome's AGENTS.md/PRODUCT.md and Wizard's README, AGENTS.md, live-PostgreSQL decision and CEO transcript. | Claims match the code: Metronome Flows load PostgreSQL; Wizard reads approved sources under the user's own Gemini sign-in, queries PostgreSQL read-only, links numbers to evidence and rechecks with Check my data. Wizard's measure is called a proxy, never ROI. No accuracy or time-saving claim. |
| MW-02 | Synthesize all 13 lines with `audio/score.py`; inspect per-line budgets and `assets/cues.json`. | Every line fits at speed 1 without truncation. Each named cue is found in its line's word boundaries. |
| MW-03 | HyperFrames snapshots at every scene, including the Wizard phases (empty, typing, steps with Metronome callout, answer, Check my data, evidence) and the closing. | Layouts match Wizard's app (sidebar, header badges, composer, run card, chart, evidence drawer) and Metronome's existing scenes; no clipped or overlapping text except the deliberate drawer and scroll layering. |
| MW-04 | `hyperframes check` at 22 sampled times on 0.8.142. | Check passes: no lint, runtime, layout, motion or contrast errors. Intentional layering is marked only on the affected elements, only while it applies. |
| MW-05 | Render with HyperFrames; probe and fully decode the MP4; measure loudness; sample encoded frames at the scene boundaries and cue moments. | 120 s, 3600 frames, H.264 1080p30, stereo AAC 48 kHz; full decode clean; speech-linked reveals land on their words. |
| MW-06 | Package tests and syntax checks: `tests/test_film_package.py`, score.py, build.mjs, builder.js, runtime.js, introduction.js, wizard.js, serve-review.mjs. Serve the review pages locally. | Tests and syntax checks pass; manifest hashes match; review pages resolve local media; MP4 served with range support. |
| MW-07 | Final-head CI; record tested SHA/run in the PR. | Merge ready passes before the head-pinned merge. |

Synthetic film only. Wizard's replay demo was run locally in fixture mode for reference; no live portal, Gemini account, PostgreSQL server or deployment is involved. No human listening claim.
