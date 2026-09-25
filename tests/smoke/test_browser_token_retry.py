"""CDP 模式浏览器侧 Token 重试（x5sec 绑定挣票客户端）。

滑块在用户真实浏览器里通过后，x5sec 只对浏览器侧请求生效；bot 侧 aiohttp
重试会被 FAIL_SYS_USER_VALIDATE 重新惩罚（2026-09-25 生产实测）。_try_browser_token_retry
在浏览器页面内用浏览器 jar 当前的 _m_h5_tk 签名发 token 请求（bot 侧副本已漂移），
成功后把 jar 里的 goofish/taobao cookie 同步回会话。
"""
import json
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

from utils.xianyu_utils import generate_sign
from xianyu_token_mixins import TokenMixin


class _FakePage:
    def __init__(self, url, response_text):
        self.url = url
        self.response_text = response_text
        self.expressions = []
        self.closed = False

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    async def evaluate(self, expression):
        self.expressions.append(expression)
        return self.response_text

    async def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, cookies, pages, new_page_text=""):
        self._cookies = cookies
        self.pages = list(pages)
        self._new_page_text = new_page_text
        self.created_pages = []

    async def cookies(self):
        return self._cookies

    async def new_page(self):
        page = _FakePage("about:blank", self._new_page_text)
        self.pages.append(page)
        self.created_pages.append(page)
        return page


class _FakeBrowser:
    def __init__(self, context):
        self.contexts = [context]


class _FakePW:
    async def stop(self):
        self.stopped = True


def _make_mixin():
    m = TokenMixin.__new__(TokenMixin)
    m.cookie_id = "1926782908"
    m.device_id = "dev123"
    m.cookies_str = "unb=1926782908; _m_h5_tk=bottoken_123; sgcookie=sg"
    m.current_token = None
    m.last_token_refresh_status = None
    m.last_token_refresh_error_message = None
    m.last_token_refresh_time = 0
    m.last_message_received_time = 0
    m.init_auth_failures = 0
    m.last_init_failure_reason = None
    m.last_init_failure_type = None
    m._safe_str = staticmethod(lambda e: str(e))  # 真实定义在宿主类上
    return m


def _jar():
    return [
        {"name": "_m_h5_tk", "value": "jarT0k_999", "domain": ".goofish.com"},
        {"name": "x5sec", "value": "X5SECVALUE", "domain": ".goofish.com"},
        {"name": "unb", "value": "1926782908", "domain": ".taobao.com"},
        {"name": "other_site", "value": "leak?", "domain": ".linux.do"},
    ]


def _extract_query(expression):
    """从 fetch 表达式末尾的第一个 JSON 字符串参数里还原 URL 查询参数。"""
    args = expression[expression.rindex("})(") + 3:]
    qs = json.loads(args.split(", ")[0])
    return parse_qs(qs)


@pytest.mark.asyncio
async def test_browser_retry_signs_with_jar_token_and_merges_jar(monkeypatch):
    success = json.dumps({
        "api": "mtop.taobao.idlemessage.pc.login.token",
        "ret": ["SUCCESS::调用成功"],
        "data": {"accessToken": "NEW_TOKEN"},
    })
    page = _FakePage("https://h5api.m.goofish.com/", success)
    context = _FakeContext(_jar(), [page])

    m = _make_mixin()
    m._connect_cdp_browser = mock.AsyncMock(return_value=(_FakePW(), _FakeBrowser(context)))
    persisted = {}
    m._persist_runtime_cookie_state = mock.AsyncMock(
        side_effect=lambda cookies_str=None, cookies_dict=None, source=None: persisted.update(v=cookies_str, source=source) or True
    )
    m._clear_qr_login_grace_period = mock.MagicMock()
    m.clear_init_auth_failure_state = mock.MagicMock()
    m._consume_pending_slider_success_notice = mock.MagicMock(return_value=False)
    m.send_token_refresh_notification = mock.AsyncMock()

    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")

    token = await m._try_browser_token_retry()

    assert token == "NEW_TOKEN"
    assert m.current_token == "NEW_TOKEN"
    assert m.last_token_refresh_status == "success"

    # 签名必须用浏览器 jar 的 _m_h5_tk（jarT0k），而不是 bot 侧副本（bottoken）
    qs = _extract_query(page.expressions[0])
    data_val = '{"appKey":"444e9908a51d1cb236a27862abc769c9","deviceId":"dev123"}'
    assert generate_sign(qs["t"][0], "jarT0k", data_val) == qs["sign"][0]
    assert qs["appKey"][0] == "34839810"
    assert qs["api"][0] == "mtop.taobao.idlemessage.pc.login.token"

    # jar 同步回会话：x5sec/_m_h5_tk 进来，跨站 cookie（linux.do）不得混入
    assert persisted["source"] == "cdp_browser_token_sync"
    assert "x5sec=X5SECVALUE" in persisted["v"]
    assert "_m_h5_tk=jarT0k_999" in persisted["v"]
    assert "other_site" not in persisted["v"]


