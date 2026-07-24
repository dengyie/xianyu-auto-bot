"""Local RateService contract tests (no network)."""

from unittest import mock

import pytest

from utils.rate_service import RateService, fetch_merchant_rate_list


def test_parse_and_sign_helpers():
    service = RateService("unb=1; _m_h5_tk=abc_123; cookie2=x", account_id="acc")
    assert service.cookies_dict["unb"] == "1"
    assert service.cookies_dict["_m_h5_tk"] == "abc_123"
    sign = service._generate_sign("1", "abc", '{"tradeId":"1"}')
    assert isinstance(sign, str) and len(sign) == 32


def test_token_and_session_detection():
    assert RateService._is_token_expired(["FAIL_SYS_TOKEN_EXOIRED::令牌过期"]) is True
    assert RateService._is_session_expired(["FAIL_SYS_SESSION_EXPIRED::Session过期"]) is True
    assert RateService._is_already_rated(["FAIL::已经评价"], {}) is True


@pytest.mark.asyncio
async def test_rate_buyer_rejects_missing_fields():
    service = RateService("", account_id="acc")
    assert (await service.rate_buyer("", "good"))["success"] is False
    service = RateService("cookie2=x", account_id="acc")
    result = await service.rate_buyer("order-1", "good")
    assert result["success"] is False
    assert "_m_h5_tk" in result["message"]


@pytest.mark.asyncio
async def test_rate_buyer_success_path(monkeypatch):
    service = RateService("unb=1; _m_h5_tk=token_1; cookie2=x", account_id="acc")

    class _Resp:
        status = 200
        headers = mock.Mock()
        headers.getall = mock.Mock(return_value=[])

        async def json(self, content_type=None):
            return {"ret": ["SUCCESS::调用成功"], "data": {}}

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr("utils.rate_service.aiohttp.ClientSession", _Session)
    result = await service.rate_buyer("order-1", "好评")
    assert result["success"] is True


def test_xianyu_auto_comment_uses_rate_service():
    from pathlib import Path

    src = Path("XianyuAutoAsync.py").read_text(encoding="utf-8")
    body = src.split("async def _call_comment_api")[1].split("def can_auto_delivery")[0]
    assert "from utils.rate_service import RateService" in body
    assert "rate_buyer" in body
    assert "session.post(comment_api_url" not in body
    assert '"cookie_str"' not in body  # 不再把 Cookie 外发到第三方 payload
