from pathlib import Path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]


def test_import_data_backend_is_removed_without_removing_mv_refresh():
    paths = set(app.openapi()["paths"])

    assert not (ROOT / "app" / "routers" / "data_import.py").exists()
    assert all(not path.startswith("/api/data-import") for path in paths)
    assert "/api/materialized-views/{source_id}/refresh" in paths


def test_import_data_ui_is_removed_and_old_bookmarks_open_flows():
    index_html = (ROOT / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    app_js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'href="#dataimport"' not in index_html
    assert 'data-page="dataimport"' not in index_html
    assert "renderDataImport" not in app_js
    assert "bindDataImportPage" not in app_js
    assert "/api/data-import" not in app_js
    assert 'dataimport: "flows"' in app_js
    assert 'apiPost(`/api/materialized-views/${sourceId}/refresh`)' in app_js
