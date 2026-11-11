import ipaddress
import os
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


def is_own_host_name(host: str | None) -> bool:
    """True when ``host``, as a browser sent it in the Host header, names this PC.

    Decided without a DNS lookup. A page on another site can point its own
    domain at this PC's address (DNS rebinding); the browser then treats that
    page and Metronome as one origin, and resolving the name would make the
    request look as if it came from the host PC's own Metronome tab. Only
    addresses and names this machine knows as its own are accepted:
    loopback, its IP addresses, ``localhost``, its computer name, and any
    extra names listed in ``DG_HOST_NAMES`` (comma separated).
    """
    clean = _clean_host(host)
    if not clean:
        return False
    ip = _ip_key(clean)
    if ip:
        return ipaddress.ip_address(ip).is_loopback or ip in _server_ip_keys()
    if clean in _server_host_keys():
        return True
    extra = {_clean_host(name) for name in os.environ.get("DG_HOST_NAMES", "").split(",")}
    return clean in extra


def _same_site_and_direct(request: Request) -> bool:
    """No proxy in between, and not a request another site made the browser send."""
    if any(request.headers.get(name) for name in
           ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")):
        return False
    origin = request.headers.get("origin")
    own_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin and origin.rstrip("/") != own_origin:
        return False
    fetch_site = request.headers.get("sec-fetch-site", "")
    return not fetch_site or fetch_site in {"same-origin", "none"}


def is_local_request(request: Request) -> bool:
    """Require a direct same-origin request to a loopback browser URL.

    ``http://localhost:8000`` and ``http://127.0.0.1:8000`` both qualify; the
    name used to be refused because only a literal address was accepted.
    """
    if request.client.host == "testclient" and request.url.hostname == "testserver":
        return True
    try:
        peer = ipaddress.ip_address(request.client.host)
    except (ValueError, AttributeError, TypeError):
        return False
    host = _clean_host(getattr(request.url, "hostname", None))
    host_ip = _ip_key(host)
    host_is_loopback = host == "localhost" or bool(host_ip and ipaddress.ip_address(host_ip).is_loopback)
    if not peer.is_loopback or not host_is_loopback:
        return False
    return _same_site_and_direct(request)


def is_host_request(request: Request) -> bool:
    """Return True for a same-site request from the host PC itself.

    The host PC is the machine running Metronome, reached by any of its own
    addresses (localhost, 127.0.0.1 or its LAN name). It administers Users,
    SQL passwords and every Flow. A page on another site open in the host's
    browser must not borrow that right, so cross-site and proxied requests are
    refused.
    """
    client = getattr(request, "client", None)
    peer = client.host if client else ""
    if peer == "testclient" and request.url.hostname == "testserver":
        return True
    if not is_server_machine(peer):
        return False
    # The address the browser asked for must be this PC's own; see
    # is_own_host_name for why a resolvable name is not enough.
    if not is_own_host_name(request.url.hostname):
        return False
    return _same_site_and_direct(request)


def has_app_access(request: Request) -> bool:
    """Return True for all requests; access tiers were removed."""
    return True


def require_app_access(request: Request, detail: str = "Access required"):
    """Compatibility hook for routes that used to have elevated access checks.

    It checks nothing. A route that must be limited to the host PC calls
    ``require_host`` instead.
    """
    return None


HOST_ONLY_HINT = "On the host PC, open Metronome as http://localhost:8000."


def require_host(request: Request, detail: str = "Only the host PC can change this.") -> None:
    """Refuse a change that only the machine running Metronome may make.

    Uses what the identity middleware decided for this request (see
    ``is_host_request``). A call without that state comes from Metronome's own
    code, which acts as the host application.
    """
    from app import user_identity

    _person_id, is_host = user_identity.request_identity(request)
    if not is_host:
        raise HTTPException(status_code=403, detail=f"{detail} {HOST_ONLY_HINT}")
