from app.scanner import pbi_fetch


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
                    "serviceExceptionJson": (
                        '{"errorCode":"ModelRefreshFailed",'
                        '"errorDescription":"Gateway could not reach the source."}'
                    ),
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
