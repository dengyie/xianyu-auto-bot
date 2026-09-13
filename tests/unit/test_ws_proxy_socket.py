"""WebSocket 代理连接：sync python_socks 出真实 socket，TLS 由 websockets 处理。

回归背景：async v2 的 Proxy.connect() 返回 AsyncioSocketStream，手工
ssl.wrap_socket 必炸（no attribute 'getsockopt'），导致 WS 静默回落直连、
代理形同虚设。修复后必须把真实 socket（非阻塞）交给 websockets。
"""
import asyncio
import socket
from types import SimpleNamespace

import websockets

from XianyuAutoAsync import XianyuLive


class _FakeSyncProxy:
    """替身 python_socks.sync.Proxy：记录调用，返回模拟 CONNECT 隧道的 socket。"""

    last_instance = None

    def __init__(self, proxy_type, host, port, username=None, password=None):
        self.proxy_type, self.host, self.port = proxy_type, host, port
        self.username, self.password = username, password
        self.connect_calls = []
        _FakeSyncProxy.last_instance = self

    def connect(self, dest_host, dest_port, timeout=None):
        self.connect_calls.append((dest_host, dest_port, timeout))
        client, _peer = socket.socketpair()
        client.settimeout(15)  # 模拟 sync connect 返回的阻塞超时 socket
        return client


def _make_self(proxy_config, proxy_url="http://mihomo-home:7891"):
    return SimpleNamespace(
        cookie_id="c1",
        proxy_config=proxy_config,
        base_url="wss://wss-goofish.dingtalk.com/",
        _get_proxy_url=lambda: proxy_url,
        _safe_str=str,
    )


def _run_ws_connect(monkeypatch, fake_self):
    captured = {}

    def fake_connect(uri, **kwargs):
        captured["uri"] = uri
        captured["kwargs"] = kwargs
        return object()  # 函数只返回 Connect 对象，调用方稍后才 await

    monkeypatch.setattr(websockets, "connect", fake_connect)
    result = asyncio.run(
        XianyuLive._create_websocket_connection(fake_self, {"Cookie": "x"})
    )
    return result, captured


def test_proxied_ws_passes_plain_nonblocking_socket(monkeypatch):
    monkeypatch.setattr("python_socks.sync.Proxy", _FakeSyncProxy)

    fake_self = _make_self({"proxy_type": "http", "proxy_host": "mihomo-home", "proxy_port": 7891})
    result, captured = _run_ws_connect(monkeypatch, fake_self)

    proxy = _FakeSyncProxy.last_instance
    assert proxy.connect_calls == [("wss-goofish.dingtalk.com", 443, 15)]
    assert proxy.proxy_type.name == "HTTP"

    sock = captured["kwargs"]["sock"]
    assert isinstance(sock, socket.socket)
    assert sock.getblocking() is False  # 事件循环要求非阻塞
    assert captured["kwargs"]["extra_headers"] == {"Cookie": "x"}
    assert captured["uri"].startswith("wss://")
    assert result is not None


def test_proxied_ws_socks5_type_maps(monkeypatch):
    monkeypatch.setattr("python_socks.sync.Proxy", _FakeSyncProxy)

    fake_self = _make_self({"proxy_type": "socks5", "proxy_host": "p", "proxy_port": 1080})
    _run_ws_connect(monkeypatch, fake_self)

    assert _FakeSyncProxy.last_instance.proxy_type.name == "SOCKS5"


def test_proxy_failure_falls_back_to_direct(monkeypatch):
    class _BrokenProxy:
        def __init__(self, *a, **k):
            pass

        def connect(self, *a, **k):
            raise OSError("proxy unreachable")

    monkeypatch.setattr("python_socks.sync.Proxy", _BrokenProxy)

    fake_self = _make_self({"proxy_type": "http", "proxy_host": "dead", "proxy_port": 1})
    result, captured = _run_ws_connect(monkeypatch, fake_self)

    # 代理失败必须回落直连（home-win 关机时 bot 仍可用），且不带 sock
    assert "sock" not in captured["kwargs"]
    assert captured["kwargs"]["extra_headers"] == {"Cookie": "x"}
    assert result is not None


def test_no_proxy_config_connects_directly(monkeypatch):
    fake_self = _make_self({"proxy_type": "none"}, proxy_url="")
    result, captured = _run_ws_connect(monkeypatch, fake_self)

    assert "sock" not in captured["kwargs"]
    assert result is not None
