# Asset ledger

| Asset | Source and use |
| --- | --- |
| Metronome icon, light palette, Outfit fonts | This repository's brand and `app/static/style.css`; static icon, no oscillator animation. |
| Wizard mark, palette and interface | The owner's Wizard repository: the favicon "W" mark in `apps/web/index.html`, tokens in `apps/web/src/styles.css`, layout and copy from `apps/web/src/components` and `services/api/wizard_api/app.py`. Screens were checked against Wizard's replay demo, run locally in fixture mode on 9 Oct 2026. Recreated in code; no screenshot is embedded. |
| Wizard figures | Wizard's synthetic `fixtures/transcripts/ceo-investment-efficiency.json` (EG 14,667, SA 10,000, AE 9,615 extra units per USD 1M; a proxy, not ROI). The PostgreSQL step, the `bi_reporting.psi_combined` query text and its retrieval details are illustrative. |
| Inter font | [Inter](https://github.com/rsms/inter) via `@fontsource/inter` 5.3.0, SIL OFL 1.1; Latin 400/500/600 subsets, license in `fonts/Inter-OFL.txt`. |
| Lucide-style icons | Path data after [Lucide](https://lucide.dev) (ISC), as Wizard uses, redrawn inline in `compositions/wizard.js`. |
| Chrome frame | Newly authored Windows Chrome recreation with tab strip, navigation, tune icon, omnibox, bookmark, extensions, profile, menu and window controls. Reference: [Chromium toolbar](https://www.chromium.org/user-experience/toolbar/) and [Chrome refresh](https://blog.google/products-and-platforms/products/chrome/google-chrome-new-features-redesign-2023/). No native capture or pixel-identity claim. |
| Background music | [Happy Beats & Business Moves Vol. 11](https://ende.app/en/song/12876-happy-beats-business-moves-vol-11) by Sascha Ende. [CC BY 4.0](https://ende.app/standard-license), verified 29 Sep 2026. File sourced from `latent-spaces/brag/skills/brag/assets/music`. Trimmed to the film's 120 seconds, faded and ducked under speech. Credit on review page, MP4 metadata and accompanying credits file. |
| Narration | Microsoft Edge TTS synthetic `en-US-AndrewNeural`; original English copy with letter-by-letter pronunciation guidance in narration.json. No real person's voice is cloned. |
| Korean report-portal font | [Noto Sans KR](https://github.com/google/fonts/tree/main/ofl/notosanskr), SIL OFL 1.1; license in `fonts/NotoSansKR-OFL.txt`. A 600-weight subset containing the Korean report labels and Latin characters is embedded. |
| Diagrams, questions, data and Korean report labels | Original material for this revision. Fictional figures and reserved `.example` domains. |

The owner requested zero subtitles; the film uses Korean only in portal UI tables. Revision 11 removes the Excel ribbon reference, the ChatGPT interface recreation and the ChatGPT/Claude/Gemini logos, with their files.
