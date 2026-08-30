from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

from app import operator_errors


ROOT = Path(__file__).parents[1]


def _event(identifier: str, *, area: str, summary: str) -> dict:
    return {
        "id": identifier,
        "created_at": "2026-08-30T12:00:00+00:00",
        "level": "error",
        "area": area,
        "logger": "app.test",
        "summary": summary,
        "error_type": "RuntimeError",
        "error_message": "example failure",
        "technical_detail": "Traceback: example failure",
        "operation_id": None,
        "scan_id": None,
        "job_id": None,
    }


def test_operator_formatter_keeps_cause_and_redacts_credentials():
    formatter = operator_errors.OperatorErrorFormatter()
    try:
        raise RuntimeError(
            "catalog failed password=hunter2 at postgres://user:secret@server/db"
        )
    except RuntimeError:
        record = logging.LogRecord(
            name="app.scanner.runner",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Scan failed",
            args=(),
            exc_info=sys.exc_info(),
        )
        record.operation_id = 42
        record.scan_id = 17

    payload = json.loads(formatter.format(record))

    assert payload["area"] == "Scanner"
    assert payload["summary"] == "Scan failed"
    assert payload["error_type"] == "RuntimeError"
    assert "hunter2" not in payload["error_message"]
    assert "secret" not in payload["technical_detail"]
    assert "password=[redacted]" in payload["error_message"]
    assert "postgres://[redacted]@server/db" in payload["technical_detail"]
    assert payload["operation_id"] == 42
    assert payload["scan_id"] == 17


def test_operator_history_reads_newest_first_and_filters():
    with tempfile.TemporaryDirectory(prefix=".operator-errors-", dir=ROOT) as temp:
        path = Path(temp) / "operator_errors.jsonl"
        backup = Path(f"{path}.1")
        backup.write_text(
            json.dumps(_event("older", area="Flows", summary="Flow failed")) + "\n",
            encoding="utf-8",
        )
        path.write_text(
            "\n".join(
                (
                    json.dumps(_event("newer", area="Scanner", summary="Scan failed")),
                    json.dumps(_event("newest", area="AI", summary="Provider failed")),
                )
            )
            + "\n",
            encoding="utf-8",
        )

        result = operator_errors.read_operator_errors(path=path, limit=10)
        scanner = operator_errors.read_operator_errors(
            path=path, limit=10, area="scanner", search="scan"
        )

        assert [item["id"] for item in result["errors"]] == ["newest", "newer", "older"]
        assert [item["id"] for item in scanner["errors"]] == ["newer"]
        assert result["storage"]["maximum_bytes"] == (
            operator_errors.MAX_FILE_BYTES * (operator_errors.BACKUP_COUNT + 1)
        )


def test_service_log_pruning_never_touches_active_files():
    with tempfile.TemporaryDirectory(prefix=".operator-prune-", dir=ROOT) as temp:
        root = Path(temp)
        active = root / "mx_analytics_error.log"
        newest = root / "mx_analytics_error-20260830.log"
        older = root / "mx_analytics_error-20260829.log"
        oversized = root / "flow_worker_error-20260830.log"
        unrelated = root / "other-20260829.log"
        for item in (active, newest, older, unrelated):
            item.write_text(item.name, encoding="utf-8")
        oversized.write_bytes(b"x")
        with oversized.open("r+b") as stream:
            stream.truncate(20 * 1024 * 1024 + 1)
        newest.touch()

        removed = operator_errors.prune_rotated_service_logs(root)

        assert removed == 2
        assert active.exists()
        assert newest.exists()
        assert not older.exists()
        assert not oversized.exists()
        assert unrelated.exists()


def test_error_log_page_and_scanner_link_are_available():
    javascript = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    index = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")

    assert 'href="#errorlogs" data-page="errorlogs"' in index
    assert "async function renderErrorLogs()" in javascript
    assert 'api("/api/system/errors?limit=150")' in javascript
    assert 'api("/api/system/errors?area=Scanner&limit=3")' in javascript
    assert 'data-open-error-logs' in javascript
    assert 'errorlogs: renderErrorLogs' in javascript


def test_service_console_noise_and_rotation_are_bounded():
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    setup = (ROOT / "setup.ps1").read_text(encoding="utf-8")

    assert 'logging.getLogger("apscheduler").setLevel(logging.WARNING)' in main
    assert "stream=sys.stdout" in main
    assert "install_operator_error_handler()" in main
    assert "AppRotateOnline 1" in setup
    assert "$NssmExe set $ServiceName AppRotateBytes 10485760" in setup
    assert "$NssmExe set $FlowServiceName AppRotateBytes 10485760" in setup
