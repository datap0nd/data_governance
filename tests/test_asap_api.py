from pathlib import Path

import pytest

from app.asap_api import (
    MicroStrategyAPI,
    MicroStrategyAPIError,
    library_base_url,
    report_pages_to_rows,
)


class FakeResponse:
    def __init__(self, status, payload=None, headers=None, text=""):
        self.status = status
        self._payload = payload
        self.headers = headers or {}
        self._text = text
        self.disposed = False

    def json(self):
        return self._payload

    def text(self):
        return self._text

    def dispose(self):
        self.disposed = True


class TokenRequest:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        return self.response


class FakePage:
    def __init__(self, request):
        self.context = type("Context", (), {"request": request})()


def test_library_base_uses_asap_origin_without_portal_paths():
    assert library_base_url({"auth_url": "https://example.test/portal/login/app"}) == (
        "https://example.test/MicroStrategyLibrary"
    )


def test_browser_session_is_exchanged_for_short_lived_api_token():
    response = FakeResponse(204, headers={"x-mstr-authtoken": "ephemeral"})
    request = TokenRequest(response)
    api = MicroStrategyAPI.from_browser(
        FakePage(request), {"auth_url": "https://example.test/portal/login/app"}
    )
    assert api.auth_token == "ephemeral"
    assert request.urls[0][0].endswith("/MicroStrategyLibrary/api/auth/token")
    assert response.disposed is True


def test_browser_session_fails_closed_when_library_rest_is_unavailable():
    response = FakeResponse(404, text="not found")
    with pytest.raises(MicroStrategyAPIError, match="Library REST"):
        MicroStrategyAPI.from_browser(
            FakePage(TokenRequest(response)),
            {"auth_url": "https://example.test/portal/login/app"},
        )


class PagedRequest:
    def __init__(self):
        self.offsets = []

    def get(self, url, **kwargs):
        offset = int(kwargs["params"]["offset"])
        self.offsets.append(offset)
        size = 200 if offset == 0 else 1
        elements = [{"id": f"h{offset + i}", "name": f"Option {offset + i}"} for i in range(size)]
        return FakeResponse(200, {"elements": elements})


def test_prompt_members_are_paged_until_the_api_is_exhausted():
    request = PagedRequest()
    api = MicroStrategyAPI(request, "https://example.test/MicroStrategyLibrary", "token")
    options = api.prompt_options(
        "project", "report", "instance", {"key": "prompt", "type": "ELEMENTS"}
    )
    assert len(options) == 201
    assert request.offsets == [0, 200]


def _report_page(offset, names, metrics):
    return {
        "status": 1,
        "definition": {
            "grid": {
                "crossTab": False,
                "rows": [{
                    "name": "Region",
                    "forms": [{"name": "DESC"}],
                    "elements": [{"formValues": [name]} for name in names],
                }],
                "columns": [{
                    "type": "templateMetrics",
                    "elements": [{"name": "Revenue"}, {"name": "Units"}],
                }],
            }
        },
        "data": {
            "paging": {"offset": offset, "current": len(metrics), "total": len(metrics)},
            "headers": {"rows": [[index] for index in range(len(metrics))], "columns": [[0, 1]]},
            "metricValues": {"raw": metrics},
        },
    }


def test_data_api_grid_is_flattened_from_definition_and_raw_values():
    header, rows = report_pages_to_rows([
        _report_page(0, ["North", "South"], [[10.5, 2], [20, 4]])
    ])
    assert header == ["Region", "Revenue", "Units"]
    assert rows == [["North", 10.5, 2], ["South", 20, 4]]


def test_each_result_page_uses_its_own_attribute_element_dictionary():
    header, rows = report_pages_to_rows([
        _report_page(0, ["North"], [[10.5, 2]]),
        _report_page(1, ["South"], [[20, 4]]),
    ])
    assert header == ["Region", "Revenue", "Units"]
    assert rows == [["North", 10.5, 2], ["South", 20, 4]]


def test_cross_tab_export_fails_instead_of_guessing_a_csv_shape():
    page = _report_page(0, ["North"], [[10.5, 2]])
    page["definition"]["grid"]["crossTab"] = True
    with pytest.raises(MicroStrategyAPIError, match="Cross-tab"):
        report_pages_to_rows([page])


def test_prompt_answers_use_catalogued_member_ids_and_canonical_weeks():
    api = MicroStrategyAPI(None, "https://example.test/MicroStrategyLibrary", "token")
    job = {
        "selections": {},
        "report": {
            "filters": [{
                "filter_key": "sell_out_week",
                "label": "Sell-out Week",
                "control_type": "week",
                "required": True,
                "automation": {
                    "prompt_key": "week-key",
                    "prompt_type": "ELEMENTS",
                    "answers": {"2026-W31": {"id": "h202631;week"}},
                },
            }]
        },
    }
    body = api._answer_body(
        job,
        [{"key": "week-key", "title": "Sell-out Week", "type": "ELEMENTS", "required": True}],
        ["2026-W31"],
    )
    assert body == {
        "prompts": [{
            "key": "week-key",
            "type": "ELEMENTS",
            "answers": [{"id": "h202631;week"}],
        }]
    }


def test_csv_download_uses_exclusive_create(tmp_path, monkeypatch):
    api = MicroStrategyAPI(None, "https://example.test/MicroStrategyLibrary", "token")
    monkeypatch.setattr(api, "execute_report", lambda job, period: [
        _report_page(0, ["North"], [[10.5, 2]])
    ])
    target = tmp_path / "result.csv"
    assert api.download_csv({}, None, target) == Path(target)
    assert target.read_text(encoding="utf-8-sig").splitlines() == [
        "Region,Revenue,Units", "North,10.5,2"
    ]
    with pytest.raises(FileExistsError):
        api.download_csv({}, None, target)
