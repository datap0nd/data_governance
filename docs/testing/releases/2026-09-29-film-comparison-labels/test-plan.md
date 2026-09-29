# Clear comparison labels and dashboard removal — plan

Baseline: eb4f17be4ea6266eecf2a05280574d2e1882b51b (merged revision 6). [Report](test-report.md).

Scope: make the code-vs-Flow scene explicitly Without Metronome / Write code versus With Metronome / No code; remove the full HTML dashboard scene and narration; ripple later scenes/chapter markers earlier by nine seconds, ending at 95 seconds. Preserve existing voice phrases, music and interactions.

| Case | Procedure | Expected |
| --- | --- | --- |
| FILM-01 | Inspect comparison at 22 and 35.8 sec; strict check at 16.1,22.4,24.5,29,35.8,54,56.9,57.2,64,69.9,70.2,84,92. | Clear contrasting panel labels, aligned panel tops, no occlusion/layout/runtime/contrast findings. |
| FILM-02 | Inspect generated scenes, narration and review navigation; inspect frames across 56.9→57.2 and 69.9→70.2. | No dashboard scene or HTML-dashboard speech. Findings transition into two access modes at 57 sec; MCP comparison begins at 70 sec. Twelve unchanged voice phrases fit without overlap or speed changes. |
| FILM-03 | Render, decode/probe and inspect encoded frames/audio; run existing package tests and five syntax checks. | 95 seconds, 2,850 H.264 1080p30 frames, stereo AAC 48 kHz, no clipping/decode errors. Two tests and syntax pass; 95-second media contract and manifest hashes match. HTTP review/demo/range work. |
| FILM-04 | Review diff and fresh final-head CI; record exact SHA/run URL in PR before head-pinned merge. | Merge ready passes for the actual final head. No application/live-system or workflow changes. |

Commands:

```text
node scripts/build.mjs
python audio/score.py
film.ps1 check --at '16.1,22.4,24.5,29,35.8,54,56.9,57.2,64,69.9,70.2,84,92' --strict --json
film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic-v7.mp4 --quiet
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```

The new text is comparison labeling requested by the owner, not narration subtitles. Synthetic scope and prior pronunciation review remain unchanged. Cached audio is reused by text/voice/rate hash, so no new voice preference judgment or ASR run is needed. No human listening pass is claimed.
