import pytest

from app import pbi_visual_export, pbi_visual_query


def _query_spec(*, filters=None):
    return {
        "fields": [
            {
                "roleName": "Values",
                "roleDisplayName": "Values",
                "target": {"table": "Operations", "column": "Subsidiary"},
                "displayName": "Subsidiary",
                "formatString": "",
            },
            {
                "roleName": "Values",
                "roleDisplayName": "Values",
                "target": {"table": "Measures", "measure": "Active policies"},
                "displayName": "Active policies",
                "formatString": "#,0",
            },
            {
                "roleName": "Values",
                "roleDisplayName": "Values",
                "target": {
                    "table": "Operations",
                    "column": "Premium",
                    "aggregationFunction": "Sum",
                },
                "displayName": "Premium",
                "formatString": "#,0.00",
            },
        ],
        "filters": filters or [],
    }


def test_build_visual_query_reproduces_fields_formats_and_filters():
    built = pbi_visual_query.build_visual_query(
        _query_spec(
            filters=[
                {
                    "source": "slicer:status",
                    "filter": {
                        "$schema": "http://powerbi.com/product/schema#basic",
                        "target": {"table": "Operations", "column": "Status"},
                        "operator": "In",
                        "values": ["Open", "Quoted"],
                    },
                },
                {
                    "source": "visual",
                    "filter": {
                        "$schema": "http://powerbi.com/product/schema#advanced",
                        "target": {"table": "Measures", "measure": "Active policies"},
                        "logicalOperator": "And",
                        "conditions": [{"operator": "GreaterThan", "value": 10}],
                    },
                },
            ]
        ),
        500,
    )

    assert "SUMMARIZECOLUMNS(" in built["dax"]
    assert "'Operations'[Subsidiary]" in built["dax"]
    assert 'KEEPFILTERS(TREATAS({"Open", "Quoted"}, \'Operations\'[Status]))' in built["dax"]
    assert '"__value0", \'Measures\'[Active policies]' in built["dax"]
    assert '"__value1", SUM(\'Operations\'[Premium])' in built["dax"]
    assert "[__value0] > 10" in built["dax"]
    assert 'FORMAT([__value0], "#,0")' in built["dax"]
    assert 'FORMAT([__value1], "#,0.00")' in built["dax"]
    assert "TOPN(501" in built["dax"]
    assert [column["header"] for column in built["columns"]] == [
        "Subsidiary",
        "Active policies",
        "Premium",
    ]


def test_build_visual_query_applies_matrix_value_decimal_places():
    spec = _query_spec()
    spec["visualFormatting"] = {
        "valueDecimalPlaces": 0,
        "valueDecimalPlacesSource": "visual",
    }

    built = pbi_visual_query.build_visual_query(spec, 500)

    assert 'FORMAT([__value0], "#,0")' in built["dax"]
    assert 'FORMAT([__value1], "#,0")' in built["dax"]


def test_build_visual_query_ignores_default_matrix_decimal_value():
    spec = _query_spec()
    spec["visualFormatting"] = {
        "valueDecimalPlaces": 0,
        "valueDecimalPlacesSource": None,
    }

    built = pbi_visual_query.build_visual_query(spec, 500)

    assert 'FORMAT([__value0], "#,0")' in built["dax"]
    assert 'FORMAT([__value1], "#,0.00")' in built["dax"]


def test_build_visual_query_escapes_identifiers_and_filter_literals():
    spec = {
        "fields": [
            {
                "target": {"table": "Owner's table", "column": "A]B"},
                "displayName": "Value",
            }
        ],
        "filters": [
            {
                "source": "visual",
                "filter": {
                    "$schema": "http://powerbi.com/product/schema#basic",
                    "target": {"table": "Owner's table", "column": "A]B"},
                    "operator": "In",
                    "values": ['He said "yes"'],
                },
            }
        ],
    }

    dax = pbi_visual_query.build_visual_query(spec, 10)["dax"]

    assert "'Owner''s table'[A]]B]" in dax
    assert '"He said ""yes"""' in dax


