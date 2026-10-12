# Several schedule triggers per Flow: test plan

- Change/PR: one Flow can run at several schedule times. The existing schedule
  stays trigger 1; **Schedule and owner > More triggers** adds up to 23 more,
  each daily, weekly or monthly at its own Dubai time. The scheduler queues at
  the earliest due trigger, the Flows list shows `+ N more triggers`, and
  choosing **Manual** removes them. Typical use: check Outlook for a file
  several times a day when the sender has no fixed delivery time; Outlook and
  file Flows already finish as a no-op when nothing new has arrived.
- Code baseline: branch `claude/dreamy-dijkstra-8btkqe` from `main` at
  `97f0abf`.
- Related report: [test-report.md](test-report.md)
- Intended environments: Linux container with the checkout-owned Python 3.13
  `.venv`, Node.js and the preinstalled Playwright Chromium; required GitHub
  Actions CI on the final head. All browser checks use the fictional in-memory
  preview `app/static/recording-preview/flow-triggers.html`.

## Prerequisites and test data

Automated tests create a disposable SQLite database per test through the
existing `flow_db` fixture and a fictional website report. The browser preview
replaces only the API layer with fictional Flows ("Customer feedback files",
"Stock check scripts"); no worker, Outlook, Python, portal or SQL access
happens. The clock is pinned to Monday 12 October 2026 09:30 Dubai
(05:30 UTC) where next-run times are asserted.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | `additional_trigger_rules` with an unsupported type, a weekly trigger without weekdays, an invalid time and 24 extra triggers | Each error names the trigger number (from 2) and the reason; 23 extra triggers are accepted; fields a schedule does not use are dropped | `tests/test_flow_triggers.py::test_trigger_rules_validate_and_number_from_two` |
| T-02 | Earliest next occurrence across daily and weekly triggers at several instants | The earliest future occurrence of any trigger; none for an empty list | `test_next_run_is_the_earliest_trigger` |
| T-03 | Flow health with triggers at 06:00 and 18:00 UTC and success evidence at 07:00 | Overdue once the 18:00 occurrence passes its 24-hour grace; healthy with a later success; pending before the baseline | `test_flow_health_checks_the_latest_due_trigger` |
| T-04 | Create a daily 08:00 Flow with daily 11:00, Monday 10:00 and monthly day-31 triggers at Monday 09:30 Dubai | Normalized triggers are saved and returned; `next_run_at` is today 10:00 Dubai (06:00 UTC); the freshness rule is still trigger 1 | `test_create_saves_triggers_and_queues_the_earliest` |
| T-05 | Build Flows whose trigger repeats trigger 1, repeats another extra trigger, lacks weekdays, or is added to a Manual Flow | 422-style validation located at `additional_triggers` with "Trigger N repeats trigger M." or the named reason; weekday order does not hide a repeat | `test_invalid_triggers_name_the_trigger` |
| T-06 | Update without the field (older client), move trigger 1 onto a saved trigger, send an explicit list, then switch to Manual | Saved triggers stay; the redundant copy is dropped; the explicit list replaces them; Manual stores `[]` | `test_update_keeps_omitted_triggers_drops_redundant_and_manual_clears` |
| T-07 | Save the same triggers, then add one | The Flow health baseline is unchanged for the same set and restarts when the set changes | `test_changing_triggers_restarts_flow_health_baseline` |
| T-08 | Activate a paused Flow with 08:00, 11:00 and 15:00 triggers; run the scheduler at 11:00 and again at 15:00 while the first run is still queued | Activation schedules 11:00; one scheduled run is queued and the next run moves to 15:00; the due 15:00 trigger is skipped while a run is active and the next run moves to tomorrow 08:00 | `test_activation_and_scheduler_use_every_trigger` |
| T-09 | Link the multi-trigger Flow's SQL target to an inheriting Source and reconcile | The Source inherits daily 08:00 only (trigger 1) | `test_source_freshness_keeps_following_trigger_one` |
| T-10 | Handover snapshot of a Flow with triggers | `schedule.additional_triggers` lists them | `test_handover_snapshot_carries_the_triggers` |
| T-11 | Node contract for trigger rows, the More triggers block, the Add default, the payload reader, the error focus and the list label | Rows numbered from 2 show only the fields their schedule needs; the Outlook/file help adds the no-op sentence; Add copies the previous trigger one hour later (wrapping midnight); the reader sends only used fields; list labels unchanged without triggers and `Daily + 2 more triggers` with them | `node tests/test_flow_triggers.mjs` |
| T-12 | Affected regressions: Flow create/update/schedule/scheduler, freshness inheritance, handover, AI investigation evidence, Dubai time policy, existing builder and list contracts | All existing cases still pass | Suites listed under Automated checks |
| U-01 | Preview **Edit Outlook flow** > Schedule and owner: Add trigger, switch the new row to Monthly, set day 15, remove trigger 3 | New Trigger 4 is weekly 15:00 Mon–Fri with focus on its time; Monthly swaps weekdays for Day of month; removal renumbers to Trigger 3 keeping Monthly/15 and moves focus to its Remove button | `tests/test_flow_triggers_preview.py::test_triggers_add_remove_save_fail_and_recover` |
| U-02 | Choose the fictional "trigger repeats another" save outcome and save; then choose "Flow saved" and save | "Flow not saved: … Trigger 3 repeats trigger 2." beside the actions, focus on Trigger 3's time, every value kept; the retry saves the exact `additional_triggers` payload | same test |
| U-03 | Set the schedule to Manual and save; open **New Outlook flow**, choose Daily 07:30 and Add trigger; open **Edit Python flow** | Manual hides More triggers and sends `[]`; a new Flow starts with no rows and adds daily 08:30; the Python help omits the no-op sentence | `test_manual_hides_triggers_and_new_flow_starts_empty` |
| U-04 | Add triggers until the limit, remove one; open **Flows list** | At 24 triggers Add is disabled and "A Flow can have at most 24 triggers." shows; removing one re-enables Add; the list shows `+ 2 more triggers` | `test_trigger_limit_and_list_label` |
| U-05 | 390×844 viewport, add a Monthly trigger | Rows stack without horizontal page overflow; Remove stays visible | `test_narrow_screen_keeps_trigger_rows_inside_the_page` |

