from __future__ import annotations

import json

from tools.ci.change_scope import classify, execution_policy, read_patterns
from tools.ci import browser_setup
from tools.ci.browser_setup import required_browsers
from tools.ci.check_docs import local_link_errors
from tools.ci.find_validation_baseline import has_full_windows, select_baseline, select_equivalent


def windows_patterns(tmp_path):
    manifest = tmp_path / "windows.txt"
    manifest.write_text("tools/*.ps1\napp/flow_worker.py\n", encoding="utf-8")
    return read_patterns(manifest)


def test_documentation_and_policy_only_use_the_lightweight_gate(tmp_path):
    scope = classify(["docs/testing/README.md", "AGENTS.md"], windows_patterns(tmp_path))
    assert scope.docs_only
    assert not scope.application
    assert not scope.windows


def test_ci_orchestration_cannot_claim_a_workflow_exemption(tmp_path):
    scope = classify(
        [".github/workflows/tests.yml"], windows_patterns(tmp_path), require_equivalence=True
    )
    assert scope.application and scope.orchestration and scope.equivalence and scope.windows


def test_frontend_change_keeps_full_ubuntu_without_adding_windows(tmp_path):
    scope = classify(["app/static/app.js"], windows_patterns(tmp_path))
    assert scope.application and scope.frontend
    assert not scope.windows


def test_explicit_and_unknown_backend_paths_default_to_windows(tmp_path):
    explicit = classify(["app/flow_worker.py"], windows_patterns(tmp_path))
    unknown = classify(["app/new_backend.py"], windows_patterns(tmp_path))
    assert explicit.windows and unknown.windows


def test_sql_and_dependency_changes_select_postgres(tmp_path):
    assert classify(["app/flow_sql.py"], windows_patterns(tmp_path)).postgres
    assert classify(["requirements-ci.lock"], windows_patterns(tmp_path)).postgres


def test_force_full_covers_scheduled_and_manual_runs(tmp_path):
    scope = classify([], windows_patterns(tmp_path), force_full=True)
    assert all((scope.application, scope.windows, scope.postgres, scope.frontend))
    assert not scope.orchestration and not scope.equivalence


def test_full_windows_detection_supports_legacy_and_six_shard_runs():
    assert has_full_windows([{"name": "Python (windows-latest)", "conclusion": "success"}])
    jobs = [
        {"name": f"Python windows {index}/6", "conclusion": "success"}
        for index in range(1, 7)
    ] + [{"name": "Test inventory reconciliation", "conclusion": "success"}]
    assert has_full_windows(jobs)
    jobs[0]["conclusion"] = "failure"
    assert not has_full_windows(jobs)


def test_push_baseline_uses_latest_successful_full_windows_ancestor():
    runs = [
        {"id": 3, "head_sha": "docs", "conclusion": "success"},
        {"id": 2, "head_sha": "app", "conclusion": "success"},
        {"id": 1, "head_sha": "old", "conclusion": "success"},
    ]
    jobs = {
        3: [{"name": "Merge ready", "conclusion": "success"}],
        2: [{"name": "Python (windows-latest)", "conclusion": "success"}],
        1: [{"name": "Python (windows-latest)", "conclusion": "success"}],
    }
    baseline = select_baseline(
        runs,
        current_sha="head",
        is_ancestor=lambda candidate, _head: candidate in {"docs", "app", "old"},
        jobs_for_run=jobs.__getitem__,
    )
    assert baseline == "app"


def test_execution_policy_reuses_exact_windows_evidence_and_selects_complementary_windows(tmp_path):
    cross_platform = classify(["app/static/app.js"], windows_patterns(tmp_path))
    assert execution_policy(cross_platform, "pull_request")["os_csv"] == "ubuntu"
    assert execution_policy(cross_platform, "push")["os_csv"] == "windows"
    reused = execution_policy(cross_platform, "push", "tested-pr-sha")
    assert reused["regression"] is False
    assert reused["os_csv"] == ""


def test_equivalent_windows_evidence_requires_same_tree_and_full_jobs():
    runs = [
        {"id": 2, "head_sha": "different", "conclusion": "success"},
        {"id": 1, "head_sha": "same", "conclusion": "success"},
    ]
    jobs = {
        2: [{"name": "Python (windows-latest)", "conclusion": "success"}],
        1: [{"name": "Python (windows-latest)", "conclusion": "success"}],
    }
    assert select_equivalent(
        runs,
        current_sha="merge",
        equivalent=lambda candidate, _head: candidate == "same",
        jobs_for_run=jobs.__getitem__,
    ) == "same"


def test_browser_inventory_uses_actual_launch_calls_without_aliasing(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_browser.py").write_text(
        "def run(p):\n    p.chromium.launch()\n    p.chromium.launch(channel='chrome')\nchannel = 'msedge'\n",
        encoding="utf-8",
    )
    assert required_browsers(tmp_path) == ["chromium", "chrome"]
    (tests / "test_edge.py").write_text(
        'def run(p):\n    p.chromium.launch(channel="msedge", headless=True)\n', encoding="utf-8"
    )
    assert required_browsers(tmp_path) == ["chromium", "chrome", "msedge"]
    assert required_browsers(tmp_path, [tests / "test_edge.py"]) == ["msedge"]
    (tests / "test_parameterized.py").write_text(
        "@pytest.mark.parametrize('channel', ['chrome', 'msedge'])\ndef test_portable(channel):\n    run(channel)\n",
        encoding="utf-8",
    )
    assert required_browsers(tmp_path, [tests / "test_parameterized.py"]) == ["chrome", "msedge"]


def test_browser_probe_only_reports_missing_without_installing(monkeypatch):
    monkeypatch.setattr(browser_setup, "probe", lambda browser: (False, f"{browser} missing"))
    results, missing = browser_setup.prepare_browsers(["chromium", "chrome"], probe_only=True)
    assert missing == ["chromium", "chrome"]
    assert results == {
        "chromium": {"ready": False, "detail": "chromium missing"},
        "chrome": {"ready": False, "detail": "chrome missing"},
    }


def test_browser_cli_accepts_an_explicit_parameterized_channel(monkeypatch, tmp_path):
    output = tmp_path / "browsers.json"
    monkeypatch.setattr(browser_setup, "probe", lambda browser: (True, "runnable"))
    monkeypatch.setattr(
        browser_setup.sys,
        "argv",
        ["browser_setup.py", "--output", str(output), "--probe-only", "--browser", "chrome"],
    )
    assert browser_setup.main() == 0
    assert list(json.loads(output.read_text(encoding="utf-8"))["browsers"]) == ["chrome"]


def test_lightweight_doc_check_reports_missing_local_targets(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    page = docs / "page.md"
    page.write_text("[good](target.md) [bad](missing.md) [web](https://example.com)\n", encoding="utf-8")
    (docs / "target.md").write_text("ok\n", encoding="utf-8")
    assert local_link_errors(tmp_path, ["docs/page.md"]) == [
        "docs/page.md:1: missing local link target missing.md"
    ]