def test_apply_visual_field_formats_normalizes_whole_number_columns():
    formatted = pbi_visual_query.apply_visual_field_formats(
        "Subsidiary,Active policies,Premium,Ratio\r\n"
        "North,1234.000000,20.50,0.125\r\n"
        "South,-2.6,30.00,n/a\r\n",
        _query_spec()
        | {
            "fields": [
                *_query_spec()["fields"],
                {
                    "target": {"table": "Measures", "measure": "Ratio"},
                    "displayName": "Ratio",
                    "formatString": "0.0%",
                },
            ]
        },
    )

    assert formatted == (
        "Subsidiary,Active policies,Premium,Ratio\r\n"
        'North,"1,234",20.50,0.125\r\n'
        "South,-3,30.00,n/a\r\n"
    )


def test_apply_visual_field_formats_supports_standard_and_ignores_scaling_formats():
    query_spec = {
        "fields": [
            {
                "target": {"table": "Measures", "measure": "Count"},
                "displayName": "Count",
                "formatString": "N0",
            },
            {
                "target": {"table": "Measures", "measure": "Millions"},
                "displayName": "Millions",
                "formatString": "0,,",
            },
        ]
    }

    formatted = pbi_visual_query.apply_visual_field_formats(
        "Count,Millions\n1000.0,2000000.0\n",
        query_spec,
    )

    assert formatted == 'Count,Millions\r\n"1,000",2000000.0\r\n'


def test_apply_visual_field_formats_honors_matrix_value_decimal_places():
    spec = _query_spec()
    spec["visualFormatting"] = {
        "valueDecimalPlaces": 0,
        "valueDecimalPlacesSource": "visual",
    }

    formatted = pbi_visual_query.apply_visual_field_formats(
        "Subsidiary,Active policies,Premium\r\nNorth,2.25,370.75\r\n",
        spec,
    )

    assert formatted == "Subsidiary,Active policies,Premium\r\nNorth,2,371\r\n"


def test_apply_visual_field_formats_keeps_field_format_when_visual_value_is_default():
    spec = _query_spec()
    spec["visualFormatting"] = {
        "valueDecimalPlaces": 0,
        "valueDecimalPlacesSource": None,
    }

    formatted = pbi_visual_query.apply_visual_field_formats(
        "Subsidiary,Active policies,Premium\r\nNorth,2.25,370.75\r\n",
        spec,
    )

    assert formatted == "Subsidiary,Active policies,Premium\r\nNorth,2,370.75\r\n"


@pytest.mark.parametrize(
    "target, message",
    [
        ({"name": "Running total", "daxExpression": "RUNNINGSUM([Value])"}, "visual calculation"),
        (
            {"table": "Calendar", "hierarchy": "Date", "hierarchyLevel": "Month"},
            "hierarchy field",
        ),
        (
            {"table": "Measures", "measure": "Share", "percentOfGrandTotal": True},
            "percent of grand total",
        ),
    ],
)
def test_build_visual_query_fails_closed_for_unsupported_visual_constructs(target, message):
    with pytest.raises(pbi_visual_query.PbiVisualQueryError, match=message):
        pbi_visual_query.build_visual_query(
            {"fields": [{"target": target, "displayName": "Value"}], "filters": []},
            100,
        )