## Automated checks

```bash
python tools/check.py verify \
  --test tests/test_flow_triggers.py \
  --test tests/test_flow_triggers_preview.py \
  --test tests/test_flows.py \
  --test tests/test_freshness_inheritance.py \
  --test tests/test_flow_handover.py \
  --test tests/test_ai_operations.py \
  --test tests/test_dubai_time_policy.py \
  --syntax app/freshness.py --syntax app/routers/flows.py \
  --syntax app/database.py --syntax app/flow_handover.py \
  --syntax app/ai/operations_tools.py --syntax app/static/app.js \
  --syntax app/static/recording-preview/flow-triggers.js
node tests/test_flow_triggers.mjs
for f in tests/test_*.mjs; do node "$f"; done
```

Required final-head CI (`Merge ready`) is the full regression.

## Usability evidence

The schedule step keeps its existing controls and adds an optional **More
triggers** block; nothing the user must decide changes, no step is added,
removed or reordered. The fictional preview is walked by U-01 to U-05 at
1280×900 and 390×844, with screenshots saved when `PREVIEW_EVIDENCE_DIR` is
set.

## Acceptance and cleanup

Accepted when every case above passes locally and the final-head `Merge ready`
check passes. The new `flows.additional_triggers_json` column defaults to
`'[]'`, so existing Flows behave exactly as before. Rollback: revert the PR;
the extra column is ignored by older code, which keeps running trigger 1 only.
No test schedules, data or settings need cleanup.
