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

    tokens3 = manager._extract_login_tokens_from_url(
        "https://passport.goofish.com/done?loginTicket=lt-789&stoken=st1"
    )
    assert tokens3["login_token"] == "lt-789"
    assert tokens3["stoken"] == "st1"

    # path 末段兜底猜测（仅当 query 无 token）
    tokens4 = manager._extract_login_tokens_from_url(
        "https://passport.goofish.com/iv/AbCdEfGhIjKlMnOpQrStUv"
    )
    assert tokens4.get("login_token_guess") == "AbCdEfGhIjKlMnOpQrStUv"


def test_apply_external_callback_url_rejects_bad_domain():
    manager = QRLoginManager()
    session = _session(manager)
    result = asyncio.run(
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
    result = asyncio.run(
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
        result = asyncio.run(
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

    result = asyncio.run(
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

    result = asyncio.run(
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
        result = asyncio.run(
            manager.apply_external_callback_url(
                session.session_id,
                "https://passport.goofish.com/done?token=partial",
                source="user_url",
            )
        )

    assert result["success"] is True
    assert result["via"] == "browser_url"
    assert session.status == "success"


def test_detect_verification_ended_elsewhere_rejects_mini_expired():
    """mini_expired.htm 不是用户完成验证，不能当 ended_elsewhere。"""
    manager = QRLoginManager()
    session = _session(manager)

    class FakePage:
        url = "https://passport.goofish.com/iv/static/mini_expired.htm"

        async def evaluate(self, *_a, **_k):
            return "二维码已失效，请重新获取"

    ended = asyncio.run(manager._detect_verification_ended_elsewhere(session, FakePage()))
    assert ended is False
    assert session.verification_ended_elsewhere is False
    assert "过期" in (session.user_hint or "")


def test_detect_verification_ended_elsewhere_accepts_real_end_text():
    manager = QRLoginManager()
    session = _session(manager)

    class FakePage:
        url = "https://passport.goofish.com/iv/done"

        async def evaluate(self, *_a, **_k):
            return "身份校验流程已经结束，请关闭页面"

    ended = asyncio.run(manager._detect_verification_ended_elsewhere(session, FakePage()))
    assert ended is True
    assert session.verification_ended_elsewhere is True


def test_apply_external_callback_url_reports_missing_unb():
    """token 与浏览器都拿不到 unb 时，明确提示缺 unb，且别拖太久。"""
    manager = QRLoginManager()
    session = _session(manager)

    async def fake_exchange(sess, token):
        return {"cookie2": "ck-only", "XSRF-TOKEN": "x"}

    async def fake_probe(sess, page, context):
        return False

    class FakePage:
        url = "https://passport.goofish.com/iv/static/mini_expired.htm"

        async def goto(self, *a, **k):
            return None

        async def wait_for_timeout(self, *a, **k):
            return None

        async def close(self):
            return None

        async def evaluate(self, *_a, **_k):
            return "二维码已失效"

    class FakeContext:
        async def add_cookies(self, *a, **k):
            return None

        async def new_page(self):
            return FakePage()

        async def cookies(self):
            return [{"name": "cookie2", "value": "ck-only"}]

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
        result = asyncio.run(
            manager.apply_external_callback_url(
                session.session_id,
                "https://passport.goofish.com/done?token=no-unb",
                source="user_url",
            )
        )

    assert result["success"] is False
    assert "unb" in result.get("missing_keys", [])
    assert "unb" in result["message"]
    assert session.status == "verification_required"


def test_apply_external_callback_url_fast_fails_iv_or_expired_without_token():
    """纯 IV/过期页没有 login_token 时必须快速失败，禁止再开 Playwright 空耗。"""
    manager = QRLoginManager()
    session = _session(manager)

    result = asyncio.run(
        manager.apply_external_callback_url(
            session.session_id,
            "https://passport.goofish.com/iv/remote/pc/mini_login_check.htm?havana_iv_token=CN-SPLIT-x",
            source="user_url",
        )
    )
    assert result["success"] is False
    assert result.get("via") == "fast_fail_no_token"
    assert "login_token" in result["message"] or "Cookie" in result["message"]


def test_encode_verification_url_as_qr_is_fallback_only(tmp_path, monkeypatch):
    """encode URL 仅作 Playwright 失败时的兜底图；主路径应是 keep-alive 打开验证页。"""
    manager = QRLoginManager()
    session = _session(manager)
    session.verification_url = (
        "https://passport.goofish.com/iv/remote/pc/mini_login_check.htm?havana_iv_token=tok-1"
    )

    saved = {}

    def fake_save(data):
        saved["bytes"] = data
        return "static/uploads/images/fake_qr.png"

    def fake_delete(_path):
        return None

    monkeypatch.setattr("utils.qr_login.image_manager.save_image", fake_save)
    monkeypatch.setattr("utils.qr_login.image_manager.delete_image", fake_delete)

    ok = manager._encode_verification_url_as_qr(session)
    assert ok is True
    assert session.screenshot_path == "static/uploads/images/fake_qr.png"
    assert session.verification_qr_encoded is True
    assert saved.get("bytes")  # PNG bytes written
    # 主路径必须是 Playwright keep-alive（与 GuDong 一致），encode 只是兜底
    import inspect
    launch_src = inspect.getsource(manager._launch_verification_page)
    assert "page.goto" in launch_src
    assert "_encode_verification_url_as_qr" in launch_src
    assert "GuDong" in launch_src or "keep-alive" in launch_src
    # keep-alive 存活路径禁止「截图失败立刻 encode」；encode 应只在 except 完全失败分支
    assert "禁止" in launch_src or "铁律" in launch_src or "keep-alive 存活" in launch_src
    # 文案必须说明扫独立码不会自动登录
    assert "不会" in (session.user_hint or "") or "兜底" in (session.user_hint or "")


def test_probe_browser_login_success_skips_im_without_cookies():
    """无完整 Cookie 时禁止 goto /im（a0b72c6d：/im 30s 超时拖死 keep-alive）。"""
    manager = QRLoginManager()
    session = _session(manager)

    class FakePage:
        url = "https://passport.goofish.com/iv/verify"

        async def evaluate(self, *_a, **_k):
            return ""

    class FakeContext:
        async def cookies(self):
            return [{"name": "cna", "value": "only-device"}]

        async def new_page(self):
            raise AssertionError("无完整 Cookie 时不应 new_page 去 /im")

    ok = asyncio.run(
        manager._probe_browser_login_success(session, FakePage(), FakeContext())
    )
    assert ok is False
    assert session.status == "verification_required"


def test_probe_browser_login_success_marks_when_cookies_ready():
    manager = QRLoginManager()
    session = _session(manager)

    class FakePage:
        url = "https://passport.goofish.com/iv/verify"

        async def evaluate(self, *_a, **_k):
            return ""

    class FakeContext:
        async def cookies(self):
            return [
                {"name": "unb", "value": "u-1"},
                {"name": "cookie2", "value": "ck-1"},
                {"name": "sgcookie", "value": "sg-1"},
            ]

        async def new_page(self):
            raise AssertionError("已有完整 Cookie 应直接收口，不必 /im")

    ok = asyncio.run(
        manager._probe_browser_login_success(session, FakePage(), FakeContext())
    )
    assert ok is True
    assert session.status == "success"
    assert session.unb == "u-1"


def test_get_session_status_exposes_verification_qr_encoded():
    manager = QRLoginManager()
    session = _session(manager)
    session.screenshot_path = "static/uploads/images/x.png"
    session.verification_qr_encoded = True
    session.user_hint = None

    status = manager.get_session_status(session.session_id)
    assert status["verification_qr_encoded"] is True
    assert "兜底" in status["message"] or "不会" in status["message"]


def test_launch_source_forbids_encode_on_capture_miss():
    """launch 源码：截图失败路径不得调用 encode；仅 Playwright 全失败 except 可 encode。"""
    import inspect

    manager = QRLoginManager()
    src = inspect.getsource(manager._launch_verification_page)
    assert "page.goto" in src
    assert "_capture_verification_screenshot" in src
    # keep-alive 主路径不得在「截图失败」后立刻 encode（旧 bug）
    assert "if not session.screenshot_path:\n                self._encode_verification_url_as_qr" not in src
    assert "if not session.screenshot_path:\n                    self._encode_verification_url_as_qr" not in src
    # encode 至多出现一次，且必须在「打开失败」分支附近
    assert src.count("_encode_verification_url_as_qr") == 1
    idx = src.index("_encode_verification_url_as_qr")
    assert "打开服务端验证页失败" in src[max(0, idx - 250) : idx + 200]

def test_probe_browser_login_success_no_im_branch_in_source():
    """_probe_browser_login_success 源码不得再 goto /im（收割改独立方法）。"""
    import inspect
    manager = QRLoginManager()
    src = inspect.getsource(manager._probe_browser_login_success)
    assert "goofish.com/im" not in src
    assert "if not cookies_ready" in src or "return False" in src


def test_harvest_after_verification_marks_success_from_im_cookies():
    """验证结束后同 context 导航收割：读到完整 Cookie 即 success。"""
    manager = QRLoginManager()
    session = _session(manager)
    session.verification_ended_elsewhere = True

    class FakePage:
        url = "https://passport.goofish.com/iv/done"

        async def goto(self, *a, **k):
            self.url = "https://www.goofish.com/im"

        async def wait_for_timeout(self, *_a, **_k):
            return None

    class FakeContext:
        async def cookies(self):
            return [
                {"name": "unb", "value": "u-harvest"},
                {"name": "cookie2", "value": "ck-h"},
                {"name": "sgcookie", "value": "sg-h"},
            ]

    ok = asyncio.run(
        manager._harvest_login_cookies_after_verification(session, FakePage(), FakeContext())
    )
    assert ok is True
    assert session.status == "success"
    assert session.unb == "u-harvest"
    assert session.verification_harvest_attempted is True

    # 第二次不再重复导航
    class BoomPage:
        url = "x"

        async def goto(self, *a, **k):
            raise AssertionError("harvest 只应尝试一次")

    ok2 = asyncio.run(
        manager._harvest_login_cookies_after_verification(session, BoomPage(), FakeContext())
    )
    assert ok2 is False or session.status == "success"


def test_get_session_status_hides_verification_url_while_keepalive():
    """keep-alive 存活且非 encode 时不向前端暴露 iframeRedirectUrl。"""
    manager = QRLoginManager()
    session = _session(manager)
    session.verification_url = "https://passport.goofish.com/iv/remote/pc/mini_login_check.htm?havana_iv_token=tok"
    session.screenshot_path = "static/uploads/images/real.png"
    session.verification_qr_encoded = False

    class AliveTask:
        def done(self):
            return False

    session.verification_task = AliveTask()
    status = manager.get_session_status(session.session_id)
    assert status["status"] == "verification_required"
    assert status.get("verification_url") in (None, "")
    assert status["screenshot_path"] == session.screenshot_path

    # encode 兜底时可以暴露（前端要警告）
    session.verification_qr_encoded = True
    status2 = manager.get_session_status(session.session_id)
    assert status2.get("verification_url") == session.verification_url


def test_launch_source_uses_nonblocking_capture_and_harvest():
    """launch 循环必须：wait_for 包截图 + 验证结束后 harvest。"""
    import inspect
    manager = QRLoginManager()
    src = inspect.getsource(manager._launch_verification_page)
    assert "asyncio.wait_for" in src
    assert "_harvest_login_cookies_after_verification" in src
    assert "timeout_ms=4000" in src or "timeout_ms=4" in src

