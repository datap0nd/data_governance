"""A portal sign-in the portal refused is not submitted again.

Reported by the owner: after a password change, every browser profile signed
in with the old password on its own, for ASAP and for GSCM, and nothing
remembered the refusal. That is a row of wrong attempts against a person's own
company account, enough to lock it.

Everything here is synthetic: fictional people and passwords, stand-in pages
instead of a browser, a temporary folder instead of the Windows profile.
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app import flow_credentials, flow_sign_in_guard, flow_worker

OLD_PASSWORD = "Winter-2025-old"
NEW_PASSWORD = "Spring-2026-new"


def _sign_in(password=OLD_PASSWORD):
    return {"username": "maya.k", "password": password}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """The folder every worker of one Windows account shares."""
    folder = tmp_path / "flow-browser"
    monkeypatch.setenv("METRONOME_FLOW_PROFILE", str(folder))
    monkeypatch.delenv("DG_PORTAL_USER", raising=False)
    monkeypatch.delenv("DG_PORTAL_PASSWORD", raising=False)
    return folder


# --- The guard itself ------------------------------------------------------------

def test_a_refused_sign_in_is_never_offered_a_second_submission(profile):
    with flow_sign_in_guard.attempt(_sign_in(), "ASAP") as attempt:
        attempt.submitted()
        attempt.refused()

    with pytest.raises(flow_sign_in_guard.SignInPaused) as paused:
        with flow_sign_in_guard.attempt(_sign_in(), "GSCM"):
            pytest.fail("the form must not be reached")
    message = str(paused.value)
    assert "ASAP refused" in message and "lock the account" in message
    assert "System > Settings > Connections > Portal sign-in" in message
    assert OLD_PASSWORD not in message and "maya.k" not in message


def test_two_submissions_that_get_nowhere_pause_it_and_one_does_not(profile):
    with flow_sign_in_guard.attempt(_sign_in(), "GSCM") as attempt:
        attempt.submitted()
        attempt.failed()
    assert flow_sign_in_guard.status(_sign_in())["paused"] is False

    # Left without saying what happened, after the form went out: still a failure.
    with pytest.raises(RuntimeError, match="browser closed"):
        with flow_sign_in_guard.attempt(_sign_in(), "GSCM") as attempt:
            attempt.submitted()
            raise RuntimeError("browser closed")
    state = flow_sign_in_guard.status(_sign_in())
    assert state["paused"] is True and state["paused_refused"] is False and state["failed_attempts"] == 2
    with pytest.raises(flow_sign_in_guard.SignInPaused, match="submitted 2 times without GSCM opening"):
        flow_sign_in_guard.raise_if_paused(_sign_in())


def test_a_form_that_was_never_sent_does_not_count(profile):
    with pytest.raises(RuntimeError):
        with flow_sign_in_guard.attempt(_sign_in(), "ASAP"):
            raise RuntimeError("the Login button was not found")
    assert flow_sign_in_guard.status(_sign_in())["failed_attempts"] == 0


def test_a_sign_in_that_works_clears_an_earlier_failure(profile):
    with flow_sign_in_guard.attempt(_sign_in(), "ASAP") as attempt:
        attempt.submitted()
        attempt.failed()
    with flow_sign_in_guard.attempt(_sign_in(), "ASAP") as attempt:
        attempt.submitted()
        attempt.succeeded()
    assert flow_sign_in_guard.status(_sign_in()) == {
        "paused": False, "paused_at": None, "paused_portal": None, "paused_refused": False, "failed_attempts": 0}


def test_a_new_password_starts_clean_and_try_again_lifts_the_pause_for_the_same_one(profile):
    with flow_sign_in_guard.attempt(_sign_in(), "ASAP") as attempt:
        attempt.submitted()
        attempt.refused()

    # A different password is a different sign-in: it gets its own first try.
    with flow_sign_in_guard.attempt(_sign_in(NEW_PASSWORD), "ASAP") as attempt:
        attempt.submitted()
        attempt.succeeded()
    assert flow_sign_in_guard.status(_sign_in(NEW_PASSWORD))["paused"] is False

    # The same password again stays paused until a person says to try it again.
    with flow_sign_in_guard.attempt(_sign_in(), "ASAP") as attempt:
        attempt.submitted()
        attempt.refused()
    assert flow_sign_in_guard.status(_sign_in())["paused"] is True
    flow_sign_in_guard.resume()
    assert flow_sign_in_guard.status(_sign_in())["paused"] is False
    flow_sign_in_guard.raise_if_paused(_sign_in())


def test_the_record_holds_neither_the_user_id_nor_the_password(profile):
    with flow_sign_in_guard.attempt(_sign_in(), "ASAP") as attempt:
        attempt.submitted()
        attempt.refused()
    record = (profile / flow_sign_in_guard.STATE_FILENAME).read_text(encoding="utf-8")
    assert OLD_PASSWORD not in record and "maya.k" not in record
    assert [item["refused"] for item in json.loads(record)["failures"]] == [True]


def test_workers_submit_one_at_a_time_and_the_second_sees_the_first_refusal(profile):
    # Twelve workers find their sessions expired at the same moment. Without
    # the lock each would submit the old password before any refusal is known.
    first_is_inside, let_first_finish = threading.Event(), threading.Event()
    second = {}

    def first_worker():
        with flow_sign_in_guard.attempt(_sign_in(), "ASAP") as attempt:
            attempt.submitted()
            first_is_inside.set()
            let_first_finish.wait(10)
            attempt.refused()

    def second_worker():
        first_is_inside.wait(10)
        try:
            with flow_sign_in_guard.attempt(_sign_in(), "ASAP"):
                second["submitted"] = True
        except flow_sign_in_guard.SignInPaused:
            second["paused"] = True

    threads = [threading.Thread(target=first_worker), threading.Thread(target=second_worker)]
    for thread in threads:
        thread.start()
    first_is_inside.wait(10)
    threads[1].join(0.6)
    assert threads[1].is_alive() and second == {}  # waiting for the first, nothing sent
    let_first_finish.set()
    for thread in threads:
        thread.join(10)
    assert second == {"paused": True}


_STOPPED_WHILE_WAITING = """
import os, sys
sys.path.insert(0, sys.argv[1])
from app import flow_sign_in_guard
sign_in = {"username": "maya.k", "password": "Winter-2025-old"}
try:
    with flow_sign_in_guard.attempt(sign_in, "ASAP") as attempt:
        attempt.submitted()
        os._exit(7)   # the service is restarted, or the PC loses power, mid-wait
