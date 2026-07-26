"""Smoke tests for Xianyu token refresh request."""
import pytest
import sys
import types
from unittest import mock

import XianyuAutoAsync
from XianyuAutoAsync import XianyuLive, ConnectionState


class _FakeTokenRefreshResponse:
    def __init__(self):
        self.status = 200
        self.headers = {}
        self.json_content_type = object()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        self.json_content_type = content_type
        return {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "accessToken": "oauth_access_token",
            },
        }


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.post_calls = []

    def post(self, *args, **kwargs):
        self.post_calls.append(
            {
                "args": args,
                "kwargs": kwargs,
            }
        )
        return self.response


class TestXianyuTokenRefreshRequest:
    """Token refresh request smoke tests."""

    def test_token_refresh_slider_runtime_prefers_slidex_when_installed(self, monkeypatch):
        fake_module = types.ModuleType("slidex")

        class FakeSlidexConfig:
            pass

        class FakeSliderSolver:
            pass

        fake_module.SlidexConfig = FakeSlidexConfig
        fake_module.SliderSolver = FakeSliderSolver
        monkeypatch.setitem(sys.modules, "slidex", fake_module)

        config_cls, slider_cls, runtime_name = XianyuAutoAsync._load_token_refresh_slider_runtime()

        assert config_cls is FakeSlidexConfig
        assert slider_cls is FakeSliderSolver
        assert runtime_name == "slidex"

    def test_token_refresh_slider_runtime_falls_back_only_when_slidex_missing(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "slidex", raising=False)
        fake_legacy_module = types.ModuleType("utils.slider_solver")

        class FakeLegacySliderSolver:
            pass

        fake_legacy_module.SliderSolver = FakeLegacySliderSolver
        monkeypatch.setitem(sys.modules, "utils.slider_solver", fake_legacy_module)

        real_import = __import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "slidex":
                raise ModuleNotFoundError("No module named 'slidex'", name="slidex")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr("builtins.__import__", fake_import)

        config_cls, slider_cls, runtime_name = XianyuAutoAsync._load_token_refresh_slider_runtime()

        assert config_cls is XianyuAutoAsync._LegacySliderConfig
        assert slider_cls is FakeLegacySliderSolver
        assert runtime_name == "legacy"

    @pytest.mark.asyncio
    async def test_refresh_token_reuses_session_and_passes_proxy(self):
        fake_response = _FakeTokenRefreshResponse()
        fake_session = _FakeSession(fake_response)

        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "token_refresh_proxy_test"
        live.session = fake_session
        live._http_proxy_url = "http://127.0.0.1:8888"
        live.device_id = "device-id"
        live.cookies_str = "_m_h5_tk=test_token_12345; cookie2=dummy_cookie2"
        live.current_token = None
        live.last_token_refresh_time = 0
        live.last_message_received_time = 123
        live.message_cookie_refresh_cooldown = 0
        live.max_captcha_verification_count = 3
        live.last_token_refresh_status = None
        live.last_token_refresh_error_message = None
        live.restarted_in_browser_refresh = True
        live.init_auth_failures = 2
        live.last_init_failure_reason = "old_reason"
        live.last_init_failure_type = "old_type"
        live._skip_db_cookie_reload_for_token_refresh = True

        create_session_called = False

        async def fake_create_session():
            nonlocal create_session_called
            create_session_called = True

        live.create_session = fake_create_session
        live._reload_latest_cookies_from_db = lambda *_args, **_kwargs: None
        live._extract_set_cookie_updates = lambda headers: {}
        live._build_cookie_string_with_updates = lambda cookie_string, updates: cookie_string
        live._need_captcha_verification = lambda _payload: False
        live._consume_pending_slider_success_notice = lambda: False
        live.clear_qr_login_grace = lambda *_args, **_kwargs: None
        live.clear_init_auth_failure_state = lambda *_args, **_kwargs: None

        async def fail_send_notification(*_args, **_kwargs):
            raise AssertionError("success path should not send token refresh notification")

        live.send_token_refresh_notification = fail_send_notification

        token = await live._refresh_token_impl(allow_password_login_recovery=False)

        assert token == "oauth_access_token"
        assert not create_session_called
        assert live.current_token == "oauth_access_token"
        assert live.last_token_refresh_status == "success"
        assert live.last_token_refresh_error_message is None
        assert live.last_message_received_time == 0
        assert len(fake_session.post_calls) == 1
        request = fake_session.post_calls[0]
        assert request["kwargs"]["proxy"] == "http://127.0.0.1:8888"
        assert fake_response.json_content_type is None

    @pytest.mark.asyncio
    async def test_handle_captcha_verification_wires_slider_into_orchestrator(self):
        """生产路径走 run_slider_async_with_fallback；测试必须 mock 编排层，禁止真浏览器。"""
        from utils.slider_orchestrator import SliderVerificationResult

        created_sliders = []

        class _FakeSlider:
            def __init__(self, cookie_id="default", cookies_str="", headless=True, proxy=None, trajectory_mode="auto", **_kwargs):
                self.cookie_id = cookie_id
                self.cookies_str = cookies_str
                self.headless = headless
                self.proxy = proxy
                self.user_id = cookie_id
                self.initial_cookies = cookies_str
                created_sliders.append(self)

            async def solve(self, verification_url):
                raise AssertionError("orchestrator mock should short-circuit before solve()")

        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "token_refresh_captcha_scene_test"
        live.cookies_str = "_m_h5_tk=test_token_12345; cookie2=dummy_cookie2"
        live.proxy_config = {}
        live.connection_state = ConnectionState.DISCONNECTED
        live.ws = None
        live._safe_str = lambda exc: str(exc)
        live.last_slider_captcha_engine = None
        live.last_slider_result_message = None

        async def fake_send_notification(*_args, **_kwargs):
            return None

        live.send_token_refresh_notification = fake_send_notification

        orchestrator_result = SliderVerificationResult(
            success=False,
            cookies=None,
            engine="playwright",
            x5_cookies={},
            message="mocked primary failure without browser",
        )

        async def fake_orchestrator(slider, url, **kwargs):
            assert slider is created_sliders[0]
            assert "punish" in url
            assert kwargs.get("engine") == "playwright"
            return orchestrator_result

        human_calls = []

        async def fake_human_fallback(*, verification_url, prior_message=""):
            # 本用例只校验编排层接线；人工兜底另测，禁止在 CI 拉真浏览器。
            human_calls.append(
                {
                    "verification_url": verification_url,
                    "prior_message": prior_message,
                }
            )
            return None

        with mock.patch("XianyuAutoAsync.db_manager.get_cookie_details", return_value={}), \
             mock.patch("XianyuAutoAsync.log_captcha_event"), \
             mock.patch.object(
                 XianyuAutoAsync,
                 "_load_token_refresh_slider_runtime",
                 return_value=(XianyuAutoAsync._LegacySliderConfig, _FakeSlider, "test"),
             ), \
             mock.patch(
                 "utils.slider_orchestrator.run_slider_async_with_fallback",
                 side_effect=fake_orchestrator,
             ), \
             mock.patch.object(
                 live,
                 "_run_human_captcha_fallback",
                 side_effect=fake_human_fallback,
             ):
            result = await live._handle_captcha_verification(
                {"data": {"url": "https://example.com/punish?action=captcha"}}
            )

        assert result is None
        assert len(created_sliders) == 1
        assert created_sliders[0].cookie_id == "token_refresh_captcha_scene_test"
        assert live.last_slider_captcha_engine == "playwright"
        assert "mocked primary failure" in (live.last_slider_result_message or "")
        assert len(human_calls) == 1
        assert "punish" in human_calls[0]["verification_url"]
        assert "mocked primary failure" in human_calls[0]["prior_message"]

    @pytest.mark.asyncio
    async def test_handle_captcha_verification_merges_strict_success_cookies(self):
        """严格成功（含 x5sec）时合并 cookie 并返回字符串，仍不启动真浏览器。"""
        from utils.slider_orchestrator import SliderVerificationResult

        created_sliders = []

        class _FakeSlider:
            def __init__(self, cookie_id="default", cookies_str="", headless=True, proxy=None, trajectory_mode="auto", **_kwargs):
                self.cookie_id = cookie_id
                self.cookies_str = cookies_str
                self.headless = headless
                self.proxy = proxy
                self.user_id = cookie_id
                self.initial_cookies = cookies_str
                created_sliders.append(self)

            async def solve(self, verification_url):
                raise AssertionError("orchestrator mock should short-circuit before solve()")

        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "token_refresh_persistent_profile_test"
        live.cookies_str = "unb=u1; sgcookie=sg1; cookie2=dummy_cookie2; _m_h5_tk=test_token_12345; _m_h5_tk_enc=enc1; t=t1; cna=cna1"
        live.cookies = {
            "unb": "u1",
            "sgcookie": "sg1",
            "cookie2": "dummy_cookie2",
            "_m_h5_tk": "test_token_12345",
            "_m_h5_tk_enc": "enc1",
            "t": "t1",
            "cna": "cna1",
        }
        live.proxy_config = {}
        live.connection_state = ConnectionState.DISCONNECTED
        live.ws = None
        live._safe_str = lambda exc: str(exc)
        live.last_slider_captcha_engine = None
        live.last_slider_result_message = None

        async def fake_send_notification(*_args, **_kwargs):
            return None

        async def fake_update_config_cookies():
            return None

        live.send_token_refresh_notification = fake_send_notification
        live.update_config_cookies = fake_update_config_cookies
        live._set_runtime_cookie_state = mock.Mock()
        live._mark_slider_success_recovery = mock.Mock()
        live._mark_pending_slider_success_notice = mock.Mock()
        live._log_cookie_merge_summary = mock.Mock()

        orchestrator_result = SliderVerificationResult(
            success=True,
            cookies={
                "unb": "u1",
                "sgcookie": "sg1",
                "cookie2": "new_cookie2",
                "_m_h5_tk": "new_tk",
                "_m_h5_tk_enc": "enc1",
                "t": "t1",
                "cna": "cna1",
                "x5sec": "ticket",
            },
            engine="playwright",
            x5_cookies={"x5sec": "ticket"},
            message="ok",
        )

        async def fake_orchestrator(slider, url, **_kwargs):
            assert slider is created_sliders[0]
            assert url.endswith("action=captcha")
            return orchestrator_result

        with mock.patch("XianyuAutoAsync.db_manager.get_cookie_details", return_value={}), \
             mock.patch("XianyuAutoAsync.log_captcha_event"), \
             mock.patch.object(XianyuLive, "clear_password_login_failure_backoff", return_value=None), \
             mock.patch.object(
                 XianyuAutoAsync,
                 "_load_token_refresh_slider_runtime",
                 return_value=(XianyuAutoAsync._LegacySliderConfig, _FakeSlider, "test"),
             ), \
             mock.patch(
                 "utils.slider_orchestrator.run_slider_async_with_fallback",
                 side_effect=fake_orchestrator,
             ):
            result = await live._handle_captcha_verification(
                {"data": {"url": "https://example.com/punish?action=captcha"}}
            )

        assert result is not None
        assert "x5sec=ticket" in result
        assert "cookie2=new_cookie2" in result
        assert len(created_sliders) == 1
        assert live.last_slider_captcha_engine == "playwright"
        live._mark_pending_slider_success_notice.assert_called_once_with("token_refresh")
