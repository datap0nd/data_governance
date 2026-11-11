"""Regression tests for the worker audit findings (synthetic fakes only)."""
import traceback

import httpx
import pytest

from app import flow_worker


# --- F-06: the stored portal password never reaches a reported error -------

PORTAL_PASSWORD = "S3cr3t-Portal-Pass!"


class _SignInLocator:
    """Locator double; ``fill`` can fail the way Playwright does on a timeout."""

    def __init__(self, page, name, present=True):
        self.page, self.name, self.present = page, name, present
        self.first = self

    def count(self):
        return 1 if self.present else 0

    def nth(self, _index):
        return self

    def fill(self, value):
        self.page.actions.append(("fill", self.name))
        if self.name in self.page.failing:
            # Playwright appends its call log, which repeats the typed value.
            raise TimeoutError(
                f'Locator.fill: Timeout 30000ms exceeded.\nCall log:\n  - fill("{value}")\n'
            )

    def click(self):
        self.page.actions.append(("click", self.name))
        if self.name in self.page.failing:
            raise TimeoutError("Locator.click: Timeout 30000ms exceeded.")


class _SignInPage:
    def __init__(self, failing=(), submit=True):
        self.failing, self.submit, self.actions = set(failing), submit, []

    def locator(self, selector):
        if "password" in selector:
            return _SignInLocator(self, "password")
        if "submit" in selector:
            return _SignInLocator(self, "submit", present=self.submit)
        return _SignInLocator(self, "username")

    def get_by_role(self, _role, **_kwargs):
        return _SignInLocator(self, "submit", present=self.submit)


@pytest.fixture()
def no_remembered_secrets(monkeypatch):
    monkeypatch.setattr(flow_worker, "_REDACTED_SECRETS", set(), raising=False)


def _credentials():
    return {"username": "bi.desktop", "password": PORTAL_PASSWORD}


@pytest.mark.parametrize("failing", ["username", "password", "submit"])
def test_sign_in_failure_never_carries_the_typed_password(no_remembered_secrets, failing):
    page = _SignInPage(failing={failing})

    with pytest.raises(RuntimeError) as error:
        flow_worker._sso_fill_and_submit(page, _credentials(), "ASAP")

    assert "ASAP sign-in form could not be filled" in str(error.value)
    assert "TimeoutError" in str(error.value)
    # Neither the message nor the formatted traceback the worker forwards may
    # repeat Playwright's call log.
    reported = str(error.value) + "".join(traceback.format_exception(error.value))
    assert PORTAL_PASSWORD not in reported and "Call log" not in reported
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_sign_in_still_fills_submits_and_names_an_unrecognized_login_action(no_remembered_secrets):
    page = _SignInPage()
    flow_worker._sso_fill_and_submit(page, _credentials(), "GSCM")
    assert page.actions == [("fill", "username"), ("fill", "password"), ("click", "submit")]

    with pytest.raises(RuntimeError, match="GSCM sign-in form was found, but its Login action was not recognized"):
        flow_worker._sso_fill_and_submit(_SignInPage(submit=False), _credentials(), "GSCM")


def test_gscm_automatic_sign_in_failure_reports_no_password(no_remembered_secrets, monkeypatch, tmp_path):
    monkeypatch.setattr(flow_worker, "_asap_login_visible", lambda _page: True)
    monkeypatch.setattr(flow_worker, "load_asap_credentials", _credentials)

    def operation():
        raise flow_worker.flow_gscm.NotSignedInError("GSCM is not signed in.")

    with pytest.raises(RuntimeError) as error:
        flow_worker._gscm_call(
            _SignInPage(failing={"password"}), False, lambda _message: None, operation, tmp_path,
        )

    assert "attempted and failed: GSCM sign-in form could not be filled" in str(error.value)
    assert PORTAL_PASSWORD not in "".join(traceback.format_exception(error.value))


class _RecordingClient:
    """httpx.Client double that records what the worker would have sent."""

    def __init__(self):
        self.bodies = []

    def request(self, _method, _path, json=None, timeout=None):
        self.bodies.append(json)

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {}

        return Response()


