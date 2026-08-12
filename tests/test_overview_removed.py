from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_overview_surface_is_removed():
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    index_html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert not (ROOT / "app" / "routers" / "overview.py").exists()
    assert "overview.router" not in main_py
    assert 'href="#overview"' not in index_html
    assert "renderOverview" not in app_js
    assert 'overview: "dashboard"' in app_js


def test_manual_tasks_surface_is_removed_but_scheduled_tasks_remain():
    index_html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'data-page="tasks"' not in index_html
    assert "renderTasks" not in app_js
    assert "Pending Task Summaries" not in app_js
    assert '/api/email/task-summaries' not in app_js
    assert 'tasks: "dashboard"' in app_js
    assert 'data-page="scheduledtasks"' in index_html
