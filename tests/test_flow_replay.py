"""Network-level export replay: capture, storage, validation, and fallback.

The module under test never talks to a real browser: the recorder is fed
fake Playwright events and replay is fed a fake ``context.request`` client,
which is exactly the seam the production code uses. What these tests pin is
the safety model - what gets stored, what never gets stored, and every
reason a replay must be rejected in favor of the browser flow.
"""

import base64
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import flow_replay, flow_worker


# ── Fake Playwright objects ──


class _FakeEmitter:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        self.handlers.get(event, []).remove(handler)

    def emit(self, event, payload):
        for handler in list(self.handlers.get(event, [])):
            handler(payload)


class _FakeContext(_FakeEmitter):
    def __init__(self, pages=(), request=None):
        super().__init__()
        self.pages = list(pages)
        self.request = request


def _request(url, method="GET", headers=None, post=None):
    return SimpleNamespace(
        url=url, method=method, headers=headers or {}, post_data_buffer=post,
    )


def _response(url, headers):
    return SimpleNamespace(url=url, headers=headers)


class _FakeAPIResponse:
    def __init__(self, status=200, body=b""):
        self.status = status
        self._body = body
        self.disposed = False

    @property
    def ok(self):
        return 200 <= self.status < 300

    def body(self):
        return self._body

    def dispose(self):
        self.disposed = True


class _FakeFetcher:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def fetch(self, url, **kwargs):
        if isinstance(self._response, Exception):
            raise self._response
        self.calls.append((url, kwargs))
        return self._response


JOB = {
    "site": {"id": 7, "name": "GSCM", "adapter": "gscm_portal"},
    "report": {"id": 42, "name": "MENA_Actual_sales"},
    "downloads": {"file_format": "xlsx"},
}
TASK_KEY = json.dumps({"export_view": None, "period_key": None}, sort_keys=True)
EXPORT_URL = "https://mdscm.sec.samsung.net/nexa/export/excel"


def _workbook_bytes(padding: int = 4096) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>" + "x" * padding)
    return buffer.getvalue()


def _staged_workbook(tmp_path):
    staged = tmp_path / "staged.xlsx"
    staged.write_bytes(_workbook_bytes())
    return staged


def _capture(tmp_path, *, headers=None, post=None, url=EXPORT_URL):
    """Run one fake export through the recorder and store its recipe."""
    context = _FakeContext(pages=[_FakeEmitter()])
    recorder = flow_replay.ExportRequestRecorder(context)
    context.emit("request", _request(
        url, method="POST",
        headers=headers if headers is not None else {"content-type": "application/x-www-form-urlencoded"},
        post=post,
    ))
    context.pages[0].emit("download", SimpleNamespace(url=url))
    stored = flow_replay.store_capture(
        tmp_path, JOB, TASK_KEY, recorder, _staged_workbook(tmp_path),
    )
    recorder.detach()
    return stored


# ── The recorder ──


def test_download_url_outranks_export_looking_responses():
    context = _FakeContext(pages=[_FakeEmitter()])
    recorder = flow_replay.ExportRequestRecorder(context)
    context.emit("request", _request("https://portal/keepalive"))
    context.emit("request", _request("https://portal/render", method="POST"))
    context.emit("request", _request(EXPORT_URL, method="POST"))
    context.emit("response", _response(
        "https://portal/render", {"content-type": "application/octet-stream"},
    ))
    context.pages[0].emit("download", SimpleNamespace(url=EXPORT_URL))
    assert recorder.best_request()["url"] == EXPORT_URL


def test_export_response_is_the_fallback_when_no_download_url_surfaced():
    context = _FakeContext()
    recorder = flow_replay.ExportRequestRecorder(context)
    context.emit("request", _request("https://portal/page"))
    context.emit("request", _request(EXPORT_URL, method="POST"))
    context.emit("response", _response(
        EXPORT_URL, {"content-disposition": 'attachment; filename="a.xlsx"'},
    ))
    assert recorder.best_request()["url"] == EXPORT_URL


def test_pages_opened_mid_export_are_observed():
    context = _FakeContext()
    recorder = flow_replay.ExportRequestRecorder(context)
    popup = _FakeEmitter()
    context.emit("page", popup)
    context.emit("request", _request(EXPORT_URL))
    popup.emit("download", SimpleNamespace(url=EXPORT_URL))
    assert recorder.best_request()["url"] == EXPORT_URL