def test_every_report_is_scrubbed_of_a_password_the_worker_typed(no_remembered_secrets):
    # Typing the credential is what registers it, even when sign-in succeeds.
    flow_worker._sso_fill_and_submit(_SignInPage(), _credentials(), "ASAP")
    leak = f'Locator.fill: Timeout\nCall log:\n  - fill("{PORTAL_PASSWORD}")'
    client = _RecordingClient()
    body = {
        "status": "failed",
        "progress": {"stage": "failed", "message": leak, "attempts": [{"error": leak}, 3]},
        "error": leak,
        "traceback": "Traceback (most recent call last):\n" + leak,
        "artifacts": [{"file_path": "C:/flows/run/export.csv"}],
        "lease_token": "0" * 32,
    }

    flow_worker._api(client, "POST", "/api/flows/worker/w/runs/1/progress", body)
    flow_worker._api(client, "POST", "/api/flows/worker/w/runs/1/tasks/abort", {"message": leak})

    sent, abort = client.bodies
    assert PORTAL_PASSWORD not in repr(client.bodies)
    assert sent["error"] == leak.replace(PORTAL_PASSWORD, "[redacted]")
    assert sent["progress"]["attempts"] == [{"error": sent["error"]}, 3]
    assert abort == {"message": sent["error"]}
    # Structural fields are passed through untouched.
    assert sent["artifacts"] == body["artifacts"] and sent["lease_token"] == body["lease_token"]
    assert sent["status"] == "failed" and sent["progress"]["stage"] == "failed"
    # The caller's own payload is not mutated.
    assert body["error"] == leak


def test_redaction_never_lengthens_a_field_the_server_caps(no_remembered_secrets):
    flow_worker._REDACTED_SECRETS.update({"pw7", PORTAL_PASSWORD})
    text = f"a pw7 b {PORTAL_PASSWORD} c" * 50

    scrubbed = flow_worker._redact_secrets(text)

    assert "pw7" not in scrubbed and PORTAL_PASSWORD not in scrubbed
    assert len(scrubbed) <= len(text)
    assert flow_worker._redact_secrets({"count": 3, "rows": None}) == {"count": 3, "rows": None}


def test_reports_are_unchanged_while_no_password_was_typed(no_remembered_secrets):
    client = _RecordingClient()
    body = {"status": "running", "progress": {"message": PORTAL_PASSWORD}}

    flow_worker._api(client, "POST", "/progress", body)

    assert client.bodies[0] is body


# --- F-05: a dead browser is replaced instead of failing every later run ----

class _LivePage:
    """Page double: optionally closed, crashed or too busy to answer a probe."""

    def __init__(self, closed=False, probe_error=None):
        self.closed, self.probe_error, self.probes = closed, probe_error, []

    def is_closed(self):
        return self.closed

    def wait_for_function(self, expression, timeout=None):
        self.probes.append((expression, timeout))
        if self.probe_error is not None:
            raise self.probe_error


class _LiveContext:
    def __init__(self, page=None, close_error=None):
        self.pages = [page or _LivePage()]
        self.close_error, self.close_calls = close_error, 0

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


@pytest.mark.parametrize("page, lost", [
    (_LivePage(), False),
    (_LivePage(closed=True), True),
    (_LivePage(probe_error=RuntimeError("Page.wait_for_function: Target crashed ")), True),
    (_LivePage(probe_error=RuntimeError("Page.goto: Page crashed")), True),
    (_LivePage(probe_error=RuntimeError(
        "Page.wait_for_function: Target page, context or browser has been closed")), True),
    # A slow or busy page is not evidence of a dead browser.
    (_LivePage(probe_error=flow_worker.PlaywrightTimeoutError("Timeout 5000ms exceeded.")), False),
    (_LivePage(probe_error=RuntimeError("Execution context was destroyed")), False),
    # Minimal doubles without a probe are treated as alive.
    (object(), False),
    (None, True),
])
def test_browser_liveness_needs_positive_evidence_of_a_dead_page(page, lost):
    assert flow_worker._browser_session_lost(_LiveContext(), page) is lost
    if isinstance(page, _LivePage) and not page.closed:
        assert page.probes == [("true", flow_worker.BROWSER_PROBE_TIMEOUT_MS)]


