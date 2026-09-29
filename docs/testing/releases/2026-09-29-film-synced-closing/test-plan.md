# Two-column narrated closing comparison — plan

Baseline: `926a6408c6c64862e5b75d0e836b8223c1dba1ca` (merged revision 7). [Report](test-report.md).

Scope: rebuild only the closing comparison as two equal columns with four paired rows. Align each reveal to its spoken point using existing narration word boundaries, adjusted for the leading PCM trim. Preserve duration, narration and prior scenes. No subtitles or application changes.

| Case | Procedure | Expected |
| --- | --- | --- |
| CLOSE-01 | Inspect encoded frames at each completed reveal and the final hold. Strict check before/after each cue. | Two columns, readable headings/cells, no clipping/contrast/runtime findings. |
| CLOSE-02 | Check cue provenance against cached WordBoundary offsets and audio trimming; inspect immediately before/after each reveal. | Paired rows appear with retries, time/tokens, accuracy, repeatable Flow; earlier rows stay visible and later rows remain hidden. Speech unchanged. |
| CLOSE-03 | Decode/probe export, verify manifest/references with existing two package tests and five syntax checks; HTTP review and MP4 range. | 95 sec / 2850 frames, H.264 1080p30, stereo AAC, intact unchanged source audio, correct hashes/references and successful playback requests. |
| CLOSE-04 | Final-head CI; record exact SHA and run URL in PR. | Required Merge ready passes before head-pinned merge. |

Commands:

```text
node scripts/build.mjs
film.ps1 check --at '86.9,87.2,87.49,87.8,88.74,89.05,90.84,91.15,91.92,92.25,94.8' --strict --json
film.ps1 snapshot --at '87.8,89.05,91.15,92.25' --no-end
film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic-v8.mp4 --quiet
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/audio/score.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/builder.js --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs
```

The comparison remains illustrative, not a measured accuracy/time/token benchmark. No new narration synthesis or human listening claim. No native computer use or live-system access.