def test_nothing_matched_returns_none():
    recorder = flow_replay.ExportRequestRecorder(_FakeContext())
    assert recorder.best_request() is None


def test_detach_removes_every_listener():
    page = _FakeEmitter()
    context = _FakeContext(pages=[page])
    recorder = flow_replay.ExportRequestRecorder(context)
    recorder.detach()
    assert not any(context.handlers.values())
    assert not any(page.handlers.values())


# ── Capture storage ──


def test_capture_round_trip_stores_method_url_and_body(tmp_path):
    assert _capture(tmp_path, post=b"reportId=42&format=xlsx")
    recipe = flow_replay.load_recipe(tmp_path, JOB, TASK_KEY)
    assert recipe["url"] == EXPORT_URL
    assert recipe["method"] == "POST"
    assert base64.b64decode(recipe["post_b64"]) == b"reportId=42&format=xlsx"
    assert recipe["expected_kind"] == "excel"


def test_cookie_and_authorization_headers_are_never_stored(tmp_path):
    assert _capture(tmp_path, headers={
        "Cookie": "SESSION=secret", "Authorization": "Bearer secret",
        "Content-Type": "application/x-www-form-urlencoded",
        ":authority": "mdscm.sec.samsung.net",
    })
    recipe = flow_replay.load_recipe(tmp_path, JOB, TASK_KEY)
    stored = {key.casefold() for key in recipe["headers"]}
    assert stored == {"content-type"}
    assert "secret" not in json.dumps(recipe)


def test_html_baseline_refuses_to_become_a_recipe(tmp_path):
    context = _FakeContext()
    recorder = flow_replay.ExportRequestRecorder(context)
    context.emit("request", _request(EXPORT_URL))
    context.emit("response", _response(
        EXPORT_URL, {"content-disposition": "attachment"},
    ))
    staged = tmp_path / "staged.xlsx"
    staged.write_bytes(b"<html><body>Single Sign On Login</body></html>")
    assert not flow_replay.store_capture(tmp_path, JOB, TASK_KEY, recorder, staged)
    assert flow_replay.load_recipe(tmp_path, JOB, TASK_KEY) is None


def test_recipes_are_keyed_by_task_so_periods_never_cross(tmp_path):
    assert _capture(tmp_path)
    other_task = json.dumps(
        {"export_view": None, "period_key": "2026-W30"}, sort_keys=True,
    )
    assert flow_replay.load_recipe(tmp_path, JOB, other_task) is None


def test_expired_recipes_are_dropped_on_load(tmp_path):
    assert _capture(tmp_path)
    data = json.loads((tmp_path / flow_replay.RECIPES_FILENAME).read_text())
    key = flow_replay.recipe_key(JOB, TASK_KEY)
    data[key]["captured_at"] = (
        datetime.now(timezone.utc)
        - timedelta(days=flow_replay.RECIPE_MAX_AGE_DAYS + 1)
    ).isoformat()
    (tmp_path / flow_replay.RECIPES_FILENAME).write_text(json.dumps(data))
    assert flow_replay.load_recipe(tmp_path, JOB, TASK_KEY) is None
    # The expired entry is also pruned from the store, not just skipped.
    remaining = json.loads((tmp_path / flow_replay.RECIPES_FILENAME).read_text())
    assert key not in remaining


def test_recipe_save_retries_a_transient_windows_replace_lock(tmp_path, monkeypatch):
    original_replace = Path.replace
    attempts = 0

    def briefly_locked(path, target):
        nonlocal attempts
        if path.name.endswith(".json.tmp") and attempts == 0:
            attempts += 1
            raise PermissionError("file is momentarily held by an indexer")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", briefly_locked)

    assert _capture(tmp_path)
    assert attempts == 1
    assert flow_replay.load_recipe(tmp_path, JOB, TASK_KEY) is not None


def test_forget_recipe_removes_only_the_named_task(tmp_path):
    assert _capture(tmp_path)
    flow_replay.forget_recipe(tmp_path, JOB, TASK_KEY)
    assert flow_replay.load_recipe(tmp_path, JOB, TASK_KEY) is None


def test_corrupt_recipe_file_is_treated_as_empty(tmp_path):
    (tmp_path / flow_replay.RECIPES_FILENAME).write_text("not json")
    assert flow_replay.load_recipe(tmp_path, JOB, TASK_KEY) is None
    assert _capture(tmp_path)  # and it can be rewritten


# ── The kill switches ──


