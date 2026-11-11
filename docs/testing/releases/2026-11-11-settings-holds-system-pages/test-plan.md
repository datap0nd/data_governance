# 2026-11-11: Settings holds the System configuration pages: test plan

- Change/PR: link in the PR's testing section. AI, Paths, Flow workers and
  Premium Viewers stop being entries of the System menu and become sections
  of **System > Settings**, in a **More settings** group at the end of its
  left rail. Each keeps its own form, Save button, API and access rule.
  Scanner and Updates stay in the System menu because they mainly run and
  watch work. Old addresses (`#ai`, `#paths`, `#flow-settings`,
  `#premiumviewers`) open the matching section, and every section has the
  address `#settings/<section>`. Messages and documents that named the old
  locations name the new ones.
- Code baseline: the commit named in the report
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux, the checkout-owned Python 3.13 `.venv`, Node
  22, Chromium. Browser tests use fictional HTTP responses. The page walk uses
  a temporary local instance with an empty database. No live environment.

## Prerequisites and test data

The browser tests serve `app/` from a local static server and answer every
`/api/` request from a fixture: two fictional people, a three-section
registry, one fictional portal, `@example.test` emails and `D:\Metronome`
paths. Nothing reaches an application, database or portal.

The page walk starts `uvicorn app.main:app` from the tested checkout on a
free loopback port with `DG_DB_PATH`, `DG_FLOWS_ROOT` and `DG_ENV_FILE`
pointing into a new temporary folder and `DG_AI_MOCK=true`. "Another
computer" is the same browser sending `x-forwarded-for: 203.0.113.9`, which
the server does not treat as the host PC.

## Test cases

