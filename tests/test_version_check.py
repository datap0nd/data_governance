"""The in-app version badge: deployed commit parsing and the update check.

setup.ps1 stamps VERSION as "<timestamp>-<commit sha>"; /api/version compares
that commit against GitHub main so the UI can say "up to date" or "update
available" instead of showing an opaque timestamp.
"""

from app import main


def _reset_cache():
    main._UPDATE_CHECK.update({"checked_at": 0.0, "latest_commit": None, "error": None})


def test_deployed_commit_is_parsed_from_a_stamped_version(monkeypatch):
    monkeypatch.setattr(main, "_APP_VERSION", "20260826-153000-f981961ab")
    assert main._deployed_commit() == "f981961ab"


def test_a_timestamp_only_version_has_no_commit(monkeypatch):
    # The pre-stamp format: "<date>-<time>". "153000" is hex-shaped but pure
    # digits, so it must not be mistaken for a commit.
    monkeypatch.setattr(main, "_APP_VERSION", "20260826-153000")
    assert main._deployed_commit() is None


def test_version_endpoint_reports_up_to_date(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(main, "_APP_VERSION", "20260826-153000-f981961ab")
    monkeypatch.setattr(
        main, "_fetch_latest_commit",
        lambda: "f981961ab00000000000000000000000000000000"[:40],
    )
    result = main.get_version()
    assert result["up_to_date"] is True
    assert result["commit"] == "f981961ab"


def test_version_endpoint_reports_update_available(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(main, "_APP_VERSION", "20260826-153000-f981961ab")
    monkeypatch.setattr(main, "_fetch_latest_commit", lambda: "abcdef0123456789")
    result = main.get_version()
    assert result["up_to_date"] is False
    assert result["latest_commit"] == "abcdef0123456789"


def test_version_endpoint_degrades_when_github_is_unreachable(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(main, "_APP_VERSION", "20260826-153000-f981961ab")

    def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(main, "_fetch_latest_commit", boom)
    result = main.get_version()
    assert result["up_to_date"] is None
    assert "offline" in result["update_check_error"]
    assert result["version"] == "20260826-153000-f981961ab"


def test_the_latest_commit_lookup_is_cached(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(main, "_APP_VERSION", "20260826-153000-f981961ab")
    calls = []

    def fetch():
        calls.append(1)
        return "f981961ab0000"

    monkeypatch.setattr(main, "_fetch_latest_commit", fetch)
    main.get_version()
    main.get_version()
    assert len(calls) == 1
