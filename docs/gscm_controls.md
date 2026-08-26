# GSCM controls: the recorder, the settings file, and how to see what a run did

The GSCM adapter drives a portal that gives it nothing stable to hold on to,
so historically its control ids were guesses observed on one deployment -
and when a guess missed, the whole flow read like a black box. This page
describes the three tools that open it up: a settings file you can edit, a
guided recorder that fills that file by watching *your* clicks, and where to
look when a run fails.

## The settings file (adjust without code)

Every control id the adapter uses is only a default. Before each GSCM
operation the worker reads:

```
<profile-dir>\gscm_controls.json
```

(`<profile-dir>` is the worker's browser profile folder, by default
`%USERPROFILE%\.metronome-flow-browser`.) Any id in this file wins over the
built-in guess, and edits take effect on the **next run - no restart, no
redeploy**. The keys:

```json
{
  "setting_button_id": "mainframe.VFrameSet.TopFrame.form.div_main.form.btn_setting",
  "go_button_id": "mainframe.VFrameSet.TopFrame.Setting1.form.div_favorite.form.btn_go",
  "excel_button_id": "mainframe.VFrameSet.MdiFrame.form.div_frameButton.form.btn_exceldown"
}
```

- `setting_button_id` - the gear that opens the Setting dialog
- `go_button_id` - the `Go >>` button in Setting > Favorite
- `excel_button_id` - the Excel download button on a report's toolbar

Delete a key (or the file) to fall back to automatic detection.

## The recorder (fill the file by clicking)

You never have to find these ids by hand. On the BI desktop, stop the worker
and run:

```
python -m app.flow_worker --teach-controls https://mdscm.sec.samsung.net/nexa/index.html
```

A visible browser window opens on GSCM (sign in if prompted). The console
then names one control at a time - the Setting gear, the Go button, the
Excel button. For each: navigate wherever you need to inside the browser,
**click that control last**, come back to the console, and press Enter.
Type `s` + Enter to skip one. Each recorded click's real component id is
saved into `gscm_controls.json`, and every future run tries your recorded
ids before any built-in guess.

Re-run the recorder any time GSCM changes after an update.

## What runs do when nothing is configured

With no recorded ids, every control lookup follows one shared mechanism, in
this order:

1. the recorded id from `gscm_controls.json` (if any),
2. the built-in default id,
3. **the live Nexacro component tree** - the portal's own registry, queried
   by component name and caption (this is how a renamed or relocated button
   is still found, even one that renders no text),
4. DOM ids shaped like the control, then visible labels, then position.

Every click is verified by its effect (the dialog opening or closing, the
download starting), and a click that lands but is swallowed is re-fired
through Nexacro's own event API.

## Where to look when a run fails (it is not a black box)

- **The run log** (Flows > the run's log in the panel) records every stage:
  navigation, which bookmark opened, each export, each retry, and - since
  the replay feature - whether a file came from the browser or from an HTTP
  replay (`export_transport`).
- **Failure messages carry the screen.** When a control is not found, the
  error lists what actually was on screen, including the ids of candidate
  controls (for Go: `Go-shaped candidates on this screen: ...`). That line
  is exactly what belongs in `gscm_controls.json`.
- **Failure screenshots** are saved to `<profile-dir>\diagnostics\` (the
  newest 20), one per failed run, showing what the browser saw at the moment
  of failure.

## ASAP

ASAP is an HTML portal, so it has no component-id file: its reports,
filters, and export views are discovered by the catalog scan and stored per
report. The run log and diagnostics screenshots above cover ASAP runs the
same way, and the network replay feature applies to both portals.
