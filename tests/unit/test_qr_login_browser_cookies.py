"""Regression: Playwright rejects cookies that set both url and path."""

from utils.qr_login import QRLoginManager


def test_build_browser_cookies_uses_url_without_path():
    manager = QRLoginManager()
    cookies = {
        "unb": "123",
        "cookie2": "abc",
        "_m_h5_tk": "token_0",
        "": "ignored-empty-name",
        "empty_value": None,
    }

    result = manager._build_browser_cookies(
        "https://passport.goofish.com/iv/remote/pc/mini_login_check.htm?havana_iv_token=x",
        cookies,
    )

    assert result == [
        {
            "name": "unb",
            "value": "123",
            "url": "https://passport.goofish.com",
        },
        {
            "name": "cookie2",
            "value": "abc",
            "url": "https://passport.goofish.com",
        },
        {
            "name": "_m_h5_tk",
            "value": "token_0",
            "url": "https://passport.goofish.com",
        },
    ]
    for cookie in result:
        assert "path" not in cookie
        assert "domain" not in cookie
        assert cookie["url"].startswith("https://")


def test_build_browser_cookies_falls_back_to_passport_host():
    manager = QRLoginManager()
    result = manager._build_browser_cookies("", {"a": "1"})
    assert result == [
        {
            "name": "a",
            "value": "1",
            "url": "https://passport.goofish.com",
        }
    ]