def test_execute_visual_query_uses_cached_account_and_returns_csv(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "results": [
                    {
                        "tables": [
                            {
                                "rows": [
                                    {"[__out0]": "North", "[__out1]": "15"},
                                    {"[__out0]": "South", "[__out1]": "20"},
                                ]
                            }
                        ]
                    }
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, **kwargs):
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(pbi_visual_query.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        pbi_visual_query,
        "get_access_token",
        lambda: {"access_token": "cached-token", "account": "user@example.com"},
    )
    monkeypatch.setattr(pbi_visual_query, "resolve_proxy", lambda url: "http://proxy.example")

    result = pbi_visual_query.execute_visual_query(
        workspace_id="11111111-1111-1111-1111-111111111111",
        dataset_id="22222222-2222-2222-2222-222222222222",
        query_spec={
            "fields": [
                {"target": {"table": "T", "column": "Region"}, "displayName": "Region"},
                {"target": {"table": "M", "measure": "Count"}, "displayName": "Count"},
            ],
            "filters": [],
        },
        max_rows=100,
    )

    assert result["data"] == "Region,Count\r\nNorth,15\r\nSouth,20\r\n"
    assert result["row_count"] == 2
    assert captured["client_kwargs"]["proxy"] == "http://proxy.example"
    assert captured["post_kwargs"]["headers"]["Authorization"] == "Bearer cached-token"
    assert captured["post_kwargs"]["json"]["serializerSettings"] == {"includeNulls": True}
    assert captured["url"].endswith(
        "/groups/11111111-1111-1111-1111-111111111111/"
        "datasets/22222222-2222-2222-2222-222222222222/executeQueries"
    )


def test_execute_visual_query_fails_closed_instead_of_sending_partial_rows(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "results": [
                    {"tables": [{"rows": [{"[__out0]": value} for value in (1, 2, 3)]}]}
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(pbi_visual_query.httpx, "Client", FakeClient)
    monkeypatch.setattr(pbi_visual_query, "get_access_token", lambda: {"access_token": "token"})
    monkeypatch.setattr(pbi_visual_query, "resolve_proxy", lambda url: None)

    with pytest.raises(pbi_visual_query.PbiVisualQueryError, match="more than the configured 2 row"):
        pbi_visual_query.execute_visual_query(
            workspace_id="11111111-1111-1111-1111-111111111111",
            dataset_id="22222222-2222-2222-2222-222222222222",
            query_spec={
                "fields": [{"target": {"table": "T", "column": "Value"}}],
                "filters": [],
            },
            max_rows=2,
        )


def test_visual_export_uses_execute_queries_only_for_visual_query_failure(monkeypatch):
    monkeypatch.setattr(
        pbi_visual_export,
        "_run_visual_action",
        lambda **kwargs: {
            "exportError": {"message": "Error running visual data query."},
            "querySpec": {"fields": [{"target": {"table": "T", "column": "Value"}}]},
            "visual": {"name": "visual1", "type": "table", "title": "Exceptions"},
        },
    )
    calls = []
    monkeypatch.setattr(
        pbi_visual_export,
        "execute_visual_query",
        lambda **kwargs: calls.append(kwargs) or {"data": "Value\r\n1\r\n"},
    )

    result = pbi_visual_export.export_visual_data(
        "22222222-2222-2222-2222-222222222222",
        "https://app.powerbi.com/reportEmbed?reportId=22222222-2222-2222-2222-222222222222",
        "ReportSection",
        "visual1",
        workspace_id="11111111-1111-1111-1111-111111111111",
        dataset_id="33333333-3333-3333-3333-333333333333",
    )

    assert result["data"] == "Value\r\n1\r\n"
    assert result["export_method"] == "execute_queries"
    assert calls[0]["workspace_id"] == "11111111-1111-1111-1111-111111111111"
    assert calls[0]["dataset_id"] == "33333333-3333-3333-3333-333333333333"


def test_visual_export_applies_whole_number_format_metadata(monkeypatch):
    monkeypatch.setattr(
        pbi_visual_export,
        "_run_visual_action",
        lambda **kwargs: {
            "data": "Count\r\n42.000000\r\n",
            "querySpec": {
                "fields": [
                    {
                        "target": {"table": "Measures", "measure": "Count"},
                        "displayName": "Count",
                        "formatString": "#,0",
                    }
                ]
            },
            "visual": {"name": "visual1", "type": "table", "title": "Exceptions"},
        },
    )

    result = pbi_visual_export.export_visual_data(
        "22222222-2222-2222-2222-222222222222",
        "https://app.powerbi.com/reportEmbed?reportId=22222222-2222-2222-2222-222222222222",
        "ReportSection",
        "visual1",
    )

    assert result["data"] == "Count\r\n42\r\n"
    assert result["export_method"] == "visual_export"


def test_visual_export_applies_matrix_value_decimal_places(monkeypatch):
    monkeypatch.setattr(
        pbi_visual_export,
        "_run_visual_action",
        lambda **kwargs: {
            "data": "Sales (Qty) L4W Avg\r\n370.75\r\n",
            "querySpec": {
                "fields": [
                    {
                        "target": {"table": "Measures", "measure": "Sales Qty L4W Avg"},
                        "displayName": "Sales (Qty) L4W Avg",
                        "formatString": "#,0.00",
                    }
                ],
                "visualFormatting": {
                    "valueDecimalPlaces": 0,
                    "valueDecimalPlacesSource": "visual",
                },
            },
            "visual": {"name": "visual1", "type": "pivotTable", "title": ""},
        },
    )

    result = pbi_visual_export.export_visual_data(
        "22222222-2222-2222-2222-222222222222",
        "https://app.powerbi.com/reportEmbed?reportId=22222222-2222-2222-2222-222222222222",
        "ReportSection",
        "visual1",
    )

    assert result["data"] == "Sales (Qty) L4W Avg\r\n371\r\n"


def test_visual_export_evaluates_expression_based_title(monkeypatch):
    monkeypatch.setattr(
        pbi_visual_export,
        "_run_visual_action",
        lambda **kwargs: {
            "data": "Value\r\n1\r\n",
            "querySpec": {
                "fields": [
                    {
                        "target": {"table": "Measures", "measure": "Value"},
                        "displayName": "Value",
                        "formatString": "#,0",
                    }
                ],
                "filters": [
                    {
                        "source": "slicer:region",
                        "filter": {
                            "$schema": "http://powerbi.com/product/schema#basic",
                            "target": {"table": "Region", "column": "Name"},
                            "operator": "In",
                            "values": ["North"],
                        },
                    }
                ],
            },
            "visual": {
                "name": "visual1",
                "type": "pivotTable",
                "title": "",
                "titleSource": "expression",
                "titleTarget": {"table": "Measures", "measure": "Title Alert 2"},
            },
        },
    )
    calls = []
    monkeypatch.setattr(
        pbi_visual_export,
        "execute_visual_query",
        lambda **kwargs: calls.append(kwargs)
        or {"data": "Visual title\r\nRecommended S/I Qty - Week 202629\r\n"},
    )

    result = pbi_visual_export.export_visual_data(
        "22222222-2222-2222-2222-222222222222",
        "https://app.powerbi.com/reportEmbed?reportId=22222222-2222-2222-2222-222222222222",
        "ReportSection",
        "visual1",
        workspace_id="11111111-1111-1111-1111-111111111111",
        dataset_id="33333333-3333-3333-3333-333333333333",
    )

    assert result["visual"]["title"] == "Recommended S/I Qty - Week 202629"
    assert result["visual"]["titleSource"] == "expression"
    assert calls[0]["query_spec"]["fields"][0]["target"] == {
        "table": "Measures",
        "measure": "Title Alert 2",
    }
    assert calls[0]["query_spec"]["filters"][0]["source"] == "slicer:region"


def test_authoring_loader_falls_back_to_second_package_cdn():
    class FakePage:
        def __init__(self):
            self.urls = []

        def add_script_tag(self, *, url):
            self.urls.append(url)
            if len(self.urls) == 1:
                raise RuntimeError("blocked")

        def evaluate(self, expression):
            return True

    page = FakePage()
    selected = pbi_visual_export._load_power_bi_authoring(page)

    assert page.urls == list(pbi_visual_export.POWER_BI_AUTHORING_URLS)
    assert selected == pbi_visual_export.POWER_BI_AUTHORING_URLS[1]


def test_runtime_reads_visual_fields_and_slicer_filters_for_rest_fallback():
    runtime = pbi_visual_export._RUNTIME_HTML

    assert 'objectName: "title"' in runtime
    assert '["titleText", "text"]' in runtime
    assert "propertyName" in runtime
    assert "visual.getProperty" in runtime
    assert 'titleSource: "property"' in runtime
    assert "titleMeasureTarget" in runtime
    assert 'titleSource: dynamicTitleTarget ? "expression" : null' in runtime
    assert 'genericTitles = new Set(["matrix", "pivottable", "table", "tableex"])' in runtime
    assert 'report.on("rendered"' in runtime
    assert "visual.page.getVisuals()" in runtime
    assert 'objectName: "columnFormatting"' in runtime
    assert 'propertyName: "labelPrecision"' in runtime
    assert "valueDecimalPlaces" in runtime
    assert 'precisionSchema.endsWith("#default")' in runtime
    assert 'precisionSchema === "" || precisionSchema.endsWith("#property")' in runtime
    assert 'valueDecimalPlacesSource: precisionIsExplicit ? "visual" : null' in runtime
    assert "() => selectedVisual.getCapabilities()" in runtime
    assert '? ["Rows", "Columns", "Values"]' in runtime
    assert "() => selectedVisual.getDataFields(role.name)" in runtime
    assert "const targets = Array.isArray(roleTargets) ? roleTargets : []" in runtime
    assert "selectedVisual.getFieldFormatString" in runtime
    assert "data: result.data" in runtime
    assert "querySpec," in runtime
    assert "visual.getSlicerState()" in runtime
    assert "querySpec" in runtime
