"""Frontend auth hydration contracts for Cookie dual-track login.

Regression background (2026-09-20 review):
- checkAuth() used to treat any /verify network failure as logged-out and
  hard-redirect to /, wiping a still-valid Cookie session.
- /verify used to echo the HttpOnly token on every success, so XSS could
  keep harvesting it from localStorage even after Cookie auth landed.
"""
from pathlib import Path

import pytest

APP_ACCOUNTS_JS = Path("static/js/app-accounts.js")
LOGIN_HTML = Path("static/login.html")
DOWNLOAD_HTML = Path("static/download.html")


@pytest.fixture(scope="module")
def accounts_js():
    return APP_ACCOUNTS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def login_html():
    return LOGIN_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def download_html():
    return DOWNLOAD_HTML.read_text(encoding="utf-8")


def test_check_auth_does_not_clear_token_on_transient_failure(accounts_js):
    start = accounts_js.index("async function checkAuth()")
    body = accounts_js[start:start + 2500]
    catch_start = body.rindex("} catch (err) {")
    catch_body = body[catch_start:]

    assert "localStorage.removeItem('auth_token')" not in catch_body
    assert "window.location.href = '/'" not in catch_body
    assert "return true;" in catch_body


def test_check_auth_only_hydrates_local_token_when_missing(accounts_js):
    start = accounts_js.index("async function checkAuth()")
    body = accounts_js[start:start + 2500]
    assert "if (result.token && !getAuthToken())" in body


def test_download_check_auth_keeps_session_on_transient_failure(download_html):
    start = download_html.index("async function checkAuth()")
    end = download_html.index("async function doLogout()")
    body = download_html[start:end]
    catch_start = body.rindex("} catch (e) {")
    catch_body = body[catch_start:]

    assert "localStorage.removeItem('auth_token')" not in catch_body
    assert "return true;" in catch_body
    assert "if (result.token && !authToken)" in body


def test_login_page_only_hydrates_token_when_local_storage_empty(login_html):
    assert "if (verifyResult.token && !existingToken)" in login_html
    assert "credentials: 'same-origin'" in login_html
