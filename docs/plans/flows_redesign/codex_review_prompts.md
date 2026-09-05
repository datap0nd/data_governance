# Future review checklist

The plans were reviewed together against main c527be4b. See 00_README.md and
delivery_log.md. The owner's current request authorizes sequential implementation
and merges; the original analysis-only prompts are superseded.

- Read current code, shared decisions and prior merge validation.
- Check paths against ownership and recovery references.
- Preserve Local snapshots/receipts, direct publication and SQL/pipeline locks.
- Keep API and UI compatibility, including legacy clients.
- Test concurrent claims and stale reports with independent connections.
- Never weaken existing tests to fit a proposal.
- Distinguish code rollback from undoing files or committed SQL.

