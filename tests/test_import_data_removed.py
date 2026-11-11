from pathlib import Path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]


def test_import_data_backend_is_removed_without_removing_mv_refresh():
    paths = set(app.openapi()["paths"])

    assert not (ROOT / "app" / "routers" / "data_import.py").exists()
    assert all(not path.startswith("/api/data-import") for path in paths)
    assert "/api/materialized-views/{source_id}/refresh" in paths
