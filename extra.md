Hi team,

Flagging a data discrepancy I've been investigating in the X PBI report (X report).

## The problem

For [month], the report shows ~X in the X measure. The Excel report using the same X source shows ~X for the same period. All other months match correctly between the two.

## Investigation steps

1. Started with the DAX measure in PBI. X filters the X table for:
   - Q/A = X
   - Category = X
   - Pre_Cur_Plan = X
   - Plan rank = latest revision (max)
   - The measure also calculates a plan_revised variable but never uses it as a filter - however, testing confirmed this isn't causing the discrepancy as filtered and unfiltered results match.
2. Checked for plan revision overlap. There are 3 plan ranks in the data (X, X, X). Verified the measure correctly resolves to the latest. No double-counting across plan revisions.
3. Traced the data source. The PBI data model connects to the X materialized view in Postgres (X schema). This view unions data from 5 tables:
   - X
   - X
   - X
   - X
   - X
4. Identified which tables feed the affected measure. The measure's filter path maps to X data specifically. The other tables have the relevant sources excluded, so the inflation is coming from X and/or X.
5. Tested for row duplication. Created a copy of the materialized view replacing UNION ALL with UNION between the two suspect tables. No change in the output - ruled out duplicate rows between the tables.
6. Flagged a potential double multiplication in the view logic. The source CTE multiplies the value by 1000. Then in a later CTE, any row with a specific Source Name pattern gets multiplied by 1000 again. The affected Source Name matches this pattern, triggering both multiplications. However, if this were the cause it would affect all months, not just one - so this may be intentional or a separate issue worth reviewing.
7. Attempted to trace further. The two suspect tables are regular tables populated by external scripts. These scripts are not among the ones I have access to. Without seeing how these tables are populated, I can't determine what's introducing the extra data for [month].

## Conclusion

The discrepancy originates in the source data within the two suspect tables, not in the DAX measure or the materialized view logic. The script that populates these tables is likely loading incorrect or additional data for [month] specifically.

## What's needed to resolve

Access to the script(s) that populate these tables, or guidance from someone familiar with this pipeline.

**Note on the double multiplication:** Even if unrelated to this specific discrepancy, the view applies a multiplication twice to certain values (once in the source CTE, once in the final transformation). This should be reviewed to confirm it's intentional.

Best regards
