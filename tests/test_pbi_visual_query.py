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

    assert "selectedVisual.getCapabilities()" in runtime
    assert "selectedVisual.getDataFields(role.name)" in runtime
    assert "selectedVisual.getFieldFormatString" in runtime
    assert "visual.getSlicerState()" in runtime
    assert "querySpec" in runtime
