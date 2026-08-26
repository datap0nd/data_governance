from app.scanner import pbi_fetch


def test_trigger_dataset_refresh_posts_to_power_bi_and_returns_tracking_headers(monkeypatch):
    observed = {}

    class Response:
        status_code = 202
        text = ""
        headers = {
            "x-ms-request-id": "request-123",
            "location": "https://api.powerbi.com/refreshes/request-123",
        }

    class FakeClient:
        def __init__(self, *, timeout, proxy):
            observed.update(timeout=timeout, proxy=proxy)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, *, headers, json):
            observed.update(url=url, headers=headers, json=json)
            return Response()

    monkeypatch.setattr(
        pbi_fetch,
        "get_access_token",
        lambda: {"access_token": "cached-token", "account": "analyst@example.test"},
    )
    monkeypatch.setattr(pbi_fetch, "resolve_proxy", lambda _url: None)
    monkeypatch.setattr(pbi_fetch.httpx, "Client", FakeClient)
    monkeypatch.setattr(
        pbi_fetch,
        "_find_workspace",
        lambda _client, _token, _name: {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Governed Workspace",
        },
    )

    result = pbi_fetch.trigger_dataset_refresh(
        "Governed Workspace",
        "33333333-3333-3333-3333-333333333333",
    )

    assert observed["url"].endswith(
        "/groups/11111111-1111-1111-1111-111111111111/"
        "datasets/33333333-3333-3333-3333-333333333333/refreshes"
    )
    assert observed["headers"] == {"Authorization": "Bearer cached-token"}
    assert observed["json"] == {"notifyOption": "MailOnFailure"}
    assert result["status"] == "accepted"
    assert result["request_id"] == "request-123"

    pipeline_result = pbi_fetch.trigger_dataset_refresh(
        "Governed Workspace",
        "33333333-3333-3333-3333-333333333333",
        notify_option="NoNotification",
    )
    assert observed["json"] == {"notifyOption": "NoNotification"}
    assert pipeline_result["request_id"] == "request-123"


def test_resolve_report_dataset_matches_name_case_insensitively(monkeypatch):
    monkeypatch.setattr(
        pbi_fetch,
        "fetch_workspace_reports",
        lambda _workspace: {
            "workspace": {"id": "workspace-1", "name": "Governed Workspace"},
            "reports": [
                {
                    "id": "report-1",
                    "name": "Daily Sellout",
                    "dataset_id": "dataset-1",
                    "web_url": "https://app.powerbi.test/reports/report-1",
                }
            ],
        },
    )

    result = pbi_fetch.resolve_report_dataset("Governed Workspace", " daily sellout ")

    assert result == {
        "workspace": {"id": "workspace-1", "name": "Governed Workspace"},
        "report_id": "report-1",
        "report_name": "Daily Sellout",
        "dataset_id": "dataset-1",
        "web_url": "https://app.powerbi.test/reports/report-1",
    }


def test_resolve_report_dataset_rejects_ambiguous_names(monkeypatch):
    monkeypatch.setattr(
        pbi_fetch,
        "fetch_workspace_reports",
        lambda _workspace: {
            "workspace": {"id": "workspace-1", "name": "Governed Workspace"},
            "reports": [
                {"id": "report-1", "name": "Shared Name", "dataset_id": "dataset-1"},
                {"id": "report-2", "name": "Shared Name", "dataset_id": "dataset-2"},
            ],
        },
    )

    try:
        pbi_fetch.resolve_report_dataset("Governed Workspace", "Shared Name")
    except pbi_fetch.PbiFetchError as exc:
        assert "multiple semantic models" in str(exc)
    else:
        raise AssertionError("Expected an ambiguous Power BI report name to fail")


def test_fetch_dataset_last_refresh_uses_cached_auth_and_extracts_failure_reason(monkeypatch):
    observed = {}

    class FakeClient:
        def __init__(self, *, timeout, proxy):
            observed["timeout"] = timeout
            observed["proxy"] = proxy

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        pbi_fetch,
        "get_access_token",
        lambda: {"access_token": "cached-access-token"},
    )
    def fake_resolve_proxy(url):
        observed["proxy_url"] = url
        return "http://proxy"

    monkeypatch.setattr(pbi_fetch, "resolve_proxy", fake_resolve_proxy)
    monkeypatch.setattr(pbi_fetch.httpx, "Client", FakeClient)

    def fake_get_json(client, token, url, params=None):
        observed.update({"client": client, "token": token, "url": url, "params": params})
        return {
            "value": [
                {
                    "status": "Failed",
                    "startTime": "2026-07-16T07:55:00Z",
                    "endTime": "2026-07-16T08:00:00Z",
                    "serviceExceptionJson": '{"errorCode":"ModelRefreshFailed"}',
                    "refreshAttempts": [
                        {
                            "serviceExceptionJson": (
                                '{"errorCode":"GatewayError",'
                                '"errorDescription":"Gateway could not reach the source."}'
                            )
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(pbi_fetch, "_get_json", fake_get_json)

    result = pbi_fetch.fetch_dataset_last_refresh(
        "11111111-1111-1111-1111-111111111111",
        "33333333-3333-3333-3333-333333333333",
    )

    assert observed["token"] == "cached-access-token"
    assert observed["proxy"] == "http://proxy"
    assert observed["params"] == {"$top": 1}
    assert observed["url"].endswith(
        "/groups/11111111-1111-1111-1111-111111111111/"
        "datasets/33333333-3333-3333-3333-333333333333/refreshes"
    )
    assert result == {
        "status": "Failed",
        "start_time": "2026-07-16T07:55:00Z",
        "end_time": "2026-07-16T08:00:00Z",
        "error": "Gateway could not reach the source.",
    }


def test_refresh_history_matches_exact_request_id_not_latest(monkeypatch):
    observed = {}

    class FakeClient:
        def __init__(self, *, timeout, proxy):
            observed.update(timeout=timeout, proxy=proxy)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(pbi_fetch, "get_access_token", lambda: {"access_token": "token"})
    monkeypatch.setattr(pbi_fetch, "resolve_proxy", lambda _url: None)
    monkeypatch.setattr(pbi_fetch.httpx, "Client", FakeClient)

    def fake_get_json(_client, _token, _url, params=None):
        observed["params"] = params
        return {
            "value": [
                {"requestId": "newer-other-request", "status": "Completed"},
                {
                    "requestId": "pipeline-request",
                    "status": "Failed",
                    "startTime": "2026-08-26T10:00:00Z",
                    "endTime": "2026-08-26T10:03:00Z",
                    "serviceExceptionJson": '{"errorDescription":"Source unavailable"}',
                },
            ]
        }

    monkeypatch.setattr(pbi_fetch, "_get_json", fake_get_json)
    result = pbi_fetch.fetch_dataset_refresh_by_request_id(
        "11111111-1111-1111-1111-111111111111",
        "33333333-3333-3333-3333-333333333333",
        "PIPELINE-REQUEST",
    )
    assert observed["params"] == {"$top": 60}
    assert result["request_id"] == "pipeline-request"
    assert result["status"] == "Failed"
    assert result["error"] == "Source unavailable"
