# Capturing the real Wizard app

From revision 13 the film shows the actual Wizard web app, not a recreation. These files record it.

Wizard runs locally in its **replay** runtime: the model's turns come from a transcript here, but every tool call is
Wizard's own (same registry, rights, evidence and checks) over its SYNTHETIC fixtures, and the screen is the real web
app (`apps/web`) driven in Chromium. The figures are therefore exactly what Wizard's tools return from synthetic data;
each answer's text was written from those results.

| File | What it is |
| --- | --- |
| `make_transcripts.py` → `transcripts/` | Four replay transcripts: "How did we do this quarter against our targets?" (with `files/Q3 targets.xlsx` attached), "Where is stock building up in the channel?", and **Check my data** for each answer (the film shows the second answer's; its six figures all MATCH). Tool calls follow the narration's order (GSCM, ASAP, NERP, then the Excel file). |
| `files/Q3 targets.xlsx` | Synthetic Q3 sell-out targets by market, written by `make_transcripts.py` when openpyxl is available. |
| `identities.json` | One synthetic sign-in, "Executive" (CEO rights). |
| `serve.py` | Starts Wizard from a checkout with those transcripts. It paces the replay like a live answer: a pause before each tool call, and each tool taking about as long as a real read. It wraps Wizard's classes at run time and changes nothing in Wizard. |
| `record.cjs` | Drives the app with Playwright at 1600 × 900 and records Chrome's screencast frames (1600 × 900; headless Chrome sends them at CSS size even at device scale 2) with their timestamps, plus markers (typed, attached, sent, answer done, check clicked, sources opened). It types the question first and then, for question 1, attaches the file, so the narration can read the question as it is typed. Every animation frame it also logs where the newest run's systems stage sits (`geometry.json`), which `scripts/screen.py` uses to hold the stage still while Wizard works. |
| `takes/<take>/markers.json`, `geometry.json` | The markers and the run's logged tool calls for each recorded take, and the stage's logged position. The frames themselves (about 500 MB) are not committed. |

Capture-only presentation, applied with a stylesheet when recording: the development labels "REPLAY · not a live
model" and "SYNTHETIC", the synthetic-build notice and the replay demo suggestions are hidden (a live install shows none
of them), " · replay mode" is removed from the account line, the floating "Latest" button is hidden, and a cursor is
drawn. Nothing else on the page is altered. The film itself says "Real Wizard app · illustrative data" throughout.

Recorded from Wizard `main` at `ff4a9e2` on 27 October 2026 (revision 14), both takes on one fresh server, question 1 first.

```bash
# In a Wizard checkout: uv sync && npm --prefix apps/web ci && npm --prefix apps/web run build
uv run --project /path/to/wizard python capture/make_transcripts.py     # transcripts (and the workbook)
uv run --project /path/to/wizard python capture/serve.py --wizard /path/to/wizard &
node capture/record.cjs quarter      # question 1 (the film uses it up to the answer)
node capture/record.cjs stock        # question 2, its check and its sources, in the same session (the sidebar lists question 1)
python scripts/screen.py             # cut the takes into assets/screen/*.mp4 (see the README)
```