### The page in a browser (`tests/test_settings_sections_browser.py`, synthetic)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| S-01 | Open `#settings`; read the System menu; choose Updates | The menu lists Settings, Changelog, Event Log, FAQ, Scanner, Updates and nothing else. Updates opens its page, is highlighted and still has the id `btn-update-app` | `test_system_menu_no_longer_offers_the_moved_pages` |
| S-02 | Open `#settings`; choose each entry under More settings; then a registry section | The rail lists the registry sections, the label More settings, then AI, Paths, Flow workers, Premium Viewers. Each opens with one page title (Settings), a section title equal to its rail name, its own form, the address `#settings/<id>`, the Settings menu entry highlighted and no sideways scroll at 1360 px. No moved page is fetched before it is opened | `test_settings_rail_offers_the_moved_pages_as_sections` |
| S-03 | For each of `#ai`, `#paths`, `#flow-settings`, `#premiumviewers`: load the page at that address; then, from FAQ, set the address by hand and press Back; then call `navigate('<id>')` and press Back | Each opens Settings at that section and the address becomes `#settings/<id>`. The typed old address is rewritten in place, so one Back returns to FAQ both times | `test_old_address_opens_settings_at_that_section` (4 cases) |
| S-04 | Open `#settings/flows`; follow its pointer to Flow workers; Back; follow the Updates pointer; return through the menu; open `#settings/nowhere` | The registry section opens from its address. The pointer opens the Flow workers section in place and Back returns to Flows. The Updates pointer opens the Updates page. Settings reopens where it was left. An unknown section opens the first one | `test_registry_sections_keep_their_place_and_have_addresses` |
| S-05 | Paths: change the root, tick Enforce, Check impact; Save while the server answers 409; Save again | Check impact lists the affected Flow. The refusal is shown under the buttons, the typed root and tick remain and Save is enabled again. The retry sends `PUT /api/system/paths` with the typed values, says "Flow paths saved" and stays on Paths with the saved value | `test_paths_saves_through_its_api_and_recovers_from_a_refusal` |
| S-06 | Flow workers: change visible capacity and recording wait, Save; save a portal limit; Start visible workers; open Flows in the rail; Save while the server answers 403 | `PUT /api/system/flows` carries all five values; "Flows settings saved"; the section stays open with the saved values. `PUT /api/system/flows/portals/7` carries the limit; "Portal limit saved". The start result is shown beside the buttons. The registry row for the same value shows what was just saved. The refusal is shown beside the buttons and the choice remains | `test_flow_workers_saves_through_its_api` |
| S-07 | Flows page, Settings tab: change background capacity, Save | The save reaches `PUT /api/system/flows`, and the view stays on Flows > Settings at `#flows`; it does not jump to System > Settings | `test_flows_page_keeps_its_own_settings_view` |
| S-08 | Premium Viewers: type two emails; Save while the server answers 503; Save again | The failure is announced, the text remains and Save is enabled. The retry sends `PUT /api/usage/premium-viewers` with both emails, says "Saved 2 premium viewers" and the count in the header updates | `test_premium_viewers_saves_through_its_api` |
| S-09 | AI: open the section; tick a feature, choose Disabled; Save while the server answers 403; Save again | Opening loads the provider's model list. The refusal is announced and the choices remain. The retry sends `PUT /api/ai/settings` with the mode, the feature and the unchanged endpoint and model, says "AI settings saved for new work" and the badge reads Disabled | `test_ai_saves_through_its_api` |
| S-10 | As another computer: open a registry section, Paths (save refused with 403), AI, Flow workers, Premium Viewers (save) | Registry: the read-only notice, controls disabled. Paths, AI and Flow workers: a notice that only the host PC can save, controls usable as on the former pages, the server's refusal shown where the page shows it and the typed value kept. Premium Viewers: no notice and the save goes through, as before the move | `test_another_computer_is_told_what_holds_for_each_section` |
| S-11 | Edit a registry number; open Paths and type a root; search "worker"; clear the search; follow the search result; return to the first section | The save bar reads "1 unsaved change in other sections" on Paths and the rail counts it. The search hides the Paths form and offers Flow workers as a link. Clearing it shows the form with the typed root. The result opens Flow workers and clears the search. The registry edit is still there and nothing was saved | `test_search_finds_a_moved_page_and_unsaved_work_survives` |
| S-12 | Open Paths while `GET /api/system/paths` answers 500; Try again; open `#flow-settings` while its API answers 500 | "Could not load Paths" with the server's sentence and a Try again button in the section's place; the rail still works; the retry loads the form. The same on a fresh load, with the registry sections still usable | `test_a_moved_page_that_cannot_load_leaves_settings_usable` |
| S-13 | Premium Viewers opened by a host PC that has not chosen a user; choose Continue as host PC | The section is fetched and drawn again once the user is known, as the page behind the old menu entry was, and stays open | `test_premium_viewers_is_redrawn_once_the_user_is_known` |

### Router and page logic without a browser (`tests/test_settings_sections.mjs`)

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| N-01 | Run the router of `app.js` with recorded renderers: page table, the list of moved pages, `_settingsSectionOf` | The four ids are no longer pages of their own; Settings, Updates, Scanner, Changelog, Event Log and FAQ are. Old and new addresses resolve to the same section; other names resolve to none | Console output |
| N-02 | `navigate('<old id>')` from another page; an old address at start-up; `/paths`; `#settings/flows`; plain `#settings`; `navigate('paths')` while Settings is on screen; `navigate('updates')` | Settings is drawn at that section and the address is `#settings/<id>`, pushed for a real move and replaced for an old address. Plain `#settings` passes no section. With Settings on screen only the section changes. Updates is still drawn and wired as its own page | Console output |
| N-03 | Run `settings.js` with a fixture registry: rail, pointers, opening at a moved page, an unknown section, changing section in place twice in quick succession, returning to a registry section | Rail order and label as in S-02; with `settings.js` alone the rail holds the registry only. A moved page is drawn by its own renderer and wired once by its own binder; only the last of two quick choices is wired. Leaving it clears its markup | Console output |
| N-04 | A moved page changes a registry value while open; a moved page whose renderer fails; search; another computer; an unsaved registry edit | The registry is read again on leaving and the unsaved edit survives. The failure is shown in the section's place and nothing is wired. The search keeps the hidden page's markup. Notices as in S-10. The save bar wording as in S-11 | Console output |

