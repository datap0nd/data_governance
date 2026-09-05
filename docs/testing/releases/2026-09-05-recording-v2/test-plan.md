# Recording v2 and date-batch retirement: test plan

Prerequisites: deploy the app and workers from the same reviewed main revision.
Drain active work with the existing updater before replacing worker code. Back up
the database and retain immutable scripts. Use synthetic reports first; keep live
portal data and traces in protected storage.

1. V2-01: On a disposable upgrade database, retain an old batched flow with queued,
   running and completed jobs. Upgrade. Expect its schedule paused, queued jobs
   cancelled with a batching-removal reason, and running/final job JSON and history
   unchanged. Restart again: no repeated conversion or duplicate draft.
2. V2-02: Open the old recording. Enter explicit start/end dates in Convert to one
   range. Reversed or malformed dates fail. Valid dates create a separate ordinary
   draft; original revision stays unchanged. Test it before activation. Do not
   submit a large range merely to reproduce the old whole batch.
3. V2-03: Submit a definition with date_batch, including an empty/null value.
   Saving and execution reject it. SQL retry on historical batched jobs is blocked.
4. V2-04: Run a v2 recording with a one-second wait and two downloads, plus Python
   transformation, in worker and portable modes. Compare data, output count and
   transformed rows. Test a 600-second wait and cancel: heartbeat continues and
   following actions do not run. Durations 0, 601, fractions and booleans fail.
5. V2-05: Move a popup-page action before popup creation or after page closure.
   Server rejects the dependency. Check title suggestions retain their iframe;
   ambiguous titles and generic Ready/Download text do not auto-select identity.
6. V2-06: Advertise only v1 capabilities on a worker. It must not claim v2
   recording, validation or execution jobs. Verify a current worker can claim.

Automated: run the complete Python suite, tests/test_*.mjs, JavaScript syntax
checks, and Windows/Linux CI. Focused modules: test_recording_date_batches,
test_recording_v2_model, test_recording_v2_pipeline, test_flow_recordings and
test_recording_controls. Full suite covers file, Outlook, bookmarks, transformation,
SQL reconciliation and leases. Record SHA, environment, counts and CI URL.

Live work-PC pilot: repeat V2-02/V2-04 with representative GSCM/ASAP downloads;
compare identity, periods, schema and contents. Mark NOT RUN until executed.
Record sanitized session/output IDs and actual failures, then clean up only test
flows/profiles/artifacts after collecting evidence. No recurring monitor is enabled.
