import ipaddress
import socket
from functools import lru_cache

from fastapi import Request


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


def has_app_access(request: Request) -> bool:
    """Return True for all requests; access tiers were removed."""
    return True


def require_app_access(request: Request, detail: str = "Access required"):
    """Compatibility hook for routes that used to have elevated access checks."""
    return None
