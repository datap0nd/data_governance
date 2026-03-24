SELECT
    replace("PLANID"::text, '_0', '') AS "Plan",
    left(replace(week_column::text, 'W', ''), 6)::integer AS "Week",
    'Sell-In (Biz Plan)' AS "Category",
    'USD' AS "Q/A",
    'Cur Plan' AS "Pre_Cur_Plan",
    "AP2ID" AS "Subsidiary",
    "AP1ID" AS "Customer",
    "ACCOUNTID" AS "OrgSales",
    "ITEM" AS "Item",
    'B2C' AS "B2B Sell In Biz Plan",
    sum(week_value::double precision * 1000 * 1000) AS "Value($/Unit)"
FROM bi_reporting.it_voc_amt_cur_year
WHERE (("AP2ID"::text || "AP2ID"::text || "AP1ID"::text || "ACCOUNTID"::text || "ITEM"::text || "CATEGORY"::text || week_column::text) NOT LIKE '%Total%')
    AND week_value <> 0
    AND replace(week_column::text, 'W', '') NOT LIKE '%W-1%'
GROUP BY "PLANID", week_column, "AP2ID", "AP1ID", "ACCOUNTID", "ITEM", "CATEGORY"
ORDER BY "Week";
