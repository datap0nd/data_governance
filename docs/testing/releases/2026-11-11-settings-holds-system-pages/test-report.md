# 2026-11-11: Settings holds the System configuration pages: test report

- Plan: [test-plan.md](test-plan.md)
- Change/PR: link in the PR's testing section
- Evidence cutoff (UTC): 2026-11-11 14:00
- Tested code revision: `8ec5a66e38f5e4be9d5f9e45cdea8d56bee7c2ac` on
  `wip/settings-move`, built on `f37800c`. The commit that adds this report
  changes only files under `docs/testing/`
- Environment: Linux 6.18 (cloud container), Python 3.13.18 from the
  checkout-owned `.venv` of the main checkout with the locked CI
  dependencies (pytest 9.1.1, Playwright for Python 1.62.0), Node 22.23.4.
  One browser for everything: Chromium 153.0.8010.12, the container's own
  build, driven by Playwright for Python in the tests and by the container's
  Playwright for Node 1.63.0 in the walk. The Chrome and Edge channels are
  not installed
- Overall finding: every case in the plan that could run here passes
  (automated and synthetic), and the page walk on a temporary instance found
  no page error, no failed request and no unexpected response. The walk
  found one defect while this
  was being built, a registry row left stale after a save on Flow workers;
  it is fixed and covered (see Findings). Seven existing browser test files
  ask for the Chrome channel by name and could not run here; they and the
  full regression are left to the pull request's CI. Nothing was checked on
  Windows, with Chrome or Edge, or on the work PC.

## Executed checks

| Check/case IDs | Command or procedure | Revision/environment | Result/counts/duration | Evidence |
| --- | --- | --- | --- | --- |
| S-01 to S-13, E-03, E-04 | `python -m pytest` with the 18 files listed in the plan, `-q -p no:cacheprovider`, a base temporary folder outside the checkout, and `PLAYWRIGHT_BROWSERS_PATH` naming a scratch folder that links to the container's Chromium (the third browser the tests' `launch()` tries; nothing was installed) | `8ec5a66`, Linux, Chromium 153 | PASS: 223 passed, 0 failed, 3 skipped, 2 third-party deprecation warnings, 111.0 s. `tests/test_settings_sections_browser.py` is 16 of them (synthetic browser) and `tests/test_users_browser.py` 9 (synthetic browser) | Console output. Run with pytest directly from a second worktree, not through `tools/check.py`, so there is no `result.json` |
| E-04, tests that read `app.js` as text | `python -m pytest tests/test_dubai_time_policy.py tests/test_flow_local_file.py` with the same options | `8ec5a66`, Linux | PASS: 25 passed, 0 failed, 0 skipped, 2.3 s | Console output |
| N-01 to N-04, E-01, E-02, E-03 | `node` on every `tests/test_*.mjs` except `tests/test_gemini_extension.mjs` | `8ec5a66`, Node 22.23.4 | PASS: 29 files, among them the new `tests/test_settings_sections.mjs` | Console output |
| Script syntax | `node --check` on the 9 scripts in `app/static/`, on `recording-preview/startup.js` and on the 5 changed or new `.mjs` tests | `8ec5a66` | PASS: 15 files | Console output |
| Whitespace | `git diff --check f37800c HEAD` | `8ec5a66` | PASS: no output | Console output |
| Do the new tests notice a regression? | Twelve deliberate breakages, one at a time, each followed by both new test files and then undone: a moved page back in the menu; old addresses not understood; a moved page drawn but not wired; the rail without the group; an old address stacked in the history; a search discarding the open page; the read-only notice on every section; a save on Flows > Settings jumping to System > Settings; the page title left as a second `h1`; the section address not written; the registry not read again; Premium Viewers not redrawn once the user is known | The application files of `8ec5a66`, before it was committed. Ten breakages ran against the test files as they were one edit earlier, the two named in Findings against the committed ones | PASS: each breakage made at least one new test fail. On the first pass the browser tests missed two of them (see Findings) | Console output |
| Page walk, steps 1 to 8 | Playwright script against a temporary instance on `127.0.0.1:8772` with an empty database, as the host PC and with `x-forwarded-for: 203.0.113.9` | `8ec5a66`, Chromium 153, 1360 px wide, scale factor 2 | PASS, details below | Console output; 17 screenshots kept outside the repository |
| Page walk, step 9 | Playwright script that opens each preview page loading `app.js` and collects page errors | `8ec5a66` | PASS: 8 pages, no page error | Console output |

The 3 skips are 2 that need PowerShell service configuration and 1 that
needs native Windows paths. The 2 warnings come from Starlette's test
client.

### What the page walk showed

