"""遗留 vendored 密码登录链路：networkidle 假超时降级（与 slidex 0.6.40 对齐）。

生产始终走 slidex 包，但 `utils.slider_stealth_mixins` 是 slidex 缺失时的兜底，
同一处 `page.goto(login_url, wait_until='networkidle', timeout=60000)` 有相同
根因缺陷：处罚页后台信标让 networkidle 永不达成 → 60s 假超时上抛，打断整条
密码登录链路。这里锁定「仅 Timeout 降级 domcontentloaded，连接错误照重试，
其余错误照抛」的行为。
"""
from __future__ import annotations

import pytest

# 必须先经 xianyu_slider_stealth 入口导入：直接导入 mixin 模块会在类体注解
# 求值时触发 _host 代理回环导入。
from utils.xianyu_slider_stealth import XianyuSliderStealth

LOGIN_URL = "https://www.goofish.com/im"


class _FakeTimeout(Exception):
    """类型名含 Timeout，匹配降级判据。"""


class _FakePage:
    def __init__(self, results):
        self.calls = []
        self._results = list(results)

    def goto(self, url, wait_until=None, timeout=None):
        self.calls.append((url, wait_until, timeout))
        if self._results:
            exc = self._results.pop(0)
            if exc is not None:
                raise exc
        return None

    @property
    def waits(self):
        return [call[1] for call in self.calls]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)


def test_networkidle_success_used_directly():
    page = _FakePage([None])
    assert XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t") == "networkidle"
    assert page.waits == ["networkidle"]


def test_networkidle_timeout_falls_back():
    page = _FakePage([_FakeTimeout("Page.goto: Timeout 60000ms exceeded."), None])
    assert XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t") == "domcontentloaded"
    assert page.waits == ["networkidle", "domcontentloaded"]


def test_fallback_failure_still_soft_ok():
    page = _FakePage([_FakeTimeout("timeout"), RuntimeError("fallback boom")])
    assert XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t") == "domcontentloaded"


def test_connection_error_retried():
    page = _FakePage([RuntimeError("net::ERR_CONNECTION_CLOSED"), None])
    assert XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t") == "networkidle"
    assert page.waits == ["networkidle", "networkidle"]


def test_non_retryable_error_raises():
    page = _FakePage([RuntimeError("unexpected navigation failure")])
    with pytest.raises(RuntimeError, match="unexpected navigation failure"):
        XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t")
