"""Browser sign-in: each computer chooses one person from Users.

A browser is bound by its client key (kept in localStorage) and, as a
fallback, by its IP address. A binding stores the chosen person's id, never a
typed name, so the displayed name always comes from Users: a rename shows at
once and deleting a person signs their computers out. Only the host PC (the
machine running Metronome) can move a computer that is already signed in to
another person; see ``app.local_access.is_host_request``.
"""
from __future__ import annotations

import re
import socket
import sqlite3
from datetime import datetime, timezone

# Set once the free-text name bindings that predate person ids are cleared.
BINDING_MARKER = "identity_person_binding_v1"
HOST_ACTOR = "Host PC"

_identity_cache: dict[tuple[str, str | None], dict | None] = {}
_hostname_cache: dict[str, str | None] = {}


def _db_path() -> str:
    # The database module owns DB_PATH and tests repoint it, so read it lazily.
    from app import database
    return database.DB_PATH


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create or repair the identity tables (also created by database.MIGRATIONS)."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_ips (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address  TEXT NOT NULL UNIQUE,
            person_name TEXT NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_devices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            client_key      TEXT NOT NULL UNIQUE,
            person_name     TEXT NOT NULL,
            last_ip_address TEXT,
            hostname        TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    for stmt in (
        "ALTER TABLE user_ips ADD COLUMN hostname TEXT",
        "ALTER TABLE user_ips ADD COLUMN client_key TEXT",
        "ALTER TABLE user_ips ADD COLUMN last_seen_at DATETIME",
        "ALTER TABLE user_ips ADD COLUMN updated_at DATETIME",
        "ALTER TABLE user_ips ADD COLUMN person_id INTEGER",
        "ALTER TABLE user_devices ADD COLUMN person_id INTEGER",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_devices_client_key ON user_devices(client_key)")


def initialize(conn: sqlite3.Connection) -> bool:
    """Clear the typed-name sign-ins once, so everyone picks a person from Users.

    Returns True on the run that cleared them. The app_settings marker keeps
    later startups from signing anyone out again.
    """
    ensure_schema(conn)
    if conn.execute("SELECT 1 FROM app_settings WHERE key=?", (BINDING_MARKER,)).fetchone():
        return False
    conn.execute("DELETE FROM user_ips")
    conn.execute("DELETE FROM user_devices")
    conn.execute(
        "INSERT INTO app_settings(key, value) VALUES (?, ?)",
        (BINDING_MARKER, datetime.now(timezone.utc).isoformat()),
    )
    clear_cache()
    return True


def clear_cache() -> None:
    _identity_cache.clear()


def clean_client_key(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if len(value) > 128:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        return None
    return value


def resolve_hostname(ip: str) -> str | None:
    """Best-effort reverse DNS lookup for LAN hostnames."""
    if ip in _hostname_cache:
        return _hostname_cache[ip]
    try:
        host = socket.gethostbyaddr(ip)[0]
    except Exception:
        host = None
    _hostname_cache[ip] = host
    return host


def resolve(ip: str, client_key: str | None = None) -> dict | None:
    """The person this browser chose: ``{"person_id", "name"}``, or None.

    The browser key wins over the IP address, so a laptop that changes
    address keeps its person.
    """
    cache_key = (ip, client_key)
    if cache_key in _identity_cache:
        return _identity_cache[cache_key]
    try:
        conn = connect()
        try:
            row = None
            if client_key:
                row = conn.execute(
                    """SELECT p.id, p.name FROM user_devices d JOIN people p ON p.id = d.person_id
                       WHERE d.client_key = ?""",
                    (client_key,),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    """SELECT p.id, p.name FROM user_ips u JOIN people p ON p.id = u.person_id
                       WHERE u.ip_address = ?""",
                    (ip,),
                ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    identity = {"person_id": row["id"], "name": row["name"]} if row else None
    _identity_cache[cache_key] = identity
    return identity


def bind(ip: str, client_key: str | None, hostname: str | None, person_id: int, name: str) -> None:
    """Sign this IP address and browser in as one person."""
    _write_binding(ip, client_key, hostname, person_id, name)
    clear_cache()


def _write_binding(ip: str, client_key: str | None, hostname: str | None, person_id: int, name: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = connect()
    try:
        existing_ip = conn.execute("SELECT id FROM user_ips WHERE ip_address = ? LIMIT 1", (ip,)).fetchone()
        if existing_ip:
            conn.execute(
                """UPDATE user_ips
                   SET person_id = ?, person_name = ?, hostname = COALESCE(?, hostname),
                       client_key = COALESCE(?, client_key), updated_at = ?, last_seen_at = ?
                   WHERE id = ?""",
                (person_id, name, hostname, client_key, now, now, existing_ip[0]),
            )
        else:
            conn.execute(
                """INSERT INTO user_ips
                   (ip_address, person_id, person_name, hostname, client_key, created_at, updated_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ip, person_id, name, hostname, client_key, now, now, now),
            )
        if client_key:
            existing_device = conn.execute(
                "SELECT id FROM user_devices WHERE client_key = ? LIMIT 1", (client_key,)
            ).fetchone()
            if existing_device:
                conn.execute(
                    """UPDATE user_devices
                       SET person_id = ?, person_name = ?, last_ip_address = ?,
                           hostname = COALESCE(?, hostname), updated_at = ?, last_seen_at = ?
                       WHERE id = ?""",
                    (person_id, name, ip, hostname, now, now, existing_device[0]),
                )
            else:
                conn.execute(
                    """INSERT INTO user_devices
                       (client_key, person_id, person_name, last_ip_address, hostname,
                        created_at, updated_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (client_key, person_id, name, ip, hostname, now, now, now),
                )
        conn.commit()
    finally:
        conn.close()


def mark_seen(ip: str, client_key: str | None, identity: dict, hostname: str | None) -> None:
    """Refresh last-seen metadata for the person this browser already chose.

    The person does not change, so cached identities stay valid.
    """
    _write_binding(ip, client_key, hostname, identity["person_id"], identity["name"])


def sign_out_person(db: sqlite3.Connection, person_id: int) -> int:
    """Remove every computer binding for one person; returns how many were removed."""
    ensure_schema(db)
    removed = db.execute("DELETE FROM user_ips WHERE person_id = ?", (person_id,)).rowcount
    removed += db.execute("DELETE FROM user_devices WHERE person_id = ?", (person_id,)).rowcount
    clear_cache()
    return removed


def signed_in_counts(db: sqlite3.Connection) -> dict[int, int]:
    """Distinct computers (by IP address) signed in as each person."""
    ensure_schema(db)
    return {
        row[0]: row[1]
        for row in db.execute(
            "SELECT person_id, COUNT(*) FROM user_ips WHERE person_id IS NOT NULL GROUP BY person_id"
        )
    }


def request_identity(request) -> tuple[int | None, bool]:
    """``(person_id, is_host)`` for a request.

    The identity middleware sets both on every HTTP request. A call without
    them comes from Metronome's own code (the scheduler, recovery paths and
    in-process tests calling a route function directly), which acts as the
    host application itself.
    """
    state = getattr(request, "state", None)
    person_id = getattr(state, "person_id", None) if state is not None else None
    try:
        is_host = state.is_host
    except AttributeError:
        return person_id, True
    return person_id, bool(is_host)