except flow_sign_in_guard.SignInPaused:
    os._exit(9)
"""


def test_a_worker_stopped_while_it_waits_for_the_portal_still_counts(profile):
    # Found in review: the outcome used to be written only when the block
    # ended. A worker killed after sending the password never got there, so
    # each restart sent the same password again and nothing was ever counted.
    root = str(Path(__file__).resolve().parents[1])
    environment = {**os.environ, "METRONOME_FLOW_PROFILE": str(profile), "DG_ENV_FILE": ""}

    def worker_is_killed_mid_sign_in():
        return subprocess.run([sys.executable, "-c", _STOPPED_WHILE_WAITING, root],
                              env=environment, capture_output=True, text=True, timeout=60).returncode

    assert worker_is_killed_mid_sign_in() == 7
    assert flow_sign_in_guard.status(_sign_in())["failed_attempts"] == 1
    assert worker_is_killed_mid_sign_in() == 7
    assert flow_sign_in_guard.status(_sign_in())["paused"] is True
    # The third worker submits nothing.
    assert worker_is_killed_mid_sign_in() == 9


def test_a_sign_in_still_waiting_for_the_portal_is_not_reported_as_failed(profile):
    with flow_sign_in_guard.attempt(_sign_in(), "ASAP") as attempt:
        attempt.submitted()
        attempt.failed()

    inside, finish = threading.Event(), threading.Event()

    def second_attempt():
        with flow_sign_in_guard.attempt(_sign_in(), "ASAP") as attempt:
            attempt.submitted()
            inside.set()
            finish.wait(10)
            attempt.succeeded()

    thread = threading.Thread(target=second_attempt)
    thread.start()
    inside.wait(10)
    during = flow_sign_in_guard.status(_sign_in())
    finish.set()
    thread.join(10)
    assert (during["paused"], during["failed_attempts"]) == (False, 1)
    assert flow_sign_in_guard.status(_sign_in())["failed_attempts"] == 0


# --- Which sign-in workers use ------------------------------------------------------

def test_the_settings_file_sign_in_is_what_workers_use(profile, monkeypatch):
    assert flow_credentials.load_asap_credentials() is None
    assert flow_credentials.asap_credential_status() == {"configured": False, "updated_at": None}

    monkeypatch.setenv("DG_PORTAL_USER", "  maya.k ")
    assert flow_credentials.load_asap_credentials() is None  # a user ID alone is not a sign-in
    monkeypatch.setenv("DG_PORTAL_PASSWORD", NEW_PASSWORD)

    used = flow_credentials.load_asap_credentials()
    assert (used["username"], used["password"], used["source"]) == ("maya.k", NEW_PASSWORD, "settings_file")
    assert flow_credentials.asap_credential_status()["configured"] is True
    # A tool that names the folder asks for the encrypted file there, and there is none.
    assert flow_credentials.load_asap_credentials(profile) is None
    assert flow_credentials.asap_credential_status(profile)["configured"] is False


# --- The worker's two sign-in paths -------------------------------------------------

class _Locator:
    def __init__(self, page, name):
        self.page, self.name = page, name

    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    def count(self):
        return int(bool(self.page.refusal)) if self.name == "refusal" else 1

    def is_visible(self):
        return True

    def fill(self, value):
        self.page.typed.append(value)

    def click(self):
        self.page.submissions += 1

    def text_content(self):
        return self.page.refusal


class _SignOnPage:
    """The company sign-on form as one browser profile sees it."""

    def __init__(self, refusal=""):
        self.refusal, self.typed, self.submissions = refusal, [], 0

    def locator(self, selector):
        if selector.startswith("text="):
            return _Locator(self, "refusal")
        return _Locator(self, "password" if "password" in selector else "submit" if "submit" in selector else "username")

    def get_by_role(self, _role, **_options):
        return _Locator(self, "submit")

    def wait_for_timeout(self, _milliseconds):
        pass


@pytest.fixture
def worker(profile, monkeypatch):
    saved = {"sign_in": _sign_in()}
    monkeypatch.setattr(flow_worker, "load_asap_credentials", lambda: dict(saved["sign_in"]))
    monkeypatch.setattr(flow_worker, "_REDACTED_SECRETS", set(), raising=False)
    monkeypatch.setattr(flow_worker, "_wait_for_navigation_roots", lambda page, timeout_ms=0: [])
    monkeypatch.setattr(flow_worker.flow_gscm, "portal_shell_rendered", lambda page: False)
    monkeypatch.setattr(flow_worker, "GSCM_AUTO_LOGIN_TIMEOUT_MS", 0)
    return saved


def test_an_old_password_is_typed_into_one_browser_and_no_other(worker, profile):
    first = _SignOnPage(refusal="Incorrect password. Try again.")
    with pytest.raises(RuntimeError, match="ASAP automatic sign-in failed: Incorrect password") as failed:
        flow_worker._asap_authenticate_if_needed(first, profile)
    assert not isinstance(failed.value, flow_sign_in_guard.SignInPaused)
    assert first.submissions == 1

    # Every other profile, and the other portal, finds the form and leaves it alone.
    for sign_in in (flow_worker._asap_authenticate_if_needed, flow_worker._gscm_authenticate_if_needed,
                    flow_worker._asap_authenticate_if_needed):
        page = _SignOnPage(refusal="Incorrect password. Try again.")
        with pytest.raises(flow_sign_in_guard.SignInPaused):
            sign_in(page, profile)
        assert page.submissions == 0 and page.typed == []


def test_gscm_stops_waiting_as_soon_as_the_page_says_no(worker, profile, monkeypatch):
    monkeypatch.setattr(flow_worker, "GSCM_AUTO_LOGIN_TIMEOUT_MS", 180_000)
    page = _SignOnPage(refusal="Incorrect user ID or password.")
    with pytest.raises(RuntimeError, match="GSCM automatic sign-in failed: Incorrect user ID or password"):
        flow_worker._gscm_authenticate_if_needed(page, profile)
    assert page.submissions == 1
    assert flow_sign_in_guard.status(_sign_in())["paused_refused"] is True


def test_a_portal_that_never_opens_is_tried_twice_then_left_alone(worker, profile):
    pages = [_SignOnPage(), _SignOnPage(), _SignOnPage()]
    with pytest.raises(RuntimeError, match="did not render"):
        flow_worker._gscm_authenticate_if_needed(pages[0], profile)
    with pytest.raises(RuntimeError, match="ASAP did not open"):
        flow_worker._asap_authenticate_if_needed(pages[1], profile)
    with pytest.raises(flow_sign_in_guard.SignInPaused):
        flow_worker._gscm_authenticate_if_needed(pages[2], profile)
    assert [page.submissions for page in pages] == [1, 1, 0]


def test_the_corrected_password_is_used_at_once_and_signs_in(worker, profile, monkeypatch):
    with pytest.raises(RuntimeError):
        flow_worker._asap_authenticate_if_needed(_SignOnPage(refusal="Incorrect password."), profile)

    worker["sign_in"] = _sign_in(NEW_PASSWORD)
    monkeypatch.setattr(flow_worker, "_wait_for_navigation_roots", lambda page, timeout_ms=0: [{"text": "Reports"}])
    page = _SignOnPage()
    assert flow_worker._asap_authenticate_if_needed(page, profile) is True
    assert page.typed == ["maya.k", NEW_PASSWORD] and page.submissions == 1
    assert flow_sign_in_guard.status(_sign_in(NEW_PASSWORD))["failed_attempts"] == 0


def test_setup_is_told_to_stop_before_a_browser_is_opened(worker, profile, monkeypatch):
    with pytest.raises(RuntimeError):
        flow_worker._asap_authenticate_if_needed(_SignOnPage(refusal="Incorrect password."), profile)

    def no_browser(*_arguments, **_options):
        pytest.fail("no browser may be started for a paused sign-in")

    monkeypatch.setattr(flow_worker, "sync_playwright", no_browser)
    with pytest.raises(flow_sign_in_guard.SignInPaused):
        flow_worker.authenticate_site(profile / "headed", "https://portal.example.test/asap", 1)
    assert flow_worker.SIGN_IN_PAUSED_EXIT_CODE == 3
