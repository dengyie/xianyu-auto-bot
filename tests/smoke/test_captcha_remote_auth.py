"""Authentication coverage for the optional remote captcha router."""

from __future__ import annotations

import importlib.util
import secrets
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
        self._tokens = {}

    def get_session_token(self, session_id: str):
        return self._tokens.get(str(session_id or "").strip())

    def verify_session_token(self, session_id: str, token) -> bool:
        expected = self.get_session_token(session_id)
        return bool(expected and token and secrets.compare_digest(expected, str(token)))

    def is_completed(self, session_id: str) -> bool:
        data = self.active_sessions.get(session_id) or {}
        return bool(data.get("completed", False))

    def session_exists(self, session_id: str) -> bool:
        return session_id in self.active_sessions


def _load_router_module(monkeypatch, controller=None):
    fake_slidex = types.ModuleType("slidex")
    fake_remote = types.ModuleType("slidex.remote")
    fake_remote.captcha_controller = controller or _FakeCaptchaController()
    monkeypatch.setitem(sys.modules, "slidex", fake_slidex)
    monkeypatch.setitem(sys.modules, "slidex.remote", fake_remote)

    module_path = Path(__file__).resolve().parents[2] / "api_captcha_remote.py"
    spec = importlib.util.spec_from_file_location("captcha_remote_auth_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    # force re-import so monkeypatched captcha_controller is bound
    sys.modules.pop("captcha_remote_auth_test_module", None)
    spec.loader.exec_module(module)
    return module, fake_remote.captcha_controller


def test_captcha_sessions_reject_missing_control_key(monkeypatch):
    monkeypatch.setenv("CAPTCHA_CONTROL_API_KEY", "captcha-secret")
    module, _ = _load_router_module(monkeypatch)
    app = FastAPI()
    app.include_router(module.router)

    response = TestClient(app).get("/api/captcha/sessions")

    assert response.status_code == 401


def test_captcha_websocket_rejects_missing_control_key(monkeypatch):
    monkeypatch.setenv("CAPTCHA_CONTROL_API_KEY", "captcha-secret")
    module, _ = _load_router_module(monkeypatch)
    app = FastAPI()
    app.include_router(module.router)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with TestClient(app).websocket_connect("/api/captcha/ws/session-id"):
            pass

    assert exc_info.value.code == 4401


def test_control_page_accepts_session_token(monkeypatch, tmp_path):
    monkeypatch.setenv("CAPTCHA_CONTROL_API_KEY", "captcha-secret")
    controller = _FakeCaptchaController()
    controller.active_sessions["sess-1"] = {
        "screenshot": "data:image/png;base64,xx",
        "captcha_info": {},
        "viewport": {"width": 400, "height": 300},
        "completed": False,
    }
    controller._tokens["sess-1"] = "session-token-abc"
    module, _ = _load_router_module(monkeypatch, controller)

    html = tmp_path / "captcha_control.html"
    html.write_text("<html><body>panel</body></html>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)

    ok = client.get("/api/captcha/control/sess-1?token=session-token-abc")
    assert ok.status_code == 200
    assert "INITIAL_SESSION_ID" in ok.text
    assert "INITIAL_SESSION_TOKEN" in ok.text
    assert "session-token-abc" in ok.text

    bad = client.get("/api/captcha/control/sess-1?token=wrong")
    assert bad.status_code == 401

    missing = client.get("/api/captcha/control/sess-1")
    assert missing.status_code == 401


def test_status_accepts_session_token_or_api_key(monkeypatch):
    monkeypatch.setenv("CAPTCHA_CONTROL_API_KEY", "captcha-secret")
    controller = _FakeCaptchaController()
    controller.active_sessions["sess-2"] = {"completed": True}
    controller._tokens["sess-2"] = "tok-2"
    module, _ = _load_router_module(monkeypatch, controller)

    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)

    by_token = client.get("/api/captcha/status/sess-2?token=tok-2")
    assert by_token.status_code == 200
    assert by_token.json()["completed"] is True

    by_key = client.get(
        "/api/captcha/status/sess-2",
        headers={"X-Captcha-Control-Key": "captcha-secret"},
    )
    assert by_key.status_code == 200

    reject = client.get("/api/captcha/status/sess-2?token=nope")
    assert reject.status_code == 401


def test_websocket_accepts_session_token(monkeypatch):
    monkeypatch.setenv("CAPTCHA_CONTROL_API_KEY", "captcha-secret")
    controller = _FakeCaptchaController()
    controller.active_sessions["ws-sess"] = {
        "screenshot": "data:image/png;base64,xx",
        "captcha_info": {},
        "viewport": {"width": 400, "height": 300},
        "completed": False,
    }
    controller._tokens["ws-sess"] = "ws-token"
    module, _ = _load_router_module(monkeypatch, controller)

    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)

    with client.websocket_connect("/api/captcha/ws/ws-sess?token=ws-token") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "session_info"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/captcha/ws/ws-sess?token=bad"):
            pass
    assert exc_info.value.code == 4401


def test_admin_sessions_still_require_api_key_not_session_token(monkeypatch):
    """列表/管理接口只认全局 key，session token 不能越权。"""
    monkeypatch.setenv("CAPTCHA_CONTROL_API_KEY", "captcha-secret")
    controller = _FakeCaptchaController()
    controller._tokens["sess-x"] = "sess-token"
    module, _ = _load_router_module(monkeypatch, controller)

    app = FastAPI()
    app.include_router(module.router)
    client = TestClient(app)

    reject = client.get("/api/captcha/sessions?token=sess-token")
    assert reject.status_code == 401

    ok = client.get(
        "/api/captcha/sessions",
        headers={"X-Captcha-Control-Key": "captcha-secret"},
    )
    assert ok.status_code == 200
