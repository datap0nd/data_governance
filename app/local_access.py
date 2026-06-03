import ipaddress
import socket
from functools import lru_cache

from fastapi import HTTPException, Request


def _clean_host(value: str | None) -> str:
    if not value:
        return ""
    host = value.strip().strip("[]").lower()
    if "%" in host:
        host = host.split("%", 1)[0]
    return host


def _ip_key(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(_clean_host(value)))
    except ValueError:
        return None


def _normalized_ip(value: str | None) -> str | None:
    if not value:
        return None
    return _ip_key(value)


@lru_cache(maxsize=1)
def _server_ip_keys() -> frozenset[str]:
    ips = {"127.0.0.1", "::1"}
    names = {socket.gethostname(), socket.getfqdn(), "localhost"}
    for name in names:
        if not name:
            continue
        try:
            for family, _, _, _, sockaddr in socket.getaddrinfo(name, None):
                if family in (socket.AF_INET, socket.AF_INET6) and sockaddr:
                    ip = _ip_key(sockaddr[0])
                    if ip:
                        ips.add(ip)
        except OSError:
            continue
    try:
        _, _, host_ips = socket.gethostbyname_ex(socket.gethostname())
        for host_ip in host_ips:
            ip = _ip_key(host_ip)
            if ip:
                ips.add(ip)
    except OSError:
        pass
    return frozenset(ips)


@lru_cache(maxsize=1)
def _server_host_keys() -> frozenset[str]:
    names = {socket.gethostname(), socket.getfqdn(), "localhost"}
    cleaned = {_clean_host(name) for name in names if name}
    cleaned.update({name.split(".", 1)[0] for name in list(cleaned) if name})
    return frozenset(name for name in cleaned if name)


def is_server_machine(host: str | None) -> bool:
    """Return True when a request came from the machine running the app."""
    clean = _clean_host(host)
    if not clean:
        return False
    ip = _ip_key(clean)
    if ip:
        parsed = ipaddress.ip_address(ip)
        return parsed.is_loopback or ip in _server_ip_keys()
    if clean in _server_host_keys():
        return True
    try:
        for result in socket.getaddrinfo(clean, None):
            sockaddr = result[-1]
            if _ip_key(sockaddr[0]) in _server_ip_keys():
                return True
        return False
    except OSError:
        return False


def ensure_admin_schema(conn):
    """Create or repair the table that grants remote admin by client IP."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS admin_user_ips (
            ip_address  TEXT PRIMARY KEY,
            enabled     INTEGER DEFAULT 1,
            granted_by  TEXT,
            granted_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_user_ips_enabled ON admin_user_ips(enabled)")


def is_admin_ip(ip: str | None) -> bool:
    """Return True when an IP is the server machine or has admin enabled."""
    normalized = _normalized_ip(ip)
    if not normalized:
        return False
    if is_server_machine(normalized):
        return True
    try:
        import sqlite3
        from app.config import DB_PATH

        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            ensure_admin_schema(conn)
            conn.commit()
            row = conn.execute(
                "SELECT enabled FROM admin_user_ips WHERE ip_address = ?",
                (normalized,),
            ).fetchone()
            return bool(row and row[0])
        finally:
            conn.close()
    except Exception:
        return False


def is_admin_request(request: Request) -> bool:
    """Return True when the request should have admin capabilities."""
    state_admin = getattr(getattr(request, "state", None), "is_admin", None)
    if state_admin is not None:
        return bool(state_admin)
    ip = request.client.host if request.client else ""
    return is_admin_ip(ip)


def require_admin(request: Request, detail: str = "Admin access required"):
    """Raise 403 unless the request has admin capabilities."""
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail=detail)