@pytest.mark.asyncio
async def test_browser_retry_returns_none_on_punish(monkeypatch):
    punished = json.dumps({
        "api": "mtop.taobao.idlemessage.pc.login.token",
        "ret": ["FAIL_SYS_USER_VALIDATE", "RGV587_ERROR::SM::哎哟喂"],
        "data": {"url": "https://h5api.m.goofish.com/punish"},
    })
    page = _FakePage("https://h5api.m.goofish.com/", punished)
    context = _FakeContext(_jar(), [page])

    m = _make_mixin()
    m._connect_cdp_browser = mock.AsyncMock(return_value=(_FakePW(), _FakeBrowser(context)))
    m._persist_runtime_cookie_state = mock.AsyncMock()

    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")

    token = await m._try_browser_token_retry()

    assert token is None
    assert m.current_token is None
    m._persist_runtime_cookie_state.assert_not_called()


@pytest.mark.asyncio
async def test_browser_retry_disabled_without_env(monkeypatch):
    m = _make_mixin()
    m._connect_cdp_browser = mock.AsyncMock()

    monkeypatch.delenv("XY_SLIDER_CDP_ENDPOINT", raising=False)

    assert await m._try_browser_token_retry() is None
    m._connect_cdp_browser.assert_not_called()


_SUCCESS_TEXT = json.dumps({
    "api": "mtop.taobao.idlemessage.pc.login.token",
    "ret": ["SUCCESS::调用成功"],
    "data": {"accessToken": "NEW_TOKEN"},
})


def _happy_mocks(m, context):
    m._connect_cdp_browser = mock.AsyncMock(return_value=(_FakePW(), _FakeBrowser(context)))
    m._persist_runtime_cookie_state = mock.AsyncMock(return_value=True)
    m._clear_qr_login_grace_period = mock.MagicMock()
    m.clear_init_auth_failure_state = mock.MagicMock()
    m._consume_pending_slider_success_notice = mock.MagicMock(return_value=False)
    m.send_token_refresh_notification = mock.AsyncMock()


@pytest.mark.asyncio
async def test_browser_retry_closes_page_it_opens(monkeypatch):
    """无现成 h5api 页时新开标签页，用完即关（不残留用户浏览器）。"""
    context = _FakeContext(_jar(), [], new_page_text=_SUCCESS_TEXT)
    m = _make_mixin()
    _happy_mocks(m, context)
    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")

    token = await m._try_browser_token_retry()

    assert token == "NEW_TOKEN"
    assert len(context.created_pages) == 1
    assert context.created_pages[0].closed is True


@pytest.mark.asyncio
async def test_browser_retry_keeps_reused_page_open(monkeypatch):
    """复用已有 h5api 页时不误关用户标签页。"""
    page = _FakePage("https://h5api.m.goofish.com/", _SUCCESS_TEXT)
    context = _FakeContext(_jar(), [page])
    m = _make_mixin()
    _happy_mocks(m, context)
    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")

    token = await m._try_browser_token_retry()

    assert token == "NEW_TOKEN"
    assert context.created_pages == []
    assert page.closed is False


@pytest.mark.asyncio
async def test_browser_retry_closes_opened_page_on_failure(monkeypatch):
    """evaluate/解析失败 → 返回 None 回退 aiohttp，且自开页仍被关闭。"""
    context = _FakeContext(_jar(), [], new_page_text="not-json")
    m = _make_mixin()
    m._connect_cdp_browser = mock.AsyncMock(return_value=(_FakePW(), _FakeBrowser(context)))
    m._persist_runtime_cookie_state = mock.AsyncMock()
    m._safe_str = staticmethod(lambda e: str(e))
    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")

    token = await m._try_browser_token_retry()

    assert token is None
    assert len(context.created_pages) == 1
    assert context.created_pages[0].closed is True
    m._persist_runtime_cookie_state.assert_not_called()


@pytest.mark.asyncio
async def test_browser_retry_does_not_log_access_token(monkeypatch):
    """响应体携带 accessToken：日志只允许出现 ret，不允许出现 token 本体。"""
    from loguru import logger as loguru_logger

    page = _FakePage("https://h5api.m.goofish.com/", _SUCCESS_TEXT)
    context = _FakeContext(_jar(), [page])
    m = _make_mixin()
    _happy_mocks(m, context)
    monkeypatch.setenv("XY_SLIDER_CDP_ENDPOINT", "http://172.19.0.1:9222")

    records = []
    handler_id = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        token = await m._try_browser_token_retry()
    finally:
        loguru_logger.remove(handler_id)

    assert token == "NEW_TOKEN"
    assert not any("NEW_TOKEN" in record for record in records)
