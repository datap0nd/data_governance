# Asset ledger

| Asset | Source and use |
| --- | --- |
| Wizard logo | The owner's Wizard repository, `brand/logo/` at `ff4a9e2`: `wizard-app-icon.svg` (the hub), `wizard-logo.svg` (the end lockup) and `wizard-mark.svg`, copied unchanged into `brand/`. Pixel art drawn by Wizard's `scripts/wizard_art.py`; only Samsung's colours are used, never its logo. |
| Wizard screens | Screen recordings of the real Wizard web app (`main` at `ff4a9e2`) in replay mode over its SYNTHETIC fixtures, made with `capture/` and cut by `scripts/screen.py` into `screen/*.mp4` (3200 × 1800, H.264). Replay and synthetic development labels and demo suggestions were hidden while recording; the film says "Real Wizard app · illustrative data". |
| Palette and type | Wizard's tokens in `apps/web/src/styles.css`, for the drawn scenes. |
| Inter font | [Inter](https://github.com/rsms/inter) via `@fontsource/inter` 5.3.0, SIL OFL 1.1; Latin 400/500/600 subsets, license in `fonts/Inter-OFL.txt`. |
| Icons | Path data after [Lucide](https://lucide.dev) (ISC), as Wizard uses, redrawn inline in `compositions/wizard.js`. Source badges are generic coloured letters, not product logos. |
| Figures and names | Synthetic. The drawn scenes' report and file names are illustrative. The app's figures are what Wizard's tools return from its synthetic fixtures; `capture/files/Q3 targets.xlsx` is a synthetic targets file. |
| Background music | [Happy Beats & Business Moves Vol. 11](https://ende.app/en/song/12876-happy-beats-business-moves-vol-11) by Sascha Ende. [CC BY 4.0](https://ende.app/standard-license), verified 29 Sep 2026. File sourced from `latent-spaces/brag/skills/brag/assets/music`. Trimmed to the film's length, faded and ducked under speech. Credit on review page, MP4 metadata and accompanying credits file. |
| Narration | Microsoft Edge TTS synthetic `en-US-AndrewNeural`, the revision 12 recording (`../audio/voice-rev12.flac`) re-timed line by line; original English copy; narration.json carries the owner's pronunciation (ASAP as a word, N-ERP, smooth initialisms). No real person's voice is cloned. |

Revision 13 replaces the recreated app screens and the old "W" mark with the real app and Wizard's logo; revision 12 removed the Metronome scenes. Both remain in git history.
