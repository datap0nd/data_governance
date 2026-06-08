import os
import sqlite3
import time

from app.config import DB_PATH, PBI_SYNC_HOUR, PBI_SYNC_MINUTE


SETTING_OVERALL_REFRESH_HOUR = "overall_refresh_hour"
SETTING_OVERALL_REFRESH_MINUTE = "overall_refresh_minute"


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


DEFAULT_OVERALL_REFRESH_HOUR = _int_env("DG_OVERALL_REFRESH_HOUR", PBI_SYNC_HOUR)
DEFAULT_OVERALL_REFRESH_MINUTE = _int_env("DG_OVERALL_REFRESH_MINUTE", PBI_SYNC_MINUTE)


def _clamp_hour(value: int) -> int:
    return min(23, max(0, int(value)))


def _clamp_minute(value: int) -> int:
    return min(59, max(0, int(value)))


def ensure_app_settings_schema(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    ensure_app_settings_schema(conn)
    conn.commit()
    return conn


def _with_db_retry(fn):
    last_exc = None
    for attempt in range(4):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            time.sleep(0.25 * (attempt + 1))
    raise last_exc


def get_setting(key: str, default: str | None = None) -> str | None:
    def _read():
        conn = _get_conn()
        try:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()

    return _with_db_retry(_read)


def set_setting(key: str, value: str) -> None:
    def _write():
        conn = _get_conn()
        try:
            conn.execute(
                """INSERT INTO app_settings (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = CURRENT_TIMESTAMP""",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    _with_db_retry(_write)


def get_overall_refresh_time() -> dict:
    hour_raw = get_setting(SETTING_OVERALL_REFRESH_HOUR, str(DEFAULT_OVERALL_REFRESH_HOUR))
    minute_raw = get_setting(SETTING_OVERALL_REFRESH_MINUTE, str(DEFAULT_OVERALL_REFRESH_MINUTE))
    try:
        hour = _clamp_hour(int(hour_raw))
    except (TypeError, ValueError):
        hour = _clamp_hour(DEFAULT_OVERALL_REFRESH_HOUR)
    try:
        minute = _clamp_minute(int(minute_raw))
    except (TypeError, ValueError):
        minute = _clamp_minute(DEFAULT_OVERALL_REFRESH_MINUTE)
    return {
        "hour": hour,
        "minute": minute,
        "time": f"{hour:02d}:{minute:02d}",
    }


def set_overall_refresh_time(hour: int, minute: int) -> dict:
    hour = _clamp_hour(hour)
    minute = _clamp_minute(minute)

    def _write_refresh_time():
        conn = _get_conn()
        try:
            for key, value in (
                (SETTING_OVERALL_REFRESH_HOUR, str(hour)),
                (SETTING_OVERALL_REFRESH_MINUTE, str(minute)),
            ):
                conn.execute(
                    """INSERT INTO app_settings (key, value, updated_at)
                       VALUES (?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(key) DO UPDATE SET
                           value = excluded.value,
                           updated_at = CURRENT_TIMESTAMP""",
                    (key, value),
                )
            conn.commit()
        finally:
            conn.close()

    _with_db_retry(_write_refresh_time)
    return get_overall_refresh_time()
