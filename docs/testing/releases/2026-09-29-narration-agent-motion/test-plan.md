# Metronome film revisions 4–6 — test plan

[Report](test-report.md). Baseline main: 49f2761c7e9bea154fc625241c6120a983ba1df8. Revision 4: natural initialisms, click alignment and six agent characters. Revision 5: functional report/destination/schedule menus and no-code empowerment. Revision 6: shorter pacing, code-vs-Flow comparison, independent cross-report inspectors, generic powered dashboards, browser/MCP access and a ChatGPT before/after example.

## Cases

| ID | Procedure | Expected result |
| --- | --- | --- |
| FILM-01 | Generate all 13 final sentences, compare local Whisper recognition and synthesis boundaries, check phrase slots, then measure aggregate gaps against v5. | Natural speech, no time compression/truncation/overlap. ASAP/GSCM, two AM and MCP recognizable. About half the inter-sentence gap time removed; 104-second final video. Record recognition ambiguities; do not claim human listening. |
| FILM-02 | Render and inspect code/Flow at 22 and 35.8 sec, agents at 38/42/46 sec, issue at 54, dashboard at 62, access at 77, comparison at 85/93 and table at 101. Run strict check at 19 specified moments including dropdowns and button states. | Code continues while the actual Flow is ready; asap-portal.com appears. Dropdown choices and press/release remain functional. Agents move at independent phases between reports/stages. No METO AI Portal, repeated Excel/questions scene or old server/AI-logo equation. Clear ChatGPT workflow plus final comparison table. Zero unintended clipping/overlap/contrast/runtime findings. |
| FILM-03 | Render H.264/AAC at 1080p30. Decode all frames, inspect encoded scene frames, probe duration and audio, measure true peak and integrated loudness. | 104 seconds, 3,120 frames, stereo AAC 48 kHz, no decoder failures or clipped peaks. 50% approximate gap reduction without speech acceleration. |
| FILM-04 | Run existing film package tests plus syntax for score, build, builder, runtime and review server. Check review/demo HTTP and MP4 byte range. | Two package tests pass with updated 104-second contract, five syntax checks pass, exact manifest hashes and playable media. No deleted/weakened test coverage. |
| FILM-05 | Inspect diff and fresh final-head CI; record exact head and run URL in PR before head-pinned merge. | No app/live-system changes or native desktop automation. ChatGPT reference contains no private account content. Clear synthetic, non-benchmark scope. Merge ready passes for final head. |

## Commands

```text
node scripts/build.mjs
python audio/score.py
film.ps1 check --at '3,12,19.8,22.4,24.5,26,28,29,35.8,38,42,46,54,62,77,85,90,93,101' --strict --json
film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic-v6.mp4 --quiet
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```

Windows ARM64 host; x64 Python 3.13.7; Node 24.19; Chrome 153.0.8010.53; HyperFrames 0.8.81; FFmpeg 7.1. Browser interaction uses Codex in Chrome. Local dropdown overlays keep the documented, narrowly scoped v5 intent markers; robot inspectors now share the fleet coordinate space and need no overflow waiver. The final clip has zero subtitles. The AI comparison demonstrates an illustrative workflow, not measured token savings or guaranteed accuracy.
