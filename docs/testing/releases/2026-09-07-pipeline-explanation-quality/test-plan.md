# Pipeline explanation quality gate: test plan

- Change/PR: Pipeline connection explanations must be useful. The local-AI
  prompt now receives a mechanical digest of the SQL or Power Query definition
  (relations, joins with their columns, WHERE predicates, GROUP BY columns,
  aggregates, M steps) plus an explicit requirements checklist. Every answer is
  checked against that digest; an answer that skips an evidenced join, filter,
  grouping, or column, or that merely restates "A feeds B", is sent back once
  with the exact gaps and otherwise replaced by a definition-derived fallback
  that names the reason. Views whose SQL the catalog scan has not stored are
  read once through the read-only route during the run. The Pipelines tooltip
  now shows provenance (AI with confidence, or definition-derived with reason),
  and connections without a cached explanation say how to generate one.
  Files: `app/pipeline_insights_quality.py` (new), `app/pipeline_insights.py`,
  `app/pipeline_insights_db.py`, `app/scanner/pipeline_insights.py`,
  `app/scanner/modules.py`, `app/routers/lineage.py`, `app/static/app.js`,
  `app/static/style.css`, `README.md`, tests.
- Code baseline: main after PR #84 (Pipelines edge routing).
- Related report: [test-report.md](test-report.md)
- Intended environments: Metronome with Local AI mode (Qwen through vLLM) and
  the Pipeline explanations feature enabled, PostgreSQL catalog access; the
  automated checks run anywhere with Python 3.11+ and Node 22.

## Prerequisites and test data

- The prompt version changed to `pipeline-edge-v3-grounded`, so every existing
  explanation is regenerated on the next Pipeline explanations run. Expect one
  full run (up to ceil(E/4) + E model calls for E eligible connections).
- The sidecar `pipeline_insights.db` gains a `relation_definitions` table
  through `CREATE TABLE IF NOT EXISTS`; no migration step and no schema-version
  bump, so an older app version can still open the file.
- Live checks need at least one materialized view built with a JOIN and a
  WHERE clause that a report uses, and one Power BI table whose M expression
  filters or removes columns.

## Test cases

| ID | Prerequisites and exact actions | Expected result | Evidence |
| --- | --- | --- | --- |
| T-01 | Under **Scanner**, run **Pipeline explanations** with Local AI enabled. | The run finishes with a message of the form "Processed N due Pipeline explanations; M used Qwen and passed the specificity check; K rejected as too generic even after one retry; …". Counts per reason appear in the run details as `error_breakdown`. | Scanner run summary |
| T-02 | Open **Pipelines**, select the report, hover the edge from a base table into a materialized view with a JOIN and WHERE. | The tooltip's technical paragraph names the join type, both joined relations, the join columns, and the WHERE predicate; the provenance line reads "Local AI explanation, high/medium confidence, generated …". No tooltip reads only "X feeds Y". | Screenshot |
| T-03 | Hover an edge whose AI answer was rejected (visible in the run breakdown) or switch the feature off under **System > AI** and reload. | The first paragraph is derived from the SQL itself (relations, joins, filters, grouping) and the second names the reason: "rejected because it did not describe…", "switched off under System > AI", "Local AI mode is disabled", etc. Provenance line reads "Derived from the stored definition; no validated AI explanation (reason)". | Screenshot |
| T-04 | Hover a Flow → source edge and an Upstream system → source edge. | Text states what the Flow loads, into which relation, when it last succeeded and its latest status; upstream text names the system, its refresh day and the source's last data. Provenance reads "Structural link only…". | Screenshot |
| T-05 | Create a view used by a report without running the PostgreSQL dependency scan again, then run Pipeline explanations. | The run message ends with "Read 1 missing view definition(s) live from PostgreSQL." and the edge is explained from that SQL. | Scanner run summary |
| T-06 | Hover a Power BI table edge whose M expression uses Table.SelectRows or Table.RemoveColumns. | The technical paragraph states the filter predicate and which columns are removed; the fallback (if AI text is missing) lists the same steps. | Screenshot |
| T-07 | Change a materialized view definition, reload Pipelines before rerunning explanations. | The tooltip shows the definition-derived text with reason "stale" and the provenance line says to rerun Pipeline explanations. | Screenshot |
| S-01 | Automated: run the commands below. | All pass; the quality tests prove a generic "A feeds B" answer is rejected with specific reasons, a specific answer covering every evidenced operation is accepted, a rejected answer is retried once with feedback, a persistently generic answer falls back with `generic_output`, missing definitions are fetched live once, and disabled features report their reason. | Console output |

T-01 to T-07 need a live Metronome with Local AI and PostgreSQL; S-01 runs locally.

## Automated checks

```bash
node --check app/static/app.js
node tests/test_pipeline_insights_display.mjs
node tests/test_lineage_display.mjs
node tests/test_lineage_edges.mjs
python -m pytest tests/test_pipeline_insights.py tests/test_lineage_depth.py -q
python -m pytest tests -q
```

## Acceptance and cleanup

Accepted when the automated checks pass and, on the live instance, T-02 shows
join columns and predicates in the tooltip for a joined materialized view.
Rollback: revert the PR; the sidecar's extra table is ignored by the previous
version, and the old prompt version regenerates explanations on its next run.
