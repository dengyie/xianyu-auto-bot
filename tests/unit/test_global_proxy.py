"""全局闲鱼出站代理（XIANYU_GLOBAL_PROXY_URL）：扫码/订单/评价等无账号上下文的
出站也必须走家宽出口。2026-09-13 审计发现 qr_login 硬编码 proxy=None、扫码会话
出生 IP 被机房 IP 污染。"""
import importlib

import pytest


def _load_qr_login():
    import utils.qr_login as qr_login

    importlib.reload(qr_login)
    return qr_login


def test_proxy_utils_resolution(monkeypatch):
    from utils import proxy_utils

    monkeypatch.delenv("XIANYU_GLOBAL_PROXY_URL", raising=False)
    assert proxy_utils.get_global_proxy_url() is None
    assert proxy_utils.resolve_proxy_url("http://a:1") == "http://a:1"
    assert proxy_utils.resolve_proxy_url(None) is None

    monkeypatch.setenv("XIANYU_GLOBAL_PROXY_URL", "http://mihomo-home:7891")
    assert proxy_utils.get_global_proxy_url() == "http://mihomo-home:7891"
    assert proxy_utils.resolve_proxy_url(None) == "http://mihomo-home:7891"
    assert proxy_utils.resolve_proxy_url("") == "http://mihomo-home:7891"

    assert proxy_utils.requests_proxies("http://p:1") == {
        "http://": "http://p:1",
        "https://": "http://p:1",
    }
    assert proxy_utils.requests_proxies(None) is None
    assert proxy_utils.playwright_proxy("http://p:1") == {"server": "http://p:1"}
    assert proxy_utils.playwright_proxy(None) is None


def test_qr_login_manager_uses_global_proxy(monkeypatch):
    qr_login = _load_qr_login()
    monkeypatch.setenv("XIANYU_GLOBAL_PROXY_URL", "http://mihomo-home:7891")
    manager = qr_login.QRLoginManager()
    assert manager.proxy == "http://mihomo-home:7891"


def test_qr_login_manager_without_env_stays_direct(monkeypatch):
    qr_login = _load_qr_login()
    monkeypatch.delenv("XIANYU_GLOBAL_PROXY_URL", raising=False)
    manager = qr_login.QRLoginManager()
    assert manager.proxy is None


def test_compose_injects_global_proxy_env():
    from pathlib import Path

    src = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "XIANYU_GLOBAL_PROXY_URL=${XIANYU_GLOBAL_PROXY_URL:-http://mihomo-home:7891}" in src