def test_enabled_by_default(monkeypatch):
    monkeypatch.delenv("DG_FLOW_REPLAY", raising=False)
    assert flow_replay.enabled(JOB)


def test_environment_kill_switch(monkeypatch):
    monkeypatch.setenv("DG_FLOW_REPLAY", "0")
    assert not flow_replay.enabled(JOB)


def test_per_flow_opt_out(monkeypatch):
    monkeypatch.delenv("DG_FLOW_REPLAY", raising=False)
    job = {**JOB, "downloads": {**JOB["downloads"], "network_replay": False}}
    assert not flow_replay.enabled(job)


# ── Replay ──


def _recipe(**overrides):
    recipe = {
        "version": flow_replay.RECIPE_VERSION,
        "url": EXPORT_URL,
        "method": "POST",
        "headers": {"content-type": "application/x-www-form-urlencoded"},
        "post_b64": base64.b64encode(b"reportId=42").decode("ascii"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "expected_kind": "excel",
        "baseline_bytes": 10_000,
    }
    recipe.update(overrides)
    return recipe


def test_replay_stages_a_workbook(tmp_path):
    response = _FakeAPIResponse(body=_workbook_bytes())
    context = _FakeContext(request=_FakeFetcher(response))
    staged = flow_replay.try_replay(context, _recipe(), tmp_path)
    assert staged is not None and staged.suffix == ".xlsx"
    assert staged.read_bytes() == _workbook_bytes()
    assert response.disposed
    url, kwargs = context.request.calls[0]
    assert url == EXPORT_URL
    assert kwargs["method"] == "POST"
    assert kwargs["data"] == b"reportId=42"
    assert kwargs["fail_on_status_code"] is False


def test_replay_names_a_legacy_workbook_xls(tmp_path):
    body = b"\xd0\xcf\x11\xe0" + b"\x01" * 4096
    context = _FakeContext(request=_FakeFetcher(_FakeAPIResponse(body=body)))
    staged = flow_replay.try_replay(context, _recipe(), tmp_path)
    assert staged is not None and staged.suffix == ".xls"


def test_replay_rejects_a_sign_in_page(tmp_path):
    body = ("<html><body>Single Sign On Login" + "x" * 4096 + "</body>").encode()
    context = _FakeContext(request=_FakeFetcher(_FakeAPIResponse(body=body)))
    assert flow_replay.try_replay(context, _recipe(), tmp_path) is None


def test_replay_rejects_sign_in_text_even_for_csv_recipes(tmp_path):
    body = ("please enter your password," + "a,b,c\n" * 200).encode()
    context = _FakeContext(request=_FakeFetcher(_FakeAPIResponse(body=body)))
    recipe = _recipe(expected_kind="text", post_b64=None, method="GET")
    assert flow_replay.try_replay(context, recipe, tmp_path) is None


def test_replay_accepts_a_csv_for_a_text_recipe(tmp_path):
    body = ("week,region,qty\n" + "2026-W30,MENA,5\n" * 100).encode()
    context = _FakeContext(request=_FakeFetcher(_FakeAPIResponse(body=body)))
    recipe = _recipe(expected_kind="text", post_b64=None, method="GET")
    staged = flow_replay.try_replay(context, recipe, tmp_path)
    assert staged is not None and staged.suffix == ".csv"


def test_replay_rejects_error_status(tmp_path):
    response = _FakeAPIResponse(status=500, body=_workbook_bytes())
    context = _FakeContext(request=_FakeFetcher(response))
    assert flow_replay.try_replay(context, _recipe(), tmp_path) is None
    assert response.disposed


def test_replay_rejects_a_stub_body(tmp_path):
    context = _FakeContext(request=_FakeFetcher(_FakeAPIResponse(body=b"PK")))
    assert flow_replay.try_replay(context, _recipe(), tmp_path) is None


def test_replay_rejects_the_wrong_file_family(tmp_path):
    body = ("a,b,c\n" * 200).encode()  # text, but the recipe expects excel
    context = _FakeContext(request=_FakeFetcher(_FakeAPIResponse(body=body)))
    assert flow_replay.try_replay(context, _recipe(), tmp_path) is None


def test_replay_swallows_network_errors(tmp_path):
    context = _FakeContext(request=_FakeFetcher(RuntimeError("connection reset")))
    assert flow_replay.try_replay(context, _recipe(), tmp_path) is None
    assert not list(tmp_path.iterdir())


def _asap_job(target: Path) -> dict:
    return {
        "flow": {"id": 1, "name": "Weekly"},
        "site": {"id": 7, "name": "ASAP", "adapter": "asap_portal"},
        "report": {"id": 42, "name": "Weekly", "filters": [], "export_views": []},
        "selections": {},
        "downloads": {
            "periods": [None], "target_folder": str(target), "file_format": "csv",
            "filename_template": "weekly_export.csv",
        },
    }


def _seed_recipe(profile: Path, job: dict, **overrides) -> str:
    key = flow_replay.recipe_key(job, flow_worker._export_task_key(None, None))
    profile.mkdir(parents=True, exist_ok=True)
    (profile / flow_replay.RECIPES_FILENAME).write_text(
        json.dumps({key: _recipe(**overrides)}), encoding="utf-8",
    )
    return key


def test_execute_job_replays_a_recorded_recipe_instead_of_the_browser(tmp_path):
    """A stored recipe makes the whole export run without any page driving.

    The fake page exposes nothing but ``context``: any attempt to navigate,
    locate, or wait would raise, so a passing run is proof that the portal
    UI was never touched.
    """
    target = tmp_path / "target"
    target.mkdir()
    profile = tmp_path / "profile"
    job = _asap_job(target)
    _seed_recipe(profile, job, expected_kind="text", method="GET", post_b64=None)
    body = ("week,region,qty\n" + "2026-W30,MENA,5\n" * 100).encode()
    page = SimpleNamespace(context=_FakeContext(
        request=_FakeFetcher(_FakeAPIResponse(body=body)),
    ))
    progress = []

    artifacts, _timings = flow_worker.execute_job(
        page, job, lambda *args: progress.append(args), profile,
        tmp_path / "staging", run_id=9, register_folder=lambda _folder: {"ops": []},
    )

    assert len(artifacts) == 1
    assert artifacts[0]["export_transport"] == "http_replay"
    saved = Path(artifacts[0]["file_path"])
    assert saved.is_file()
    assert saved.read_text(encoding="utf-8-sig").startswith("week,region,qty")
    messages = [detail.get("message", "") for _s, detail, *_r in progress]
    assert any("replaying the recorded HTTP export request" in m for m in messages)


def test_execute_job_falls_back_to_the_browser_when_replay_fails(tmp_path, monkeypatch):
    """A dead recipe costs one HTTP round trip, then the UI flow runs.

    The recipe is also forgotten, so the next run goes straight to the
    browser instead of repeating the doomed request.
    """
    target = tmp_path / "target"
    target.mkdir()
    profile = tmp_path / "profile"
    job = _asap_job(target)
    key = _seed_recipe(profile, job, expected_kind="text", method="GET", post_b64=None)

    monkeypatch.setattr(flow_worker, "_asap_open_report", lambda *_args: object())
    monkeypatch.setattr(
        flow_worker, "_asap_activate_export_view",
        lambda _page, frame, label: (frame, label),
    )
    monkeypatch.setattr(flow_worker, "_asap_apply_configuration", lambda *_args: None)
    monkeypatch.setattr(flow_worker, "_has_named_control", lambda *_args: False)

    def fake_download(_page, _frame, _job, staging_dir):
        path = Path(staging_dir) / "source.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value,units\nitem,1\n", encoding="utf-8")
        return path, []

    monkeypatch.setattr(flow_worker, "_asap_download", fake_download)
    page = SimpleNamespace(context=_FakeContext(
        request=_FakeFetcher(_FakeAPIResponse(status=500)),
    ))
    progress = []

    artifacts, _timings = flow_worker.execute_job(
        page, job, lambda *args: progress.append(args), profile,
        tmp_path / "staging", run_id=9, register_folder=lambda _folder: {"ops": []},
    )

    assert len(artifacts) == 1
    assert artifacts[0]["export_transport"] == "browser"
    stored = json.loads((profile / flow_replay.RECIPES_FILENAME).read_text())
    assert key not in stored
    messages = [detail.get("message", "") for _s, detail, *_r in progress]
    assert any("falling back to the browser" in m for m in messages)


def test_captured_recipe_replays_end_to_end(tmp_path):
    assert _capture(tmp_path, post=b"reportId=42")
    recipe = flow_replay.load_recipe(tmp_path, JOB, TASK_KEY)
    context = _FakeContext(request=_FakeFetcher(
        _FakeAPIResponse(body=_workbook_bytes()),
    ))
    staging = tmp_path / "staging"
    staged = flow_replay.try_replay(context, recipe, staging)
    assert staged is not None and staged.parent == staging
