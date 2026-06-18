"""Trusted client IP resolution for request security decisions."""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Iterable


TRUTHY_VALUES = {"1", "true", "yes", "on"}
DEFAULT_TRUSTED_PROXIES = ("127.0.0.1", "::1")


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _request_peer_host(request: Any) -> str:
    client = getattr(request, "client", None)
    return str(getattr(client, "host", "") or "")


def _is_trusted_proxy(peer_host: str, trusted_proxies: Iterable[str]) -> bool:
    if not peer_host:
        return False
    trusted_proxies = list(trusted_proxies)
    if peer_host in trusted_proxies:
        return True
    try:
        peer_ip = ipaddress.ip_address(peer_host)
    except ValueError:
        return False

    for proxy in trusted_proxies:
        try:
            if "/" in proxy:
                if peer_ip in ipaddress.ip_network(proxy, strict=False):
                    return True
            elif peer_ip == ipaddress.ip_address(proxy):
                return True
        except ValueError:
            continue
    return False


def should_trust_proxy_headers() -> bool:
    return os.getenv("TRUST_PROXY_HEADERS", "").strip().lower() in TRUTHY_VALUES


def configured_trusted_proxies() -> list[str]:
    configured = _split_csv(os.getenv("TRUSTED_PROXY_IPS", ""))
    return list(DEFAULT_TRUSTED_PROXIES) + configured


def get_client_ip(request: Any, *, default: str = "unknown") -> str:
    """Return a client IP that only trusts forwarded headers from trusted peers."""
    if not request:
        return default

    peer_host = _request_peer_host(request)
    headers = getattr(request, "headers", {}) or {}
    if should_trust_proxy_headers() and _is_trusted_proxy(peer_host, configured_trusted_proxies()):
        forwarded_for = headers.get("X-Forwarded-For", "")
        if forwarded_for:
            first_hop = forwarded_for.split(",", 1)[0].strip()
            if first_hop:
                return first_hop
        real_ip = headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip.strip()

    return peer_host or default
