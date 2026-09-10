from __future__ import annotations

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
def metronome_fresh_database(metronome_empty_database_template: Path, tmp_path: Path) -> Path:
    """Copy the immutable empty template to a test-owned database path."""
    target = tmp_path / "metronome-empty.db"
    shutil.copy2(metronome_empty_database_template, target)
    return target
