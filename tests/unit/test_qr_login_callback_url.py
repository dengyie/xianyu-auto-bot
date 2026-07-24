"""User-pasted callback/redirect URL can close a verification_required QR session."""

import asyncio
from unittest.mock import AsyncMock, patch

from utils.qr_login import QRLoginManager, QRLoginSession


def _session(manager: QRLoginManager, status: str = "verification_required") -> QRLoginSession:
    session_id = "test-session-callback-url"
    session = QRLoginSession(session_id, user_id=1)
    session.status = status
    session.cookies = {"cna": "device-cna-1"}
    manager.sessions[session_id] = session
    return session


def test_extract_first_url_from_mixed_text():
    manager = QRLoginManager()
    text = "请打开 https://passport.goofish.com/iv/verify?havana_iv_token=abc 完成"
    assert manager._extract_first_url(text) == (
        "https://passport.goofish.com/iv/verify?havana_iv_token=abc"
    )
    assert manager._extract_first_url("  https://www.goofish.com/im?x=1  ") == (
        "https://www.goofish.com/im?x=1"
    )
    assert manager._extract_first_url("no url here") is None


def test_is_allowed_callback_url_domain_guard():
    manager = QRLoginManager()
    assert manager._is_allowed_callback_url("https://passport.goofish.com/iv/verify") is True
    assert manager._is_allowed_callback_url("https://login.taobao.com/member/login.jhtml") is True
    assert manager._is_allowed_callback_url("https://evil.example.com/steal") is False
    assert manager._is_allowed_callback_url("ftp://goofish.com/x") is False
    assert manager._is_allowed_callback_url("not-a-url") is False


def test_extract_login_tokens_from_url():
    manager = QRLoginManager()
    tokens = manager._extract_login_tokens_from_url(
        "https://passport.goofish.com/callback?token=tok123&havana_iv_token=hiv1"
    )
    assert tokens["login_token"] == "tok123"
    assert tokens["havana_iv_token"] == "hiv1"

    tokens2 = manager._extract_login_tokens_from_url(
        "https://passport.goofish.com/x#lgToken=lg456"
    )
    assert tokens2["login_token"] == "lg456"


def test_apply_external_callback_url_rejects_bad_domain():
    manager = QRLoginManager()
    session = _session(manager)
    result = asyncio.get_event_loop().run_until_complete(
        manager.apply_external_callback_url(
            session.session_id,
            "https://evil.example.com/callback?token=x",
        )
    )
    assert result["success"] is False
    assert "域名" in result["message"]
    assert session.status == "verification_required"


def test_apply_external_callback_url_rejects_empty():
    manager = QRLoginManager()
    session = _session(manager)
    result = asyncio.get_event_loop().run_until_complete(
        manager.apply_external_callback_url(session.session_id, "   ")
    )
    assert result["success"] is False
    assert session.status == "verification_required"


def test_apply_external_callback_url_login_token_success():
    manager = QRLoginManager()
    session = _session(manager)

    async def fake_exchange(sess, token):
        assert token == "tok-ok"
        return {"unb": "u-100", "cookie2": "ck2", "sgcookie": "sg1"}

    with patch.object(manager, "_exchange_login_token", side_effect=fake_exchange):
        result = asyncio.get_event_loop().run_until_complete(
            manager.apply_external_callback_url(
                session.session_id,
                "https://passport.goofish.com/done?token=tok-ok",
                source="user_url",
            )
        )

    assert result["success"] is True
    assert result["status"] == "success"
    assert result["via"] == "login_token"
    assert result["unb"] == "u-100"
    assert session.status == "success"
    assert session.success_source == "user_url"
    assert session.cookies["cookie2"] == "ck2"


def test_apply_external_callback_url_already_success_is_idempotent():
    manager = QRLoginManager()
    session = _session(manager)
    session.status = "success"
    session.unb = "u-1"
    session.cookies = {"unb": "u-1", "cookie2": "ck-old"}

    result = asyncio.get_event_loop().run_until_complete(
        manager.apply_external_callback_url(
            session.session_id,
            "https://passport.goofish.com/done?token=other",
        )
    )
    assert result["success"] is True
    assert result.get("already_success") is True
    assert session.unb == "u-1"
    assert session.cookies["cookie2"] == "ck-old"


def test_apply_external_callback_url_rejects_expired():
    manager = QRLoginManager()
    session = _session(manager)
    session.created_time = 0

    result = asyncio.get_event_loop().run_until_complete(
        manager.apply_external_callback_url(
            session.session_id,
            "https://passport.goofish.com/done?token=x",
        )
    )
    assert result["success"] is False
    assert result["status"] == "expired"
    assert session.status == "expired"


def test_get_session_status_accepts_user_url_flag():
    manager = QRLoginManager()
    session = _session(manager)
    session.screenshot_path = "/static/uploads/qr.png"
    session.verification_ended_elsewhere = True

    status = manager.get_session_status(session.session_id)
    assert status["status"] == "verification_required"
    assert status["accept_user_url"] is True
    assert status["accept_user_cookies"] is True
    assert "网址" in status["message"] or "Cookie" in status["message"]


def test_apply_external_callback_url_browser_fallback_success():
    """token 换取不完整时，走 Playwright 探测成功路径。"""
    manager = QRLoginManager()
    session = _session(manager)

    async def fake_exchange(sess, token):
        return {"cookie2": "partial-only"}  # 缺 unb，不完整

    async def fake_probe(sess, page, context):
        sess.cookies.update({"unb": "u-browser", "cookie2": "ck-b", "sgcookie": "sg-b"})
        sess.unb = "u-browser"
        sess.status = "success"
        sess.success_source = "user_url"
        return True

    class FakePage:
        url = "https://www.goofish.com/im"

        async def goto(self, *a, **k):
            return None

        async def wait_for_timeout(self, *a, **k):
            return None

        async def close(self):
            return None

    class FakeContext:
        async def add_cookies(self, *a, **k):
            return None

        async def new_page(self):
            return FakePage()

        async def cookies(self):
            return [
                {"name": "unb", "value": "u-browser"},
                {"name": "cookie2", "value": "ck-b"},
            ]

        async def close(self):
            return None

    class FakeBrowser:
        async def new_context(self, **k):
            return FakeContext()

        async def close(self):
            return None

    class FakePlaywright:
        class chromium:
            @staticmethod
            async def launch(**k):
                return FakeBrowser()

        async def stop(self):
            return None

    class FakeAsyncPlaywright:
        async def start(self):
            return FakePlaywright()

    with patch.object(manager, "_exchange_login_token", side_effect=fake_exchange), \
         patch.object(manager, "_probe_browser_login_success", side_effect=fake_probe), \
         patch("playwright.async_api.async_playwright", return_value=FakeAsyncPlaywright()):
        result = asyncio.get_event_loop().run_until_complete(
            manager.apply_external_callback_url(
                session.session_id,
                "https://passport.goofish.com/done?token=partial",
                source="user_url",
            )
        )

    assert result["success"] is True
    assert result["via"] == "browser_url"
    assert session.status == "success"