1. The System menu lists Settings, Changelog, Event Log, FAQ, Scanner and
   Updates. The rail lists the seven registry sections, the label More
   settings, then AI, Paths, Flow workers and Premium Viewers. Plain
   `#settings` became `#settings/connections`. All eleven entries opened;
   the four moved ones each showed one page title (Settings), a section
   title equal to the rail name and the address `#settings/<id>`. No entry
   scrolled sideways. After a click, keyboard focus was on the entry that
   opened. Enter on the Paths entry opened it, and three Tabs later focus
   was in the root field.
2. Paths: Check impact answered "All configured paths fit this root." A
   relative folder was refused with "Choose an absolute filesystem path."
   under the buttons, the typed value stayed and Save was enabled; the
   corrected value saved with "Flow paths saved" and the page showed "Value
   from setting". Flow workers: visible capacity 1 to 2 saved with "Flows
   settings saved", and the registry row under Flows > Workers then read 2,
   "Set here". A portal limit saved with "Portal limit saved". Premium
   Viewers: two fictional emails saved with "Saved 2 premium viewers" and
   the header read "2 premium viewers". AI: the model line read "Could not
   load /models: Metronome could not reach the AI models endpoint." (no
   model provider runs here), a feature was switched off and saved with "AI
   settings saved for new work", and the status read "4 of 5 enabled".
3. A registry number was edited without saving: the bar read "1 unsaved
   change". With Paths open it read "1 unsaved change in other sections" and
   the rail counted "1 unsaved" on the section. A search for "worker" showed
   9 registry rows and offered Flow workers as a link; clearing it showed
   the Paths form with the text typed before. The registry edit was still
   there and was discarded with "Changes discarded."
4. The Folders pointer read "Flows root and enforced paths. Under Paths in
   More settings. Shows which Flows a change would affect." and opened
   Paths; Back returned to `#settings/folders`. The Updates pointer opened
   the Updates page, and its menu entry was the highlighted one.
5. Flows > Settings: visible capacity saved with "Flows settings saved" and
   the view stayed on `#flows` with the Settings tab selected.
6. `#ai`, `#paths`, `#flow-settings` and `#premiumviewers` each opened the
   matching section on a fresh load, when typed while on FAQ and through
   `navigate()`, with the address rewritten to `#settings/<id>`; Back
   returned to `#faq`. `/paths` opened Paths. `#settings/freshness` opened
   Freshness and alerts and did so again after a reload. `#settings/nowhere`
   opened Connections.
7. Another computer: the registry sections showed "Only the host PC can
   change settings…" and no usable control. AI, Paths and Flow workers
   showed "Only the host PC can save changes to <name>. You are looking
   from another computer. On the host PC, open Metronome as
   http://localhost:8000." with their controls usable, as on the former
   pages. Saving Paths answered "Only the host PC can change where Flows
   keep their files…" under the buttons and saving Flow workers "Only the
   host PC can change Flows settings…" beside them, each with the typed
   value kept; saving AI answered "AI settings were not saved: AI connection
   settings can be changed only on this Metronome computer." Premium
   Viewers showed no notice and saved with "Saved 3 premium viewers", which
   any computer could do before the move as well.
8. At 1000 px wide none of the four sections scrolled sideways. At 860 px
   the page was 2 px wider than the window on every page tried, changed or
   not (FAQ and Updates included): the theme button in the navigation bar
   passes the edge once the user chip is shown. Nothing in the page content
   passed the edge. This is existing behaviour and was left as it is.
9. Flow owners, Flow triggers, Folder names, Managed, Playback, Python
   scheduler, Python scripts and Templates refresh loaded `app.js` without
   `settings.js` and reported no page error.

Responses of 400 or above during the walk, all expected: `502 POST
/api/ai/settings/models` each time the AI section opened (no provider);
`400 PUT /api/system/paths` for the relative folder; as another computer
`403` for `GET /api/system/remote-flow-control`, `POST
/api/ai/settings/models`, `PUT /api/system/paths`, `PUT /api/system/flows`
and `PUT /api/ai/settings`. No page error, console error or failed request.

## Unperformed or blocked in-scope checks

| IDs | Status | Reason | Next action |
| --- | --- | --- | --- |
| E-04, browser tests of preview pages and the Flows page that load `app.js`: `tests/test_templates_refresh_preview.py`, `tests/test_managed_flow_editor.py`, `tests/test_optional_recording_editor.py`, `tests/test_recording_range_editor.py`, `tests/test_recording_startup_ui.py`, `tests/test_flow_excel_ui.py`, `tests/test_flow_list_browser.py` | NOT RUN | They start the browser with `channel='chrome'`, and the container has no Chrome. Step 9 of the walk opened the preview pages for page errors only; the Flows list fixture, which also loads `app.js` without `settings.js`, was not opened | The pull request's CI |
| `tests/test_gemini_extension.mjs` | NOT RUN | It imports the npm package `pg`, which is not installed here. Nothing it covers changed | The pull request's CI |
| Full regression on Ubuntu and the Windows verifier job | NOT RUN | Final-head CI runs after the pull request is opened; the branch has not been pushed | Record the run URL and head SHA in the PR's testing section |

