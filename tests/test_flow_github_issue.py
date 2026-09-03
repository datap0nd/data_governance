from app import flow_github_issue as github_issue


def _snapshot(run_id=42):
    return {
        "run_id": run_id,
        "status": "failed",
        "flow_name": "GSCM Sales",
        "site_name": "GSCM",
        "report_name": "MENA_Sales_Hier",
        "trigger_type": "manual",
        "started_at": "2026-09-03T14:00:00",
        "finished_at": "2026-09-03T14:01:00",
        "failure_stage": "export",
        "files_saved": 0,
        "error": (
            r"token=top-secret user@example.com at "
            r"C:\Users\Analyst\diagnostics\failure.png"
        ),
        "events": [{
            "status": "failed",
            "stage": "export",
            "message": r"password=hunter2 C:\private\run.log",
            "error": "Authorization: Bearer abc.def.ghi",
            "traceback": "trace secret=hidden-value",
            "created_at": "2026-09-03T14:01:00",
        }],
    }


def test_issue_body_is_bounded_and_redacts_external_diagnostics():
    snapshot = _snapshot()
    snapshot["events"] *= 30

    body = github_issue.build_issue_body(snapshot)

    assert len(body) <= github_issue.MAX_ISSUE_BODY_CHARS
    assert "metronome-flow-run-id:42" in body
    assert "MENA_Sales_Hier" in body
    assert "top-secret" not in body
    assert "hunter2" not in body
    assert "abc.def.ghi" not in body
    assert "hidden-value" not in body
    assert "user@example.com" not in body
    assert r"C:\Users\Analyst" not in body
    assert "[redacted]" in body
    assert "[local path]" in body
    assert "[email redacted]" in body


def test_publish_creates_the_single_failure_issue(monkeypatch):
    monkeypatch.setenv("DG_GITHUB_TOKEN", "test-token")
    calls = []

    def request(method, url, payload=None):
        calls.append((method, url, payload))
        if method == "GET":
            return []
        return {"html_url": "https://github.example/issues/9"}

    monkeypatch.setattr(github_issue, "_request", request)

    result = github_issue.publish_failure_issue(_snapshot())

    assert result == {
        "status": "created",
        "run_id": 42,
        "issue_url": "https://github.example/issues/9",
    }
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert calls[1][2]["title"] == github_issue.ISSUE_TITLE
    assert "metronome-flow-run-id:42" in calls[1][2]["body"]


def test_publish_updates_existing_issue_and_reopens_it(monkeypatch):
    monkeypatch.setenv("DG_GITHUB_TOKEN", "test-token")
    calls = []

    def request(method, url, payload=None):
        calls.append((method, url, payload))
        if method == "GET":
            return [{
                "number": 9,
                "title": github_issue.ISSUE_TITLE,
                "body": "<!-- metronome-flow-run-id:41 -->",
                "html_url": "https://github.example/issues/9",
            }]
        return {"html_url": "https://github.example/issues/9"}

    monkeypatch.setattr(github_issue, "_request", request)

    result = github_issue.publish_failure_issue(_snapshot())

    assert result["status"] == "updated"
    assert [call[0] for call in calls] == ["GET", "PATCH"]
    assert calls[1][1].endswith("/issues/9")
    assert calls[1][2]["state"] == "open"


def test_older_failure_cannot_overwrite_a_newer_issue(monkeypatch):
    monkeypatch.setenv("DG_GITHUB_TOKEN", "test-token")
    calls = []

    def request(method, url, payload=None):
        calls.append((method, url, payload))
        return [{
            "number": 9,
            "title": github_issue.ISSUE_TITLE,
            "body": "<!-- metronome-flow-run-id:99 -->",
            "html_url": "https://github.example/issues/9",
        }]

    monkeypatch.setattr(github_issue, "_request", request)

    result = github_issue.publish_failure_issue(_snapshot(42))

    assert result["status"] == "superseded"
    assert [call[0] for call in calls] == ["GET"]


def test_scheduler_is_disabled_without_a_pat(monkeypatch):
    monkeypatch.delenv("DG_GITHUB_TOKEN", raising=False)

    assert github_issue.schedule_failure_issue(_snapshot()) == {
        "status": "disabled",
        "reason": "DG_GITHUB_TOKEN is not configured.",
    }


def test_scheduler_failure_does_not_escape_to_the_flow(monkeypatch):
    monkeypatch.setenv("DG_GITHUB_TOKEN", "test-token")

    class BrokenThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(github_issue.threading, "Thread", BrokenThread)

    assert github_issue.schedule_failure_issue(_snapshot()) == {
        "status": "failed",
        "run_id": 42,
        "reason": "Could not start publisher.",
    }
