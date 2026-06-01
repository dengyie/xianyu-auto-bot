"""Smoke tests for Xianyu token refresh request."""
import pytest
from unittest import mock

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
    async def test_handle_captcha_verification_marks_slider_scene_as_token_refresh(self):
        created_sliders = []

        class _FakeSlider:
            def __init__(self, cookie_id="default", cookies_str="", headless=True, proxy=None, trajectory_mode="auto"):
                self.cookie_id = cookie_id
                self.cookies_str = cookies_str
                self.headless = headless
                self.proxy = proxy
                self.risk_trigger_scene = None
                created_sliders.append(self)

            async def solve(self, verification_url):
                self.verification_url = verification_url
                return True, {"cna": "test_cna", "_m_h5_tk": "test_tk", "cookie2": "test_cookie2"}

        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "token_refresh_captcha_scene_test"
        live.cookies_str = "_m_h5_tk=test_token_12345; cookie2=dummy_cookie2"
        live.proxy_config = {}
        live.connection_state = ConnectionState.DISCONNECTED
        live.ws = None
        live._safe_str = lambda exc: str(exc)

        async def fake_send_notification(*_args, **_kwargs):
            return None

        live.send_token_refresh_notification = fake_send_notification

        with mock.patch("XianyuAutoAsync.db_manager.get_cookie_details", return_value={}), \
             mock.patch("XianyuAutoAsync.log_captcha_event"), \
             mock.patch("utils.slider_solver.SliderSolver", _FakeSlider):
            result = await live._handle_captcha_verification(
                {"data": {"url": "https://example.com/punish?action=captcha"}}
            )

        assert result is None
        assert len(created_sliders) == 1

    @pytest.mark.asyncio
    async def test_handle_captcha_verification_enables_account_persistent_profile_for_token_refresh(self):
        created_sliders = []

        class _FakeSlider:
            def __init__(self, cookie_id="default", cookies_str="", headless=True, proxy=None, trajectory_mode="auto"):
                self.cookie_id = cookie_id
                self.cookies_str = cookies_str
                self.headless = headless
                self.proxy = proxy
                self.risk_trigger_scene = None
                created_sliders.append(self)

            async def solve(self, verification_url):
                self.verification_url = verification_url
                return True, {"cna": "test_cna", "_m_h5_tk": "test_tk", "cookie2": "test_cookie2"}

        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "token_refresh_persistent_profile_test"
        live.cookies_str = "_m_h5_tk=test_token_12345; cookie2=dummy_cookie2"
        live.proxy_config = {}
        live.connection_state = ConnectionState.DISCONNECTED
        live.ws = None
        live._safe_str = lambda exc: str(exc)

        async def fake_send_notification(*_args, **_kwargs):
            return None

        live.send_token_refresh_notification = fake_send_notification

        with mock.patch("XianyuAutoAsync.db_manager.get_cookie_details", return_value={}), \
             mock.patch("XianyuAutoAsync.log_captcha_event"), \
             mock.patch("utils.slider_solver.SliderSolver", _FakeSlider):
            result = await live._handle_captcha_verification(
                {"data": {"url": "https://example.com/punish?action=captcha"}}
            )

        assert result is None
        assert len(created_sliders) == 1