## Usability evidence

Recorded from the walk above at `8ec5a66`, in Chromium at 1360 px. The
forms of the four pages and what the user decides on them did not change;
the journey to reach them did, at the owner's request, and was not paused
for a preview first.

| Changed control | Label and placement | Feedback, failure and recovery | Entered work and navigation |
| --- | --- | --- | --- |
| System menu | Six entries; the four that moved are gone | Updates still opens and highlights | Old addresses and in-app links land on the new place (step 6) |
| Rail group | A divider and the label More settings, then AI, Paths, Flow workers, Premium Viewers, in the fonts and colours the rail already used. In a window under 900 px the label takes its own row above the four entries | The open entry is marked and named as the section title. "Loading Paths…" is shown while a page's data loads. A page that cannot load shows "Could not load Paths", the server's sentence and Try again in its own place, and the rest of Settings keeps working (S-12) | Focus stays on the entry that opened. The address follows the open section; Back steps through sections and then leaves Settings |
| Section title | The page's own title, shown as a section title equal to the rail name; Flow workers reads "Flow workers" here and still "Flows settings" inside Flows | Not applicable | One page title per page |
| Pointer under "Set somewhere else" | Says where it leads: "Under Paths in More settings." | Opens the section in place | Unsaved registry edits are kept; Back returns to the section the pointer was in |
| Search result | A More settings group after the matching registry rows, with one line on what the page holds | Following it opens the section and clears the search | A form hidden by the search keeps what was typed in it (step 3) |
| Notice for another computer | One sentence above the layout that fits the open section; none on Premium Viewers | A refused save still shows the server's sentence where each page shows it, and nothing typed is lost (step 7) | Unchanged |
| Save bar | "1 unsaved change in other sections" while a moved page is open, so it is not read as that page's own Save | Unchanged | The rail counts the unsaved changes on their section |
| Each page's own Save | Unchanged labels: Save paths, Save settings, Save portal settings, Save Premium Viewers, Save AI settings | The same messages as before, each seen in step 2; refusals in S-05, S-06, S-08, S-09 and step 7 | After a save the section stays open and shows the saved values |

## Findings, limitations and retests

### Found and fixed while building this

- A registry row went stale. The Flow workers form and the Workers group
  under Flows hold the same five stored values. Because a section now opens
  without reloading the page, the registry row still showed the value from
  before a save made on Flow workers; a screenshot of the first walk showed
  "Visible browser windows 1, Default" after 2 had been saved. Settings now
  reads the registry again after one of the four pages was open. Retested:
  S-06, N-04, and step 2 of the final walk read 2, "Set here".
- The read-only notice and the sentence "Every change is recorded in the
  Event Log" would have been untrue above Premium Viewers, which any
  computer can save and whose saves are not logged. Both now depend on the
  open section (S-10, N-04).
- `navigate()` set the hash even when it was already right. No `hashchange`
  follows an unchanged hash, so the skip flag stayed set and the next Back
  or hand-typed address was ignored once. That would have broken Back
  between sections; the hash is now written only when it differs (N-02).
- The browser tests first missed two of the twelve breakages: an old
  address stacked in the history, because a browser does not add an entry
  for a hash set while the page is still loading, and the Premium Viewers
  redraw. The old-address test now presses Back after typing the old
  address, and `test_premium_viewers_is_redrawn_once_the_user_is_known` was
  added. Both breakages were rerun and are caught.

### Decisions and limits

- AI moved; Scanner and Updates did not. AI is a form of mode, connection,
  features and limits. Scanner is module cards, run buttons and two
  histories around two small settings. Updates is the installed version,
  Run update and the last attempt around two switches.
- The five values on Flow workers can now be edited in two sections of the
  same page. The form was left whole because this change moves pages
  without rewriting them and because Flows > Settings shows the same form.
- Text typed into one of the four forms and not saved is lost when another
  rail entry is opened, as it was when leaving the page before. Only a
  search keeps it.
- Whether Settings shows another computer a notice is declared per page in
  `settingsSubpages` (`hostOnly`). The server checks are untouched and
  decide; the declaration would have to follow if one of them changed.
- Flows > Settings shows two page titles, "Flows" and "Flows settings".
  That is how it was before and it was left alone.
- Not updated: `docs/metronome-feature-inventory.md`, a dated baseline of
  the navigation as of 2026-09-08, and two README mentions of "System >
  Refresh Schedule", a page that was already folded into Scanner.

## Merge evidence

Pending. The branch `wip/settings-move` has not been pushed and no pull
request exists yet. Before merging, record the final `Merge ready` run URL,
the tested head SHA and the result in the PR's testing section.
