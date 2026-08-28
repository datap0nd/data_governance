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


def test_manual_tasks_surface_is_removed_and_legacy_artifact_pages_are_gone():
    index_html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'data-page="tasks"' not in index_html
    assert "renderTasks" not in app_js
    assert "Pending Task Summaries" not in app_js
    assert '/api/email/task-summaries' not in app_js
    assert 'tasks: "dashboard"' in app_js
    # Scripts, Scheduled Tasks, and Power Automate were replaced by Flows.
    assert 'data-page="scripts"' not in index_html
    assert 'data-page="scheduledtasks"' not in index_html
    assert 'data-page="powerautomate"' not in index_html
    for renderer in ("renderScripts", "renderScheduledTasks", "renderPowerAutomate",
                     "bindScriptsPage", "bindScheduledTasksPage", "bindPowerAutomatePage"):
        assert renderer not in app_js
    # Old hashes land on the superseding Flows page.
    assert 'scripts: "flows"' in app_js
    assert 'scheduledtasks: "flows"' in app_js
    assert 'powerautomate: "flows"' in app_js


def test_management_group_became_create_artifacts_under_tools():
    index_html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'aria-label="Management pages"' not in index_html
    assert ">Management <" not in index_html
    assert '<a href="#create" data-page="create" role="menuitem">Create Artifacts</a>' in index_html
    assert 'data-pages="create,bestpractices' in index_html
    assert "<h1>Create Artifacts</h1>" in app_js


def test_pipelines_and_new_flows_are_top_level_navigation_items():
    index_html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    style_css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")

    assert '<a href="#lineage" data-page="lineage">Pipelines</a>' in index_html
    assert index_html.count('data-page="flows"') == 1
    assert '<a href="#flows" data-page="flows" class="nav-item-with-badge">' in index_html
    assert '<span class="nav-new-badge">New</span>' in index_html
    assert 'data-pages="flows,bestpractices' not in index_html
    assert 'data-pages="lineage,flows"' not in index_html
    assert ".nav-new-badge" in style_css


def test_pipelines_exposes_report_flow_and_materialized_view_refresh_controls():
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert '<h2>Pipelines</h2>' in app_js
    assert '{ key: "flows", label: "Flows" }' in app_js
    assert 'id="lineage-report-refresh"' in app_js
    assert 'data-lin-refresh-flow' in app_js
    assert 'data-lin-refresh-mv' in app_js
    assert 'add(`flow-${flow.id}`, `source-${sourceId}`, true, flow.executable === false)' in app_js


def test_alert_surfaces_use_detection_dates_and_power_bi_error_details():
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert "Weighted views" not in app_js
    assert "weighted impact" not in app_js
    assert "First detected" in app_js
    assert "PBI Refresh Error:" in app_js
    assert "check the notes" not in app_js


def test_alert_table_uses_source_logos_and_gives_owner_room():
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    style_css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")

    assert "function alertAssetLogo(action)" in app_js
    assert 'aria-label="${labels[kind]}"' in app_js
    assert '<th style="width:9%">Type</th>' not in app_js
    assert 'class="alerts-owner-cell"' in app_js
    assert ".alert-source-logo-powerbi" in style_css
    assert ".alerts-owner-cell" in style_css
    assert "min-width: 960px" in style_css


def test_dashboard_alert_table_has_scan_driven_state_and_neutral_issue_labels():
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    table_start = app_js.index("function renderDashboardAlertsTable")
    table_end = app_js.index("function bindDashboardAlerts", table_start)
    table_source = app_js[table_start:table_end]

    assert '>Status</th>' not in table_source
    assert 'alerts-row-open' not in table_source
    assert 'status-pill-wrapper' not in table_source
    assert 'actionTypeBadge(a.type, true)' in table_source
    assert 'colspan="6"' in table_source