def test_a_missing_context_or_unreadable_page_state_counts_as_lost():
    class Unreadable(_LivePage):
        def is_closed(self):
            raise RuntimeError("connection closed")

    assert flow_worker._browser_session_lost(None, _LivePage()) is True
    assert flow_worker._browser_session_lost(_LiveContext(), Unreadable()) is True


def test_a_lost_browser_is_closed_and_forgotten_but_a_live_one_is_kept():
    page = _LivePage()
    context = _LiveContext(page)
    assert flow_worker._discard_lost_browser(context, page) == (context, page)
    assert context.close_calls == 0

    page.closed = True
    assert flow_worker._discard_lost_browser(context, page) == (None, None)
    assert context.close_calls == 1

    # Closing what is left of a crashed browser may itself fail.
    broken = _LiveContext(page, close_error=RuntimeError("browser has been closed"))
    assert flow_worker._discard_lost_browser(broken, page) == (None, None)
    assert flow_worker._discard_lost_browser(None, page) == (None, page)


def _run_two_portal_tasks(tmp_path, monkeypatch, *, launch, after_first_task):
    """Drive the real worker loop over two download tasks with fakes around it."""
    from contextlib import nullcontext
    from types import SimpleNamespace
    from app import flow_parallel_worker

    clock = [0]
    seen, reports = [], []
    job = {"execution": {"browser_mode": "headless", "browser_channel": "chrome"}}
    pending = [{"id": 1, "job": job, "lease_token": "a" * 32},
               {"id": 2, "job": job, "lease_token": "b" * 32}]
    monkeypatch.setattr(flow_worker, "sync_playwright", lambda: nullcontext(
        SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))))
    monkeypatch.setattr(flow_worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(flow_worker.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def api(client, method, path, body=None):
        assert clock[0] <= 120, "Worker failed to exit after the idle timeout"
        if path.endswith("/register"):
            return {}
        if path.endswith("/claim"):
            return {"task": pending.pop(0)} if pending else {}
        reports.append((path, body))
        return {}

    def execute(client, identity, task, page, profile, staging, *, headed):
        seen.append((task["id"], page, page.is_closed()))
        if task["id"] == 1:
            after_first_task(page)

    monkeypatch.setattr(flow_worker, "_api", api)
    monkeypatch.setattr(flow_parallel_worker, "execute_task", execute)
    flow_worker.run_worker("http://127.0.0.1:1", "bi-desktop-headless", "Headless", tmp_path / "profile",
                           False, False, idle_exit_seconds=30)
    return seen, reports


def test_worker_relaunches_the_browser_after_its_page_died(tmp_path, monkeypatch):
    launched = []

    def launch(_profile, **_options):
        launched.append(_LiveContext())
        return launched[-1]

    def window_closed(page):
        page.closed = True

    seen, reports = _run_two_portal_tasks(tmp_path, monkeypatch, launch=launch, after_first_task=window_closed)

    # The second task gets a fresh, open page from a second launch.
    assert [(task_id, closed) for task_id, _page, closed in seen] == [(1, False), (2, False)]
    assert seen[0][1] is launched[0].pages[0] and seen[1][1] is launched[1].pages[0]
    assert [context.close_calls for context in launched] == [1, 1]
    assert reports == []


def test_worker_keeps_one_browser_while_its_page_stays_usable(tmp_path, monkeypatch):
    launched = []

    def launch(_profile, **_options):
        launched.append(_LiveContext())
        return launched[-1]

    seen, _reports = _run_two_portal_tasks(tmp_path, monkeypatch, launch=launch, after_first_task=lambda _page: None)

    assert len(launched) == 1 and seen[0][1] is seen[1][1]
    assert launched[0].close_calls == 1  # only the worker's own shutdown


def test_a_failed_relaunch_fails_only_that_work_with_a_clear_message(tmp_path, monkeypatch):
    launched = []

    def launch(_profile, **_options):
        if launched:
            raise RuntimeError("Chromium distribution 'chrome' is not found")
        launched.append(_LiveContext())
        return launched[-1]

    def crashed(page):
        page.probe_error = RuntimeError("Page.wait_for_function: Target crashed ")

    seen, reports = _run_two_portal_tasks(tmp_path, monkeypatch, launch=launch, after_first_task=crashed)

    assert [task_id for task_id, _page, _closed in seen] == [1]
    (path, body), = reports
    assert path.endswith("/tasks/2/progress") and body["status"] == "failed"
    assert body["lease_token"] == "b" * 32 and body["progress"]["stage"] == "browser_launch"
    assert body["progress"]["message"].startswith("Could not open Google Chrome.")
    assert launched[0].close_calls == 1


# --- F-07: a lost progress report is not a failed step ----------------------

class _Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture()
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(flow_worker.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(flow_worker.time, "sleep", fake.sleep)
    return fake


class _OutageClient:
    """The local API, unreachable (or answering ``status``) until ``down_until``."""

    def __init__(self, clock, down_until, status=None):
        self.clock, self.down_until, self.status, self.calls = clock, down_until, status, 0

    def request(self, method, path, json=None, timeout=None):
        self.calls += 1
        request = httpx.Request(method, "http://127.0.0.1:8000" + path)
        if self.clock.now < self.down_until:
            if self.status is None:
                raise httpx.ConnectError("[WinError 10061] No connection could be made", request=request)
            return httpx.Response(self.status, request=request, json={"detail": "database is locked"})
        return httpx.Response(200, request=request, json={"accepted": True})


@pytest.mark.parametrize("status", [None, 503])
def test_run_reports_outlast_a_service_restart(clock, status):
    client = _OutageClient(clock, down_until=100, status=status)

    assert flow_worker._api_report(client, "POST", "/progress", {"status": "running"}) == {"accepted": True}

    # The plain ``_api`` budget is spent after ten seconds of this outage.
    assert clock.now >= 100
    assert clock.sleeps[:10] == list(range(1, 11)) and max(clock.sleeps) == 10


def test_run_reports_give_up_after_about_two_minutes(clock):
    client = _OutageClient(clock, down_until=float("inf"))

    with pytest.raises(httpx.ConnectError):
        flow_worker._api_report(client, "POST", "/heartbeat")

    assert clock.now == flow_worker.RUN_REPORT_RETRY_SECONDS == 120
    assert client.calls == len(clock.sleeps) + 1


def test_run_reports_do_not_retry_a_rejection(clock):
    client = _OutageClient(clock, down_until=float("inf"), status=422)

    with pytest.raises(RuntimeError, match="database is locked"):
        flow_worker._api_report(client, "POST", "/progress", {"status": "running"})

    assert client.calls == 1 and clock.sleeps == []


def test_other_worker_calls_keep_their_short_retry_budget(clock):
    client = _OutageClient(clock, down_until=float("inf"))

    with pytest.raises(httpx.ConnectError):
        flow_worker._api(client, "POST", "/api/flows/worker/w/claim")

    assert client.calls == 5 and clock.sleeps == [1, 2, 3, 4]


def _committing_engine(log):
    """A fake PostgreSQL engine for ``load_artifacts`` that records commit/rollback."""
    from types import SimpleNamespace

    class Result:
        def fetchall(self):
            return [("value", "NO", None, "NO", "NEVER")]

    class Cursor:
        def copy_expert(self, _statement, _stream):
            return None

        def close(self):
            return None

    transaction = SimpleNamespace(commit=lambda: log.append("commit"), rollback=lambda: log.append("rollback"))

    class Connection:
        connection = SimpleNamespace(cursor=lambda: Cursor())

        def begin(self):
            return transaction

        def execute(self, statement, *_args, **_kwargs):
            return None if str(statement).startswith("SET LOCAL") else Result()

        def close(self):
            log.append("close")

    return lambda _database: SimpleNamespace(connect=lambda: Connection(), dispose=lambda: None)


SQL_TARGET = {"database": "db", "schema": "reporting", "table": "target", "mode": "append"}


def test_sql_load_stays_successful_when_the_committed_report_cannot_be_sent(tmp_path, monkeypatch, capsys):
    from app import flow_sql

    path = tmp_path / "rows.csv"
    path.write_text("value\n1\n2\n", encoding="utf-8")
    log, events = [], []
    monkeypatch.setattr(flow_sql, "_engine", _committing_engine(log))

    def progress(detail):
        events.append((detail["stage"], detail["status"]))
        if (detail["stage"], detail["status"]) == ("sql_commit", "completed"):
            raise httpx.ConnectError("the server went away")

    result = flow_sql.load_artifacts([{"file_path": str(path)}], SQL_TARGET, progress=progress)

    assert result["rows_written"] == 2 and result["target"] == "db.reporting.target"
    assert log == ["commit", "close"]
    assert events[-1] == ("sql_commit", "completed") and ("sql_failed", "failed") not in events
    assert "SQL commit to db.reporting.target succeeded, but reporting it failed: ConnectError" in capsys.readouterr().err


def test_sql_load_still_rolls_back_when_a_report_fails_before_the_commit(tmp_path, monkeypatch):
    from app import flow_sql

    path = tmp_path / "rows.csv"
    path.write_text("value\n1\n", encoding="utf-8")
    log = []
    monkeypatch.setattr(flow_sql, "_engine", _committing_engine(log))

    def progress(detail):
        if (detail["stage"], detail["status"]) == ("sql_commit", "started"):
            raise httpx.ConnectError("the server went away")

    with pytest.raises(flow_sql.SqlHandoffError, match="PostgreSQL confirmed rollback"):
        flow_sql.load_artifacts([{"file_path": str(path)}], SQL_TARGET, progress=progress)

    assert log == ["rollback", "close"]


def _rejection(status_code):
    """What ``_api`` raises when the server answers a report with an error."""
    request = httpx.Request("POST", "http://127.0.0.1:8000/progress")
    response = httpx.Response(status_code, request=request, json={"detail": "no"})
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        error = RuntimeError(f"{exc} Server detail: no")
        error.__cause__ = exc
        return error


def test_only_transport_failures_and_5xx_count_as_lost_in_transit():
    assert flow_worker._report_lost_in_transit(httpx.ConnectError("refused"))
    assert flow_worker._report_lost_in_transit(httpx.ReadTimeout("slow"))
    assert flow_worker._report_lost_in_transit(_rejection(503))
    assert flow_worker._report_lost_in_transit(_rejection(503).__cause__)
    assert not flow_worker._report_lost_in_transit(_rejection(409))
    assert not flow_worker._report_lost_in_transit(_rejection(422).__cause__)
    assert not flow_worker._report_lost_in_transit(RuntimeError("disk full"))
    assert not flow_worker._report_lost_in_transit(OSError("disk full"))


def test_after_a_commit_an_undeliverable_intermediate_report_is_dropped(clock, capsys):
    sent = []

    def progress(status, detail, *args, **kwargs):
        sent.append(detail["stage"])
        raise httpx.ConnectError("refused")

    report = flow_worker._progress_after_commit(progress)

    assert report("running", {"stage": "sql_insertion_complete"}, [], []) is None
    assert sent == ["sql_insertion_complete"] and clock.sleeps == []
    assert "'running' report could not be delivered: ConnectError" in capsys.readouterr().err


def test_after_a_commit_the_terminal_report_is_retried_until_the_server_returns(clock):
    delivered = []

    def progress(status, detail, artifacts=None, timings=None, **extra):
        if clock.now < 200:
            raise _rejection(503)
        delivered.append((status, detail, artifacts, extra))

    report = flow_worker._progress_after_commit(progress)
    report("succeeded", {"stage": "complete"}, ["a.csv"], source_receipt=None)

    assert delivered == [("succeeded", {"stage": "complete"}, ["a.csv"], {"source_receipt": None})]
    assert 200 <= clock.now < flow_worker.COMMITTED_REPORT_RETRY_SECONDS


def test_after_a_commit_the_terminal_report_gives_up_without_raising(clock, capsys):
    attempts = []

    def progress(status, detail, *args, **kwargs):
        attempts.append(clock.now)
        raise httpx.ConnectError("refused")

    report = flow_worker._progress_after_commit(progress)

    assert report("succeeded", {"stage": "complete"}) is None
    assert clock.now == flow_worker.COMMITTED_REPORT_RETRY_SECONDS == 600
    assert len(attempts) > 10
    assert "SQL is committed, but the 'succeeded' report could not be delivered" in capsys.readouterr().err


@pytest.mark.parametrize("error", [_rejection(409), RuntimeError("disk full"), KeyError("stage")])
def test_after_a_commit_a_rejected_or_broken_report_is_still_raised(clock, error):
    def progress(*_args, **_kwargs):
        raise error

    with pytest.raises(type(error)):
        flow_worker._progress_after_commit(progress)("succeeded", {"stage": "complete"})
    assert clock.sleeps == []


def _sql_retry_job(tmp_path):
    saved = tmp_path / "saved.csv"
    saved.write_text("value\n1\n", encoding="utf-8")
    return {
        "job_type": "sql_retry",
        "flow": {"id": 7, "name": "Committed", "source_type": "portal"},
        "sql_handoff": {"enabled": True, **SQL_TARGET},
        "sql_retry": {"source_run_id": 3, "artifacts": [
            {"file_path": str(saved), "filename": saved.name, "row_count": 1, "status": "saved"},
        ]},
    }


def _run_committing_flow(tmp_path, monkeypatch, progress):
    from app import flow_sql

    loads = []

    def load_artifacts(artifacts, target, progress=None):
        loads.append(target["table"])
        return {"rows_written": 1, "files_loaded": len(artifacts), "target": "db.reporting.target"}

    monkeypatch.setattr(flow_sql, "load_artifacts", load_artifacts)
    state = flow_worker.execute_flow(
        None, _sql_retry_job(tmp_path), progress, tmp_path / "profile",
        run_id=11, register_folder=lambda _folder: {"ops": []},
    )
    return state, loads


def test_a_committed_run_is_not_failed_by_reports_lost_after_the_commit(tmp_path, monkeypatch, clock):
    delivered = []

    def progress(status, detail, *args, **kwargs):
        if detail["stage"] == "sql_insertion_complete" or (status == "succeeded" and clock.now < 45):
            raise httpx.ConnectError("refused")
        delivered.append((status, detail["stage"]))

    state, loads = _run_committing_flow(tmp_path, monkeypatch, progress)

    assert loads == ["target"] and state["sql_result"]["rows_written"] == 1
    assert delivered == [("running", "sql_retry"), ("running", "sql_insertion"), ("succeeded", "complete")]


def test_a_committed_run_ends_quietly_when_the_server_never_returns(tmp_path, monkeypatch, clock):
    statuses = []

    def progress(status, detail, *args, **kwargs):
        statuses.append(status)
        if detail["stage"] in {"sql_insertion_complete", "complete"}:
            raise httpx.ConnectError("refused")

    state, loads = _run_committing_flow(tmp_path, monkeypatch, progress)

    # No exception reaches the worker loop, so it never posts ``failed``.
    assert loads == ["target"] and state["sql_result"]["rows_written"] == 1
    assert "failed" not in statuses and clock.now == flow_worker.COMMITTED_REPORT_RETRY_SECONDS


def test_a_report_lost_before_the_commit_still_fails_the_run(tmp_path, monkeypatch, clock):
    def progress(status, detail, *args, **kwargs):
        if detail["stage"] == "sql_insertion":
            raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        _run_committing_flow(tmp_path, monkeypatch, progress)


# --- F-09: a catalog scan cannot wait on a stuck overlay forever ------------

class _OverlayPage:
    """ASAP page double whose waits advance the fake clock; it closes eventually."""

    def __init__(self, clock, closes_at):
        self.clock, self.closes_at = clock, closes_at

    def wait_for_timeout(self, milliseconds):
        self.clock.now += milliseconds / 1_000

    def is_closed(self):
        return self.clock.now >= self.closes_at


def test_catalog_scan_fails_a_report_whose_overlay_never_clears(monkeypatch, tmp_path, clock):
    monkeypatch.setattr(flow_worker, "_asap_goto", lambda *_args: False)
    monkeypatch.setattr(flow_worker, "_asap_open_report", lambda *_args: object())
    monkeypatch.setattr(flow_worker, "_asap_export_view_candidates", lambda *_args: [])
    monkeypatch.setattr(flow_worker, "_asap_loading_overlay_visible", lambda _page: True)
    job = {
        "site": {"base_url": "https://portal.example.internal", "auth_url": None},
        "discovery": {
            "max_duration_minutes": 90, "scope": ["*"],
            "report_paths": [["Market", "Group", "Target Report"]],
        },
    }

    with pytest.raises(
        RuntimeError, match="Target Report: ASAP loading overlay did not clear within 1800 seconds",
    ):
        flow_worker.discover_asap_catalog(
            _OverlayPage(clock, closes_at=6 * 3600), job, lambda *_args, **_kwargs: None, tmp_path,
        )

    assert flow_worker.ASAP_SCAN_OVERLAY_TIMEOUT_SECONDS <= clock.now < flow_worker.ASAP_SCAN_OVERLAY_TIMEOUT_SECONDS + 5


def test_export_view_activation_also_bounds_the_wait_after_its_click(monkeypatch, clock):
    from types import SimpleNamespace

    clicked = []
    monkeypatch.setattr(flow_worker, "_asap_loading_overlay_visible", lambda _page: bool(clicked))
    monkeypatch.setattr(flow_worker, "_asap_frame", lambda _page: object())
    control = SimpleNamespace(click=lambda **_kwargs: clicked.append(True))
    monkeypatch.setattr(
        flow_worker, "_asap_export_view_candidates", lambda _page, _frame: [("Export Wizard", control)],
    )

    with pytest.raises(RuntimeError, match="ASAP loading overlay did not clear within 1800 seconds"):
        flow_worker._asap_activate_export_view(
            _OverlayPage(clock, closes_at=6 * 3600), object(), "Export Wizard",
            max_seconds=flow_worker.ASAP_SCAN_OVERLAY_TIMEOUT_SECONDS,
        )

    assert clicked == [True]


def test_a_flow_run_still_waits_for_the_overlay_while_the_page_is_open(monkeypatch, clock):
    monkeypatch.setattr(flow_worker, "_asap_loading_overlay_visible", lambda _page: True)

    with pytest.raises(
        RuntimeError,
        match="ASAP page closed while waiting for the ASAP loading overlay to clear after 180 minutes",
    ):
        flow_worker._asap_activate_export_view(_OverlayPage(clock, closes_at=3 * 3600), object(), "Export Wizard")


# --- F-11: a waiting coordinator does not post the same progress every second

def _coordinate(monkeypatch, tmp_path, clock, state_at):
    """Run the coordinator loop against a scripted server; return its posts."""
    from app import flow_parallel_worker

    posts = []

    def api(client, method, path, body=None):
        if path.endswith("/tasks/claim"):
            return {}  # every export is being downloaded by a helper worker
        if path.endswith("/finalizer"):
            return {**state_at(clock.now), "finalizer_token": "f" * 32}
        assert path.endswith("/tasks")
        return state_at(clock.now)

    monkeypatch.setattr(flow_worker, "_api", api)
    monkeypatch.setattr(flow_worker, "_prepare_run_folder", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(flow_worker, "_job_store_id", lambda *_args: "store")
    monkeypatch.setattr(flow_worker, "_runtime_store_ids", lambda *_args: ["store"])
    monkeypatch.setattr(flow_parallel_worker, "_valid_carried", lambda _job: {})
    monkeypatch.setattr(flow_parallel_worker, "assemble_bundle", lambda job, state, profile: list(state["artifacts"]))

    def progress(status, detail, artifacts=None):
        posts.append((clock.now, status, dict(detail), list(artifacts or [])))

    artifacts, _timings = flow_parallel_worker.acquire_bundle(
        object(), "bi-desktop-headless", {}, None, {}, progress, tmp_path, None,
        artifacts=[], run_id=5, register_folder=lambda _folder: {},
    )
    return posts, artifacts


def test_waiting_coordinator_posts_changes_at_once_and_otherwise_a_keepalive(monkeypatch, tmp_path, clock):
    from app import flow_parallel_worker
    from app.routers import flows

    def state_at(now):
        completed = 0 if now < 100 else 1 if now < 130 else 2
        return {
            "state": "downloading", "error": None, "completed": completed, "total": 2,
            "active": 2 - completed, "artifacts": [{"filename": f"export-{index}.csv"} for index in range(completed)],
        }

    posts, artifacts = _coordinate(monkeypatch, tmp_path, clock, state_at)

    # 130 one-second polls used to store 131 identical-or-not run events.
    assert [at for at, *_ in posts] == [0, 30, 60, 90, 100, 130]
    assert [detail["completed"] for _at, _status, detail, _artifacts in posts] == [0, 0, 0, 0, 1, 2]
    assert all(status == "running" and detail["stage"] == "parallel_downloads" for _at, status, detail, _ in posts)
    assert posts[-1][2]["message"] == "Downloaded 2 of 2 exports; 0 active slots."
    assert [len(items) for *_, items in posts] == [0, 0, 0, 0, 1, 2] and len(artifacts) == 2
    # A quiet run still shows as alive well inside the stale-run window.
    gaps = [later[0] - earlier[0] for earlier, later in zip(posts, posts[1:])]
    assert max(gaps) <= flow_parallel_worker.PARALLEL_PROGRESS_KEEPALIVE_SECONDS == 30
    assert flow_parallel_worker.PARALLEL_PROGRESS_KEEPALIVE_SECONDS * 10 <= flows.RUN_STALE_TIMEOUT_SECONDS


def test_coordinator_posts_a_changed_artifact_list_even_when_the_counts_match(monkeypatch, tmp_path, clock):
    def state_at(now):
        name = "first.csv" if now < 5 else "replaced.csv"
        return {
            "state": "downloading", "error": None, "completed": 1 if now < 8 else 2, "total": 2,
            "active": 1, "artifacts": [{"filename": name}],
        }

    posts, _artifacts = _coordinate(monkeypatch, tmp_path, clock, state_at)

    assert [(at, items[0]["filename"]) for at, _status, _detail, items in posts] == [
        (0, "first.csv"), (5, "replaced.csv"), (8, "replaced.csv"),
    ]


# --- F-22: the sign-in bootstrap names no portal host in source -------------

@pytest.fixture()
def site_db(tmp_path, monkeypatch):
    from app import database, settings

    db_path = tmp_path / "sites.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "DB_PATH", str(db_path))
    database.init_db()
    with database.get_db() as db:
        db.executemany(
            "INSERT INTO flow_sites(name, adapter, base_url, auth_url) VALUES (?, ?, ?, ?)",
            [
                ("Audit ASAP", "asap_portal", "https://asap.example.internal", "https://asap.example.internal/login"),
                ("Audit GSCM", "gscm_portal", None, "https://gscm.example.internal/nexa/index.html"),
                ("Audit GSCM mirror", "gscm_portal", "https://mirror.example.internal:8443/", None),
                ("Audit other", "web_export", "https://other.example.internal", None),
            ],
        )
    return db_path


@pytest.mark.parametrize("url, adapter", [
    ("https://gscm.example.internal/nexa/index.html", "gscm_portal"),
    ("https://GSCM.Example.Internal:443/another/page?x=1", "gscm_portal"),
    ("https://mirror.example.internal/", "gscm_portal"),
    ("https://asap.example.internal/login", "asap_portal"),
    ("https://other.example.internal/", "asap_portal"),
    # A host no saved website uses keeps the original ASAP bootstrap.
    ("https://unknown.example.internal/nexa/index.html", "asap_portal"),
    ("https://notgscm.example.internal.example.org/", "asap_portal"),
    ("not a url", "asap_portal"),
    ("", "asap_portal"),
])
def test_bootstrap_adapter_comes_from_the_saved_website_with_the_same_host(site_db, url, adapter):
    assert flow_worker._adapter_for_auth_url(url) == adapter


def test_bootstrap_adapter_never_creates_a_missing_database(tmp_path, monkeypatch):
    from app import database

    missing = tmp_path / "elsewhere" / "governance.db"
    monkeypatch.setattr(database, "DB_PATH", str(missing))

    assert flow_worker._adapter_for_auth_url("https://gscm.example.internal/") == "asap_portal"
    assert not missing.exists() and not missing.parent.exists()


def test_bootstrap_adapter_inference_names_no_host_in_source():
    import inspect
    import re

    source = inspect.getsource(flow_worker._adapter_for_auth_url)
    # A quoted dotted name such as "portal.example.internal" would be a host.
    assert not re.search(r"""["'][\w-]+(?:\.[\w-]+)+["']""", source)
