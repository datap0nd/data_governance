from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def metronome_empty_database_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create the current empty schema once for tests that do not exercise startup."""
    from app import database

    root = tmp_path_factory.mktemp("database-template")
    template = root / "empty.db"
    original = database.DB_PATH
    started = time.monotonic()
    try:
        database.DB_PATH = str(template)
        database.init_db()
    finally:
        database.DB_PATH = original
    duration = time.monotonic() - started
    (root / "timing.json").write_text(
        f'{{"schema_version":1,"template_setup_seconds":{duration:.6f}}}\n',
        encoding="utf-8",
    )
    return template


@pytest.fixture()
def metronome_fresh_database(
    metronome_empty_database_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Copy the immutable schema and isolate the matching Flow filesystem."""
    target = tmp_path / "metronome-empty.db"
    shutil.copy2(metronome_empty_database_template, target)
    configured_root = os.environ.get("DG_FLOWS_ROOT")
    flows_root = (
        Path(configured_root) / tmp_path.name
        if configured_root
        else tmp_path / "metronome" / "flows"
    )
    flows_root.mkdir(parents=True, exist_ok=False)
    monkeypatch.setenv("DG_FLOWS_ROOT", str(flows_root))
    return target
