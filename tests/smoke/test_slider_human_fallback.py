"""Unit tests for human captcha fallback URL/helpers."""

from __future__ import annotations

from utils.slider_human_fallback import (
    HUMAN_ENGINE,
    build_captcha_control_url,
    resolve_captcha_public_host,
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
