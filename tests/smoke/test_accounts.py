"""Smoke tests for 闲鱼 account management (Cookie CRUD)."""
from pathlib import Path

import pytest

import reply_server


class TestAccounts:
    """Cookie / account management smoke tests."""

    def test_list_cookies_empty(self, client, auth):
        """GET /cookies returns empty list when no cookies."""
        resp = client.get("/cookies", headers=auth)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_add_cookie_success(self, client, auth):
        """POST /cookies adds a new cookie."""
        resp = client.post(
            "/cookies",
            json={"id": "cookie_test_001", "value": "unb=test; _m_h5_tk=abc123"},
            headers=auth,
        )
        assert resp.status_code == 200
        assert resp.json() == {"msg": "success"}

    def test_add_cookie_duplicate_own_ok(self, client, auth):
        """POST /cookies with duplicate id for same user succeeds (update)."""
        # Add first
        client.post(
            "/cookies",
            json={"id": "cookie_dup_001", "value": "unb=test; _m_h5_tk=abc123"},
            headers=auth,
        )
        # Add again with same id
        resp = client.post(
            "/cookies",
            json={"id": "cookie_dup_001", "value": "unb=updated; _m_h5_tk=xyz789"},
            headers=auth,
        )
        assert resp.status_code == 200

    def test_toggle_cookie_status(self, client, auth):
        """PATCH /cookies/{cid}/status toggles enabled state."""
        # Add a cookie first
        client.post(
            "/cookies",
            json={"id": "cookie_toggle_001", "value": "unb=test"},
            headers=auth,
        )
        # Disable it
        resp = client.put(
            "/cookies/cookie_toggle_001/status",
            json={"enabled": False, "pause_duration": 10},
            headers=auth,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("enabled") is False
        assert "msg" in data

    def test_cookie_details_requires_manager(self, client, auth):
        """GET /cookies/details returns empty when no cookies exist."""
        resp = client.get("/cookies/details", headers=auth)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_manual_cookie_import_requires_body(self, client, auth):
        """POST /manual-cookie-import without required fields returns 422."""
        resp = client.post("/manual-cookie-import", json={}, headers=auth)
        # FastAPI validation error
        assert resp.status_code == 422

    def test_password_login_session_is_forbidden_for_other_user(self, client, auth, user_auth):
        session_id = "password_login_owner_only_session"
        reply_server.password_login_sessions[session_id] = {
            "session_id": session_id,
            "user_id": 2,
            "account_id": "owner-cookie",
            "status": "processing",
            "timestamp": 9999999999,
            "error": None,
        }

        check = client.get(f"/password-login/check/{session_id}", headers=auth)
        assert check.status_code == 200
        assert check.json()["status"] == "forbidden"

        cancel = client.post(f"/password-login/cancel/{session_id}", headers=auth)
        assert cancel.status_code == 200
        assert cancel.json()["success"] is False
        assert cancel.json()["status"] == "forbidden"

        owner_check = client.get(f"/password-login/check/{session_id}", headers=user_auth)
        assert owner_check.status_code == 200
        assert owner_check.json()["status"] == "processing"

    def test_manual_cookie_import_session_is_forbidden_for_other_user(self, client, auth, user_auth):
        session_id = "manual_cookie_import_owner_only_session"
        reply_server.manual_cookie_import_sessions[session_id] = {
            "session_id": session_id,
            "user_id": 2,
            "account_id": "owner-cookie",
            "status": "processing",
            "timestamp": 9999999999,
            "error": None,
        }

        foreign_check = client.get(f"/manual-cookie-import/check/{session_id}", headers=auth)
        assert foreign_check.status_code == 200
        assert foreign_check.json()["status"] == "forbidden"

        owner_check = client.get(f"/manual-cookie-import/check/{session_id}", headers=user_auth)
        assert owner_check.status_code == 200
        assert owner_check.json()["status"] == "processing"

    def test_qr_login_session_is_forbidden_for_other_user(self, client, auth, user_auth, monkeypatch):
        class FakeQrSession:
            def __init__(self, session_id, user_id):
                self.session_id = session_id
                self.user_id = user_id
                self.status = "waiting"
                self.verification_url = None
                self.screenshot_path = None
                self.cookies = {}
                self.unb = None

            def is_expired(self):
                return False

        async def fake_generate_qr_code(*, user_id=None):
            session_id = "qr_login_owner_only_session"
            reply_server.qr_login_manager.sessions[session_id] = FakeQrSession(session_id, user_id)
            return {"success": True, "session_id": session_id, "qr_code_url": "data:image/png;base64,stub"}

        monkeypatch.setattr(reply_server.qr_login_manager, "generate_qr_code", fake_generate_qr_code)

        generated = client.post("/qr-login/generate", headers=user_auth)
        assert generated.status_code == 200
        session_id = generated.json()["session_id"]

        foreign_check = client.get(f"/qr-login/check/{session_id}", headers=auth)
        assert foreign_check.status_code == 200
        assert foreign_check.json()["status"] == "forbidden"

        owner_check = client.get(f"/qr-login/check/{session_id}", headers=user_auth)
        assert owner_check.status_code == 200
        assert owner_check.json()["status"] == "waiting"

    def test_qr_login_refresh_cookies_is_owner_only(self, client, other_user_auth, user_auth, monkeypatch):
        class FakeXianyuLive:
            def __init__(self, *args, **kwargs):
                pass

            async def refresh_cookies_from_qr_login(self, qr_cookies_str, cookie_id=None, user_id=None):
                return True

        import XianyuAutoAsync

        monkeypatch.setattr(XianyuAutoAsync, "XianyuLive", FakeXianyuLive)

        cookie_id = "qr_refresh_owner_only_cookie"
        reply_server.db_manager.save_cookie(cookie_id, "unb=owner; token=value", user_id=2)

        foreign_resp = client.post(
            "/qr-login/refresh-cookies",
            json={"cookie_id": cookie_id, "qr_cookies": "unb=foreign; token=value"},
            headers=other_user_auth,
        )
        assert foreign_resp.status_code == 200
        assert foreign_resp.json()["success"] is False

        owner_resp = client.post(
            "/qr-login/refresh-cookies",
            json={"cookie_id": cookie_id, "qr_cookies": "unb=owner-new; token=value"},
            headers=user_auth,
        )
        assert owner_resp.status_code == 200
        assert owner_resp.json()["success"] is True

    def test_qr_login_cooldown_routes_are_owner_only(self, client, other_user_auth, user_auth, monkeypatch):
        class FakeCooldownInstance:
            qr_cookie_refresh_cooldown = 600
            last_qr_cookie_refresh_time = 123

            def get_qr_cookie_refresh_remaining_time(self):
                return 45

            def reset_qr_cookie_refresh_flag(self):
                return None

        cookie_id = "qr_cooldown_owner_only_cookie"
        reply_server.db_manager.save_cookie(cookie_id, "unb=owner; token=value", user_id=2)

        fake_instance = FakeCooldownInstance()
        manager = reply_server.cookie_manager.manager
        monkeypatch.setattr(manager, "get_xianyu_instance", lambda cid: fake_instance if cid == cookie_id else None)

        foreign_status = client.get(f"/qr-login/cooldown-status/{cookie_id}", headers=other_user_auth)
        assert foreign_status.status_code == 200
        assert foreign_status.json()["success"] is False

        owner_status = client.get(f"/qr-login/cooldown-status/{cookie_id}", headers=user_auth)
        assert owner_status.status_code == 200
        assert owner_status.json()["success"] is True
        assert owner_status.json()["remaining_time"] == 45

        foreign_reset = client.post(f"/qr-login/reset-cooldown/{cookie_id}", headers=other_user_auth)
        assert foreign_reset.status_code == 200
        assert foreign_reset.json()["success"] is False

        owner_reset = client.post(f"/qr-login/reset-cooldown/{cookie_id}", headers=user_auth)
        assert owner_reset.status_code == 200
        assert owner_reset.json()["success"] is True
        assert owner_reset.json()["previous_remaining_time"] == 45

    def test_face_verification_screenshot_is_owner_only(self, client, other_user_auth, user_auth):
        account_id = "face_verify_owner_only_account"
        reply_server.db_manager.save_cookie(account_id, "unb=owner; token=value", user_id=2)

        screenshots_dir = Path(reply_server.static_dir) / "uploads" / "images"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshots_dir / f"face_verify_{account_id}_owner.jpg"
        screenshot_path.write_bytes(b"fake-jpg-data")

        foreign_read = client.get(f"/face-verification/screenshot/{account_id}", headers=other_user_auth)
        assert foreign_read.status_code == 200
        assert foreign_read.json()["success"] is False

        owner_read = client.get(f"/face-verification/screenshot/{account_id}", headers=user_auth)
        assert owner_read.status_code == 200
        assert owner_read.json()["success"] is True

        foreign_delete = client.delete(f"/face-verification/screenshot/{account_id}", headers=other_user_auth)
        assert foreign_delete.status_code == 200
        assert foreign_delete.json()["success"] is False

        owner_delete = client.delete(f"/face-verification/screenshot/{account_id}", headers=user_auth)
        assert owner_delete.status_code == 200
        assert owner_delete.json()["success"] is True
        assert not screenshot_path.exists()
