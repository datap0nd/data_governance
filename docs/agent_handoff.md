# Agent Handoff

## Current Objective

Make Power BI recurrence delivery fail closed unless the semantic model's latest
refresh succeeded, default each alert owner from its report, and notify that
owner by email when an actual send run fails before delivery is launched.

## Repo State

- Path: `/Users/rafaelcunha/Documents/data_governance`
- Branch: `main`
- Base commit: `74bed17 Add Power BI visual query fallback`
- Public repo: no, private
- Push status: implementation is ready for its direct `origin/main` commit and push

## Decisions Made

- Refresh verification is a mandatory delivery gate, not an optional row rule.
- Every draft and send run queries the live Power BI refresh-history endpoint
  through the existing cached delegated account. Only status `Completed` passes.
- Failed, cancelled, running, missing, or unavailable refresh status blocks
  visual export and sends no alert data.
- A recurrence stores the alert owner's name. New alerts default from the local
  report owner, while the current owner email is resolved dynamically from the
  central People registry.
- Saving requires a People entry with a valid email. Existing recurrences without
  an owner derive and persist the current report owner on their next run.
- Failed send runs notify the owner with the failure reason, refresh result when
  available, report/page/visual context, and the Power BI report link.
- Draft-test failures do not send owner notifications.
- Failure notifications use the existing Outlook task delivery. If Outlook
  itself is unavailable, the attempt is recorded in run detail but email cannot
  be delivered through that same unavailable channel.

## Files Changed

- `app/database.py`: add recurrence ownership and backfill existing alerts by
  report name.
- `app/scanner/pbi_fetch.py`: fetch the latest live semantic-model refresh and
  extract useful Power BI failure details.
- `app/routers/recurrences.py`: enforce the refresh gate, resolve owners and
  emails, send owner failure notices, persist run details, and enrich report
  picker data.
- `app/static/app.js`: show alert owners and the refresh gate, default owners
  from reports, and require a mapped owner email before saving.
- `tests/test_recurrences.py`: cover owner defaults, picker data, refresh
  blocking, owner notifications, and draft behavior.
- `tests/test_pbi_fetch.py`: cover cached authentication, endpoint construction,
  and refresh-error extraction.
- `README.md`: document the refresh gate, ownership, and notification behavior.

## Commands And Checks

- `uv run --python /opt/homebrew/bin/python3.11 --with-requirements requirements.txt --with pytest python -m pytest -q`: 38 passed.
- `node --check app/static/app.js`: passed.
- `python3 -m py_compile app/database.py app/routers/recurrences.py app/scanner/pbi_fetch.py tests/test_recurrences.py tests/test_pbi_fetch.py`: passed with the host Python 3.9 parser.
- `git diff --check`: passed.
- Python 3.14 validation was not run because Python 3.14 is not installed on this Mac.
- Live Windows Power BI and Outlook validation was not run because this Mac does
  not have the work PC token cache, workspace access, Edge runtime, or Outlook profile.

## Open Questions

- The work PC must confirm that the cached account can read refresh history for
  the selected semantic model.
- The current Outlook launcher confirms that the interactive task was launched,
  not final Exchange delivery. Pre-delivery and launcher failures are detected;
  a complete Outlook outage cannot email its own failure notice.

## Next Step

Update and restart Metronome on the work PC, ensure each report owner has an
email in Management > Create > People, then use Create drafts once and test one
send against both a completed and a deliberately failed latest refresh.
