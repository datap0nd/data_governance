# Visual Metronome film and MCP ending — test report

## Revision and environment

Evidence cutoff: 2026-09-29 07:17 UTC. Working changes on base `1a9eb2e01670c4ff6b12925a2d1fec55d23fe146`, branch `codex/visual-film-portals-mcp`. The PR records the final tested SHA and CI URL after this report cutoff.

Windows ARM64; checkout-owned Python 3.13.7 x64, Node 24.19, Chrome 153.0.8010.53, HyperFrames 0.8.81, FFmpeg 7.1. Synthetic portals/data and code-rendered interfaces only. Native desktop automation and live systems are outside scope.

## Results

| Case | Status | Evidence |
| --- | --- | --- |
| VIS-01 | PASS | `node scripts/build.mjs` and `film.ps1 check --at '6,18,31,39,47,59,73,81,88,106,117,126,133,140' --strict --json`: no lint/runtime/layout/contrast errors or warnings. 14 layout samples; 197 contrast checks at five times. [Strict result](../../../../media/metronome-in-sync/renders/revision-check.json). Automated motion analysis was disabled; before/after animation states were inspected visually. |
| VIS-02 | PASS | Generated-markup checks and [14 encoded frame samples](../../../../media/metronome-in-sync/renders/review/encoded-samples.jpg) confirm zero subtitle/brand-corner/chapter elements, no Excel icon/window in scene 1, six requested sources, icon/name-only centered hub and no shared-key claim. Korean report font has zero missing Hangul glyphs. |
| VIS-03 | PASS | Sixteen ordered non-overlapping phrases fit within 0–144 seconds; maximum post-generation speed factor 1.0. Explicit `spoken` spellings for ASAP/GSCM/AM/API/ETL/MCP are retained beside readable English copy. No subtitle generation or review download links remain. Natural-language pronunciation remains subject to listener review. |
| VIS-04 | PASS | Rendered with `film.ps1 render --fps 30 --quality looks --workers 4 --output renders/review/animatic-v3.mp4 --quiet`. FFprobe: 144.000 s, H.264, 1920×1080, 30 fps, 4,320 frames, stereo AAC 48 kHz. Full `ffmpeg -v error -i animatic-v3.mp4 -f null -` decoding passed. Loudnorm: −16.16 LUFS, −1.92 dBTP, 14.90 LU range. Attribution metadata added with stream copy and fast-start; stream timing unchanged. [Measurements](../../../../media/metronome-in-sync/renders/encoded-validation.json). |
| VIS-05 | PASS | Chrome chapter buttons sought to 78 s (expanded finding visibly played) and 122 s (ChatGPT boost visibly played). Review/frame/script/scene-9 HTTP requests returned 200. MP4 byte range returned 206, `bytes 0-1023/11967978`. No subtitle controls or subtitle downloads are present. |
| VIS-06 | PASS | Two existing tests passed; zero failures/errors/skips. Four JS/Python syntax checks passed. Verifier run `20260929T071406561Z-18540-8790388e`, 2026-09-29T07:14:06.561913+00:00 to 2026-09-29T07:14:11.834238+00:00; source fingerprint `b0af2e7171ff9831dfc53cd87f6dff1aad49d41407e779857d993107aed4c9e7`. |
| VIS-07 | PASS | Changed-file/provenance review and staged/base diff whitespace checks. Film/docs plus only the two explicit duration assertions in the existing film test; no app/workflow change. Licensed AI logos and music credited; fictional data and `.example` URLs. |
| VIS-08 | NOT RUN | Final-head CI pending at cutoff. Record successful required `Merge ready`, run URL and exact SHA in the PR before the head-pinned merge. |

Verifier command:

```text
python tools/check.py verify --test tests/test_film_package.py --syntax media/metronome-in-sync/scripts/build.mjs --syntax media/metronome-in-sync/compositions/runtime.js --syntax media/metronome-in-sync/scripts/serve-review.mjs --syntax media/metronome-in-sync/audio/score.py
```

## Resolved findings and limits

Initial strict checks found the overlapping portal composition, an overflowing fix panel, a connector stacking rule and an API label outside its container. Portals were placed side by side and the remaining layout rules corrected. A check observed incomplete audio while its master was rebuilding; validation was repeated only after the 144-second master completed. A snapshot exposed inherited Excel scaling; its full-size layout was restored and inspected before final render. The API narration was shortened so no phrase needed time compression.

The render log reported one non-blocking resource 404 without a URL. Strict runtime checks and full encoded decoding passed; the missing resource was not identified.

The 84-second test expectation was updated to the expanded 144-second story. All existing artifact hashes, PCM format and local-link checks remain active. No test or workflow was removed or bypassed. Git may emit normal LF-to-CRLF checkout notices.

The METO AI Portal/API architecture, Metronome MCP connections and simplified no-code builder illustrate the owner's requested product direction. This film does not establish deployment or implement those features. Auditor behavior remains read-only; the proposed fix is not automatically executed. Chrome pixel identity to a particular release is not claimed. The Excel ribbon comes from the owner's Scribble tour, while portal and worksheet data are synthetic.
