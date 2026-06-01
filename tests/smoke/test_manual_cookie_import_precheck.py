"""Smoke tests for manual cookie import pre-check."""
import pytest
import utils.xianyu_slider_stealth as slider_module


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeCookies:
    def __init__(self):
        self.values = {}

    def update(self, data):
        self.values.update(data)

    def get_dict(self):
        return dict(self.values)


class _FakeSession:
    def __init__(self, payload, response_cookies=None):
        self.headers = {}
        self.cookies = _FakeCookies()
        self._payload = payload
        self._response_cookies = dict(response_cookies or {})
        self.post_calls = []

    def post(self, url, params=None, data=None, proxies=None, timeout=None):
        self.post_calls.append(
            {
                "url": url,
                "params": params,
                "data": data,
                "proxies": proxies,
                "timeout": timeout,
            }
        )
        self.cookies.update(self._response_cookies)
        return _FakeResponse(self._payload)


class TestManualCookieImportPrecheck:
    """Manual cookie import pre-check smoke tests."""

    def _run_probe(self, payload, response_cookies=None, mocker=None):
        fake_session = _FakeSession(payload, response_cookies=response_cookies)
        patcher = mocker.patch("requests.Session", return_value=fake_session)
        result = slider_module.probe_cookie_verification_from_cookie(
            "unb=test_user; _m_h5_tk=test_token_12345; cookie2=dummy",
            proxy=None,
        )
        return result, fake_session

    def test_probe_cookie_verification_returns_verification_url_when_present(self, mocker):
        result, fake_session = self._run_probe(
            {
                "ret": ["FAIL_SYS_USER_VALIDATE::校验失败"],
                "data": {"url": "https://passport.goofish.com/identity_verify"},
                "v": "1.0",
            },
            mocker=mocker,
        )

        assert result["status"] == "verification_required"
        assert result["verification_url"] == "https://passport.goofish.com/identity_verify"
        assert result["session_cookies"]["unb"] == "test_user"
        assert fake_session.post_calls[0]["timeout"] == 30

    def test_probe_cookie_verification_treats_success_token_payload_as_cookie_valid(self, mocker):
        result, _fake_session = self._run_probe(
            {
                "ret": ["SUCCESS::调用成功"],
                "data": {
                    "accessToken": "oauth_access_token",
                    "refreshToken": "oauth_refresh_token",
                },
                "v": "1.0",
            },
            response_cookies={
                "_m_h5_tk": "refreshed_token_12345",
                "cookie2": "updated_cookie2",
            },
            mocker=mocker,
        )

        assert result["status"] == "cookie_valid"
        assert result["verification_url"] is None
        assert result["payload"]["data"]["accessToken"]
        assert result["session_cookies"]["_m_h5_tk"] == "refreshed_token_12345"
        assert result["session_cookies"]["cookie2"] == "updated_cookie2"
