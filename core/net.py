"""Network safety helpers. The only user-supplied-URL surface is events_sources
validate; everything else fetches curated sources.yaml URLs. We still guard:
http/https only, host must resolve to a public IP, redirects not followed."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

USER_AGENT = "hermes-local-events/0.1 (personal family event finder; polite)"

BLOCKED_HOSTS = {"localhost"}


def _is_public(host: str) -> bool:
    if host.lower() in BLOCKED_HOSTS:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:  # blocks loopback, private, link-local (incl. 169.254.169.254), reserved
            return False
    return True


def safe_probe(url: str, timeout: float = 15.0) -> dict:
    """Fetch a user-supplied URL with SSRF guards. Returns {ok, status, text?}."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "error": "only http(s) URLs are allowed"}
    if not _is_public(parsed.hostname):
        return {"ok": False, "error": "host resolves to a private/blocked address"}
    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"fetch failed: {exc.__class__.__name__}"}
    return {"ok": True, "status": resp.status_code, "text": resp.text[:500_000]}