### Existing tests changed to the new location

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| E-01 | `tests/test_ai_settings_display.mjs`: it required an AI entry in the System menu and an `ai` page in the router | It now requires that the menu has no AI entry and that the list of pages inside Settings names the AI renderer and binder. Its other assertions are unchanged | Console output |
| E-02 | `tests/test_paths_display.mjs`, `tests/test_settings_page_loading.mjs` | The first finds the Paths code under its renamed source marker. The second also builds the list of pages inside Settings without `settings.js`, as the preview pages do | Console output |
| E-03 | `tests/test_ai_settings.py`, `tests/test_pipeline_insights.py`, `tests/test_ai_operations_display.mjs` pinned the words "System > AI" | They pin "System > Settings > AI". No other assertion changed | Counts in the report |
| E-04 | Tests of the modules whose messages changed, of the production page and of the preview pages that load `app.js` | Pass unchanged | Counts in the report |

## Page walk

On the temporary instance, at 1360 px wide with a device scale factor of 2,
as the host PC and as another computer:

1. Open the System menu and every rail entry; record titles, addresses,
   keyboard focus and sideways scroll.
2. Paths: Check impact, a refused value, a save. Flow workers: a save, a
   portal limit, then the registry row for the same value. Premium Viewers
   and AI: a save each. Record the feedback each gives.
3. An unsaved registry edit, a visit to Paths, a search, and back.
4. Pointers from registry sections; Back; the Updates page and its menu entry.
5. Flows > Settings: a save in place.
6. Each old address on a fresh load, typed by hand and through `navigate()`;
   `/paths`; `#settings/<section>` with a reload; an unknown section.
7. As another computer: the notice on each section, usable controls, the
   refusal of each host-only save, and a Premium Viewers save.
8. The four moved sections at 1000 px and 860 px wide.
9. Every preview page under `app/static/recording-preview/` that loads
   `app.js`, for page errors.

Collect page errors, console errors, failed requests and every response of
400 or above, and keep screenshots outside the repository.

## Automated checks

```bash
python -m pytest tests/test_settings_sections_browser.py tests/test_users_browser.py \
  tests/test_ai_settings.py tests/test_ai_operations.py tests/test_ai_key_protection.py \
  tests/test_pipeline_insights.py tests/test_email_alert_summary.py tests/test_settings_registry.py \
  tests/test_audit_access.py tests/test_recording_startup.py tests/test_recording_diagnostics.py \
  tests/test_recording_playback_ui.py tests/test_flow_triggers_preview.py tests/test_flow_owners_preview.py \
  tests/test_python_scripts_preview.py tests/test_recording_slider_editor.py tests/test_flow_capacity.py \
  tests/test_flow_paths.py -q
python -m pytest tests/test_dubai_time_policy.py tests/test_flow_local_file.py -q
for f in tests/test_*.mjs; do node "$f"; done
node --check app/static/app.js app/static/settings.js
git diff --check
```

The browser tests start the first of Chrome, Playwright's Chromium or a
Chromium build under `PLAYWRIGHT_BROWSERS_PATH`, and skip when none starts.
Final-head CI is the full regression, including the browser tests that ask
for the Chrome channel by name.

## Usability evidence

The journey to these four pages changes at the owner's request: System >
Settings, then an entry in the rail, instead of a System menu entry. What
the user decides on each page, its controls and its feedback are unchanged,
so the walk above covers the changed controls: the rail group, the pointers
between sections, the search result, the notices, the save bar wording and
the addresses. The report records for each of them the label, the feedback
beside the action, what a failure shows and whether entered work survives.

## Acceptance and cleanup

Accepted when every case above passes, the walk reports no page error, no
failed request and no unexpected response, and final-head CI passes. Stop
the temporary instance and delete its folder afterwards. Rollback is a
revert of the change: the old addresses work before and after it, so no
bookmark or link has to be changed either way.
