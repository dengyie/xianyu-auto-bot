"""轻量保活收到 Set-Cookie 时必须能落库，不得因幻影模块属性崩掉。

2026-09-19 线上现象：keep_session_alive 每 180 秒报一次
`module 'XianyuAutoAsync' has no attribute 'cookies_str'`，状态被标 exception。
根因是 CookieMixin 拆分时把方法参数错写成模块全局 `_host.cookies_str`，
而该名字只存在于 XianyuAutoAsync.py 的 `if __name__ == '__main__'` 分支里，
容器以导入方式运行 → 每次响应带 Set-Cookie 就 AttributeError，
服务端下发的 Cookie 续期被整体丢弃（`_m_h5_tk` 轮换丢失有会话老化风险）。

这里刻意**不 monkeypatch `_host`**：走真实 _HostProxy → getattr(XianyuAutoAsync, ...)
解析，才能把「幻影属性」这类缺陷钉死。
"""
from pathlib import Path

import pytest

from xianyu_cookie_mixin import CookieMixin

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeHeaders:
    """最小 aiohttp CIMultiDictProxy 替身（大小写不敏感 + getall）。"""

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def __contains__(self, key):
        return any(k.lower() == key.lower() for k, _ in self._pairs)

    def getall(self, key, default=None):
        values = [v for k, v in self._pairs if k.lower() == key.lower()]
        return values or (default if default is not None else [])


class _Host(CookieMixin):
    cookie_id = "c-keepalive"

    def __init__(self, cookies=None):
        self.cookies = dict(cookies or {"_m_h5_tk": "old_token", "unb": "123456"})
        self.cookies_str = self._serialize_cookies(self.cookies)
        self.session = None
        self.config_updates = 0

    async def update_config_cookies(self):
        self.config_updates += 1


@pytest.mark.asyncio
async def test_keepalive_set_cookie_is_applied_without_phantom_attr():
    """保活响应带 Set-Cookie：更新必须落到运行时状态，且不得抛异常。"""
    host = _Host()
    headers = _FakeHeaders([
        ("Content-Type", "application/json"),
        ("Set-Cookie", "_m_h5_tk=new_token_123; Path=/; HttpOnly"),
        ("Set-Cookie", "_m_h5_tk_enc=enc_456; Path=/"),
    ])

    changed = await host._apply_response_cookie_updates(headers, "session_keepalive")

    assert changed is True
    assert host.cookies["_m_h5_tk"] == "new_token_123"
    assert host.cookies["_m_h5_tk_enc"] == "enc_456"
    assert "_m_h5_tk=new_token_123" in host.cookies_str
    assert host.config_updates == 1


@pytest.mark.asyncio
async def test_keepalive_without_set_cookie_is_a_noop():
    """无 Set-Cookie 时提前返回，不写库（这就是此前保活偶尔显示"成功"的原因）。"""
    host = _Host()
    before = host.cookies_str

    changed = await host._apply_response_cookie_updates(
        _FakeHeaders([("Content-Type", "application/json")]), "session_keepalive"
    )

    assert changed is False
    assert host.cookies_str == before
    assert host.config_updates == 0


def test_set_runtime_cookie_state_honors_cookies_str_argument():
    """只传 cookies_str（无 dict）时必须解析参数本身，而不是去摸模块全局。"""
    host = _Host()

    host._set_runtime_cookie_state(cookies_str="a=1; b=2", source="unit")

    assert host.cookies == {"a": "1", "b": "2"}
    assert host.cookies_str == "a=1; b=2"


@pytest.mark.asyncio
async def test_persist_runtime_cookie_state_passes_argument_through():
    host = _Host()

    changed = await host._persist_runtime_cookie_state(cookies_str="x=9", source="unit")

    assert changed is True
    assert host.cookies == {"x": "9"}
    assert host.config_updates == 1


def test_recent_auth_ok_keepalive_skips_password_login_when_cookie_still_valid():
    """WS 连败不等于 Cookie 失效：保活刚成功时不要开密码登录进处罚页。"""
    import time

    from XianyuAutoAsync import XianyuLive

    host = XianyuLive.__new__(XianyuLive)
    host.last_session_keepalive_status = "success"
    host.last_session_keepalive_time = time.time()
    host.session_keepalive_interval = 600

    assert host._has_recent_auth_ok_keepalive() is True

    host.last_session_keepalive_status = "network_failed"
    assert host._has_recent_auth_ok_keepalive() is False

    host.last_session_keepalive_status = "success"
    host.last_session_keepalive_time = time.time() - 10
    assert host._has_recent_auth_ok_keepalive(window_seconds=5) is False


def test_source_has_no_phantom_host_cookies_str():
    """源码契约：任何 `_host.cookies_str` 都会在容器里 100% 命中 AttributeError。"""
    offenders = []
    for path in sorted(REPO_ROOT.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "_host.cookies_str" in line:
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"幻影属性残留: {offenders}"
