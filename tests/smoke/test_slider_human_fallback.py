"""Unit tests for human captcha fallback URL/helpers."""

from __future__ import annotations

from unittest import mock

import pytest

from utils.slider_human_fallback import (
    HUMAN_ENGINE,
    build_captcha_control_url,
    resolve_captcha_public_host,
    run_human_captcha_session,
)
from utils.slider_orchestrator import validate_slider_result


def test_build_captcha_control_url_uses_public_base(monkeypatch):
    monkeypatch.setenv("CAPTCHA_PUBLIC_BASE_URL", "https://bot.example.com")
    url = build_captcha_control_url("sid-1", "tok-1")
    assert url == "https://bot.example.com/api/captcha/control/sid-1?token=tok-1"


def test_build_captcha_control_url_appends_api_port(monkeypatch):
    monkeypatch.delenv("CAPTCHA_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("SERVER_HOST", "10.0.0.8")
    monkeypatch.setenv("API_PORT", "8090")
    monkeypatch.setenv("CAPTCHA_CONTROL_SCHEME", "http")
    url = build_captcha_control_url("sid-2", "tok-2")
    assert url == "http://10.0.0.8:8090/api/captcha/control/sid-2?token=tok-2"


def test_build_captcha_control_url_skips_port_when_host_has_port(monkeypatch):
    monkeypatch.delenv("CAPTCHA_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("SERVER_HOST", "example.com:9443")
    monkeypatch.setenv("CAPTCHA_CONTROL_SCHEME", "https")
    url = build_captcha_control_url("sid-3", "tok-3")
    assert url == "https://example.com:9443/api/captcha/control/sid-3?token=tok-3"


def test_build_captcha_control_url_without_token(monkeypatch):
    monkeypatch.setenv("CAPTCHA_PUBLIC_BASE_URL", "https://bot.example.com")
    url = build_captcha_control_url("sid-4", "")
    assert url == "https://bot.example.com/api/captcha/control/sid-4"


def test_resolve_captcha_public_host_prefers_server_host(monkeypatch):
    monkeypatch.setenv("SERVER_HOST", "public.example")
    monkeypatch.setenv("PUBLIC_IP", "1.2.3.4")
    assert resolve_captcha_public_host() == "public.example"


def test_human_engine_still_requires_x5():
    result = validate_slider_result(True, {"unb": "1"}, engine=HUMAN_ENGINE)
    assert result.success is False
    assert result.engine == HUMAN_ENGINE

    ok = validate_slider_result(True, {"unb": "1", "x5sec": "t"}, engine=HUMAN_ENGINE)
    assert ok.success is True
    assert ok.x5_cookies == {"x5sec": "t"}


@pytest.mark.asyncio
async def test_human_session_ignores_immediate_complete_when_slider_never_seen(monkeypatch):
    monkeypatch.setenv("XY_SLIDER_HUMAN_FALLBACK", "1")
    created = []

    class FakeSolver:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.page = object()
            self.context = None

        async def _init_browser(self):
            return None

        async def _load_page(self, url):
            return None

        async def _wait_slider(self):
            return False

        async def _get_cookies(self):
            return {"cookie2": "x"}

        async def close(self):
            return None

    class FakeConfig:
        pass

    class FakeController:
        def __init__(self):
            self.active_sessions = {}
            self.check_calls = 0

        async def create_session(self, session_id, page, cookie_id="default"):
            self.active_sessions[session_id] = {"captcha_info": None, "captcha_seen": False}
            return {"token": "t", "captcha_info": None}

        async def check_completion(self, session_id):
            self.check_calls += 1
            return True

        def is_completed(self, session_id):
            return False

        async def close_session(self, session_id):
            self.active_sessions.pop(session_id, None)

    controller = FakeController()
    fake_remote = mock.MagicMock()
    fake_remote.captcha_controller = controller

    async def fake_load():
        return FakeConfig, FakeSolver, "slidex"

    with mock.patch.dict("sys.modules", {"slidex.remote": fake_remote}), \
         mock.patch("utils.slider_human_fallback._load_slider_solver_class", side_effect=fake_load):
        result = await run_human_captcha_session(
            cookie_id="u-human",
            cookies_str="c=1",
            verification_url="https://example.com/punish",
            timeout=1.2,
            poll_interval=0.4,
        )

    assert created and created[0].get("provider") == "auto"
    assert result.success is False
    assert controller.check_calls >= 2
    assert "超时" in (result.message or "")
