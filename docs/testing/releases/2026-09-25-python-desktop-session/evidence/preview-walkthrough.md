# Fictional Python Flow folder preview walkthrough

Executed 2026-09-25 at 11:51 UTC by `tests/test_python_folder_preview.py`
(Playwright 1.62.0, headless Chromium) against
`app/static/recording-preview/python-folder.html` served from this checkout at
revision `674fabe`. Production `app/static/app.js` renders the Python builder
and the Flows list. The preview replaces the API with fictional in-memory data
and adds the **proposed** "Flow folder" control. Nothing is saved, run or
created on disk, and the application itself does not have this control yet.

| Case | Viewport | Action and observed state | Screenshot |
| --- | --- | --- | --- |
| U-01 | 1280×900 | New Python Flow: **Flow folder** sits below **What should these scripts do?**; **No Metronome folder** is selected with **Just run the scripts**; help: "Nothing is created on disk for this Flow. The scripts run in place, from any folder." No document overflow. | [folder-new-run-default.png](folder-new-run-default.png) |
| U-02 | 1280×900 | Typed the name "Weekly stock refresh" and chose **Managed Metronome folder**: help names `C:\Metronome\Flows\Python\Weekly stock refresh with Downloads and Scripts`. | [folder-new-managed.png](folder-new-managed.png) |
| U-03 | 1280×900 | **Produce a file or SQL table** with **No Metronome folder**: help points to the Output step, where a required **Output folder** field replaced the managed destination; its help says any folder the worker service can write, even while Enforce paths is on. | [folder-new-output-folder.png](folder-new-output-folder.png) |
| U-04 failure | 1280×900 | Entered `reports\stock` and clicked **Create flow**: "Flow not saved: Output folder must be a full path such as \\server\share\folder or D:\Reports." beside the actions; the field was focused; name and script path were kept. | [folder-new-validation.png](folder-new-validation.png) |
| U-04 recovery | 1280×900 | Entered `\\fileserver\reports\stock` and created: status "Flow saved · Preview · no Metronome folder"; the payload carried `managed_folder: false` and that folder; the Python group lists the new Flow with `\\fileserver\reports\stock`. | [folder-list-new-flow.png](folder-list-new-flow.png) |
| U-05 | 1280×900 | Edited the managed "Excel price refresh" Flow: **Managed Metronome folder** was selected; choosing **No Metronome folder** showed "Metronome stops using Python\Excel price refresh. The folder and its files stay on disk…". **Save changes** reported "…no Metronome folder; Python\Excel price refresh stays on disk". **More › Open folder** then said "Excel price refresh has no Metronome folder: its scripts run from C:\Reports\excel_jobs." | [folder-edit-drop-managed.png](folder-edit-drop-managed.png) |
| U-06 | 390×844 | New Python Flow and the Flows list: the choice and its help wrap; no page-wide horizontal overflow on either screen. | [folder-mobile-new.png](folder-mobile-new.png), [folder-mobile-list.png](folder-mobile-list.png) |

No page errors were raised. This is synthetic browser evidence of a proposal
for owner review; it verifies no application behavior.
