# Capturing the real Wizard app

Revision 13 shows the actual Wizard web app, not a recreation. These files record it.

Wizard runs locally in its **replay** runtime: the model's turns come from a transcript here, but every tool call is
Wizard's own (same registry, rights, evidence and checks) over its SYNTHETIC fixtures, and the screen is the real web
app (`apps/web`) driven in Chromium. The figures are therefore exactly what Wizard's tools return from synthetic data;
each answer's text was written from those results.

| File | What it is |
| --- | --- |
| `make_transcripts.py` → `transcripts/` | Three replay transcripts: "How did we do this quarter against our targets?" (with `files/Q3 targets.xlsx` attached), "Where is stock building up in the channel?", and the first answer's **Check my data**. Tool calls follow the narration's order (GSCM, ASAP, NERP, then the Excel file). |
| `files/Q3 targets.xlsx` | Synthetic Q3 sell-out targets by market, written by `make_transcripts.py` when openpyxl is available. |
| `identities.json` | One synthetic sign-in, "Executive" (CEO rights). |
| `serve.py` | Starts Wizard from a checkout with those transcripts. It paces the replay like a live answer: a pause before each tool call, and each tool taking about as long as a real read. It wraps Wizard's classes at run time and changes nothing in Wizard. |
| `record.cjs` | Drives the app with Playwright at 1600 × 900, device scale 2, and records Chrome's screencast frames with their timestamps, plus markers (typed, sent, answer done, check clicked, sources opened). |
| `takes/<take>/markers.json` | The markers and the run's logged tool calls for each recorded take. The frames themselves (about 600 MB) are not committed. |

Capture-only presentation, applied with a stylesheet when recording: the development labels "REPLAY · not a live
model" and "SYNTHETIC", the synthetic-build notice and the replay demo suggestions are hidden (a live install shows none
of them), " · replay mode" is removed from the account line, the floating "Latest" button is hidden, and a cursor is
drawn. Nothing else on the page is altered. The film itself says "Real Wizard app · illustrative data" throughout.

Recorded from Wizard `main` at `ff4a9e2` (26 October 2026).

```bash
# In a Wizard checkout: uv sync && npm --prefix apps/web ci && npm --prefix apps/web run build
uv run --project /path/to/wizard python capture/make_transcripts.py     # transcripts (and the workbook)
uv run --project /path/to/wizard python capture/serve.py --wizard /path/to/wizard &
node capture/record.cjs quarter      # question 1, its check and its sources
node capture/record.cjs stock        # question 2, in the same session (the sidebar lists question 1)
python scripts/screen.py             # cut the takes into assets/screen/*.mp4 (see the README)
```
