"""Authentication coverage for the optional remote captcha router."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


class _FakeCaptchaController:
    def __init__(self):
        self.active_sessions = {}
        self.websocket_connections = {}


def _load_router_module(monkeypatch):
    fake_slidex = types.ModuleType("slidex")
    fake_remote = types.ModuleType("slidex.remote")
    fake_remote.captcha_controller = _FakeCaptchaController()
    monkeypatch.setitem(sys.modules, "slidex", fake_slidex)
    monkeypatch.setitem(sys.modules, "slidex.remote", fake_remote)

    module_path = Path(__file__).resolve().parents[2] / "api_captcha_remote.py"
    spec = importlib.util.spec_from_file_location("captcha_remote_auth_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_captcha_sessions_reject_missing_control_key(monkeypatch):
    monkeypatch.setenv("CAPTCHA_CONTROL_API_KEY", "captcha-secret")
    module = _load_router_module(monkeypatch)
    app = FastAPI()
    app.include_router(module.router)

    response = TestClient(app).get("/api/captcha/sessions")

    assert response.status_code == 401


def test_captcha_websocket_rejects_missing_control_key(monkeypatch):
    monkeypatch.setenv("CAPTCHA_CONTROL_API_KEY", "captcha-secret")
    module = _load_router_module(monkeypatch)
    app = FastAPI()
    app.include_router(module.router)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with TestClient(app).websocket_connect("/api/captcha/ws/session-id"):
            pass

    assert exc_info.value.code == 4401
