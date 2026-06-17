"""Smoke tests for 闲鱼 account management (Cookie CRUD)."""
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
