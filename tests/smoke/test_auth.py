"""Smoke tests for auth: login, register, password change."""
import pytest

# All tests in this class need captcha disabled
_CAPTCHA_OFF = "false"


class TestAuth:
    """Authentication smoke tests."""

    @pytest.fixture(autouse=True)
    def _disable_captcha(self, auth):
        """Disable captcha for auth tests since we can't provide one."""
        from reply_server import db_manager
        db_manager.set_system_setting("login_captcha_enabled", _CAPTCHA_OFF)

    def test_register_with_registration_enabled(self, client):
        """POST /register with valid data and registration enabled."""
        from reply_server import db_manager

        db_manager.set_system_setting("registration_enabled", "true")
        db_manager.save_verification_code("newuser@test.local", "123456")

        resp = client.post("/register", json={
            "username": "newuser",
            "email": "newuser@test.local",
            "password": "password123",
            "verification_code": "123456",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_register_duplicate_username_rejected(self, client):
        """POST /register with existing username returns error."""
        from reply_server import db_manager

        db_manager.set_system_setting("registration_enabled", "true")
        db_manager.create_user("dupeuser", "dupe@test.local", "pass123")
        db_manager.save_verification_code("dupe2@test.local", "654321")

        resp = client.post("/register", json={
            "username": "dupeuser",
            "email": "dupe2@test.local",
            "password": "password123",
            "verification_code": "654321",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "用户名已存在" in data.get("message", "")

    def test_login_correct_password(self, client, admin_token):
        """POST /login with correct credentials returns success + token."""
        resp = client.post("/login", json={
            "username": "admin",
            "password": "admin123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data.get("token") is not None

    def test_login_wrong_password_rejected(self, client, admin_token):
        """POST /login with wrong password returns failure."""
        resp = client.post("/login", json={
            "username": "admin",
            "password": "wrongpassword",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False

    def test_protected_route_requires_auth(self, client):
        """GET /api/orders without auth returns 401."""
        resp = client.get("/api/orders")
        assert resp.status_code == 401

    def test_verify_and_logout_preserve_auth_contract(self, client, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}

        verified = client.get("/verify", headers=headers)
        logged_out = client.post("/logout", headers=headers)
        verified_after_logout = client.get("/verify", headers=headers)

        assert verified.status_code == 200
        assert verified.json()["authenticated"] is True
        assert verified.json()["username"] == "admin"
        assert logged_out.json() == {"message": "已登出"}
        assert verified_after_logout.json() == {"authenticated": False}

    def test_change_password_correct_current(self, client, admin_token):
        """POST /change-admin-password with correct current password succeeds."""
        resp = client.post(
            "/change-admin-password",
            json={"current_password": "admin123", "new_password": "newpass456"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True

    def test_change_password_wrong_current(self, client, admin_token):
        """POST /change-admin-password with wrong current password fails."""
        resp = client.post(
            "/change-admin-password",
            json={"current_password": "wrong", "new_password": "newpass456"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is False

    def test_login_sets_auth_cookie_with_security_flags(self, client):
        """POST /login sets auth_token cookie with HttpOnly, SameSite=lax, and Path=/."""
        resp = client.post("/login", json={
            "username": "admin",
            "password": "admin123",
        })
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "auth_token=" in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()
        assert "path=/" in set_cookie.lower()

    def test_verify_and_protected_route_with_cookie_only(self, client):
        """Authentication succeeds using Cookie alone without Authorization header."""
        login_resp = client.post("/login", json={
            "username": "admin",
            "password": "admin123",
        })
        token = login_resp.json()["token"]

        verify_resp = client.get("/verify", cookies={"auth_token": token})
        assert verify_resp.status_code == 200
        verify_data = verify_resp.json()
        assert verify_data["authenticated"] is True
        assert verify_data["username"] == "admin"
        assert verify_data["is_admin"] is True
        assert verify_data["token"] == token

    def test_verify_with_authorization_header_does_not_echo_token(self, client):
        """Bearer callers already have the token; /verify must not copy it back into JS."""
        login_resp = client.post("/login", json={
            "username": "admin",
            "password": "admin123",
        })
        token = login_resp.json()["token"]

        verify_resp = client.get("/verify", headers={"Authorization": f"Bearer {token}"})
        assert verify_resp.status_code == 200
        verify_data = verify_resp.json()
        assert verify_data["authenticated"] is True
        assert "token" not in verify_data

    def test_empty_bearer_header_falls_back_to_cookie(self, client):
        """An empty Authorization: Bearer value must not block Cookie authentication."""
        login_resp = client.post("/login", json={
            "username": "admin",
            "password": "admin123",
        })
        token = login_resp.json()["token"]

        verify_resp = client.get(
            "/verify",
            headers={"Authorization": "Bearer "},
            cookies={"auth_token": token},
        )
        assert verify_resp.status_code == 200
        assert verify_resp.json()["authenticated"] is True

    def test_lowercase_bearer_header_authenticates(self, client):
        """Authorization scheme matching is case-insensitive for Bearer."""
        from reply_server import _token_from_authorization_header

        login_resp = client.post("/login", json={
            "username": "admin",
            "password": "admin123",
        })
        token = login_resp.json()["token"]

        assert _token_from_authorization_header(f"bearer {token}") == token
        assert _token_from_authorization_header("Bearer ") is None
        assert _token_from_authorization_header("Basic abc") is None

        verify_resp = client.get("/verify", headers={"Authorization": f"bearer {token}"})
        assert verify_resp.status_code == 200
        assert verify_resp.json()["authenticated"] is True

    def test_logout_with_cookie_clears_cookie_and_revokes_session(self, client):
        """POST /logout via Cookie deletes cookie and revokes session."""
        login_resp = client.post("/login", json={
            "username": "admin",
            "password": "admin123",
        })
        token = login_resp.json()["token"]

        logout_resp = client.post("/logout", cookies={"auth_token": token})
        assert logout_resp.status_code == 200
        logout_set_cookie = logout_resp.headers.get("set-cookie", "")
        assert "auth_token=" in logout_set_cookie
        assert "max-age=0" in logout_set_cookie.lower() or "expires=" in logout_set_cookie.lower()

        after_resp = client.get("/verify", cookies={"auth_token": token})
        assert after_resp.json()["authenticated"] is False

    def test_session_persistence_restores_from_db_after_memory_cleared(self, client):
        """Sessions survive memory wipes by rehydrating from SQLite user_sessions table."""
        from reply_server import SESSION_TOKENS

        login_resp = client.post("/login", json={
            "username": "admin",
            "password": "admin123",
        })
        token = login_resp.json()["token"]
        assert token in SESSION_TOKENS

        # 模拟服务重启或内存清理
        SESSION_TOKENS.clear()
        assert token not in SESSION_TOKENS

        # 通过 Cookie 再次访问，验证自动从 SQLite 回源恢复
        resp = client.get("/verify", cookies={"auth_token": token})
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is True
        assert resp.json()["username"] == "admin"
        # 确认已重新写回内存缓存
        assert token in SESSION_TOKENS

    def test_inactive_user_session_is_rejected_and_evicted(self, client):
        """Inactive user sessions are rejected and purged from memory and DB."""
        from reply_server import db_manager, SESSION_TOKENS

        db_manager.set_system_setting("registration_enabled", "true")
        db_manager.save_verification_code("user_test@local", "888888")
        client.post("/register", json={
            "username": "user_test",
            "email": "user_test@local",
            "password": "password123",
            "verification_code": "888888",
        })
        user = db_manager.get_user_by_username("user_test")

        login_resp = client.post("/login", json={
            "username": "user_test",
            "password": "password123",
        })
        token = login_resp.json()["token"]

        v1 = client.get("/verify", cookies={"auth_token": token})
        assert v1.json()["authenticated"] is True

        # 将用户置为禁用 (is_active = 0)
        with db_manager.lock:
            cursor = db_manager.conn.cursor()
            cursor.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user["id"],))
            db_manager.conn.commit()

        # 再次请求验证，应该拒绝访问并注销该 token
        v2 = client.get("/verify", cookies={"auth_token": token})
        assert v2.json()["authenticated"] is False
        assert token not in SESSION_TOKENS
        assert db_manager.get_user_session(token) is None

    def test_cleanup_expired_sessions(self, client):
        """cleanup_expired_sessions deletes stale sessions and keeps valid ones."""
        from reply_server import db_manager
        import time
        now = time.time()
        db_manager.save_user_session("valid_tok", 1, "admin", True, now, now + 3600)
        db_manager.save_user_session("stale_tok", 1, "admin", True, now - 7200, now - 3600)

        cleaned = db_manager.cleanup_expired_sessions()
        assert cleaned >= 1
        assert db_manager.get_user_session("valid_tok") is not None
        assert db_manager.get_user_session("stale_tok") is None

    def test_login_fails_closed_when_session_cannot_be_persisted(self, client, monkeypatch):
        """A login that cannot write user_sessions must not mint a one-shot memory token."""
        from reply_server import SESSION_TOKENS, session_service

        def boom(**_kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(session_service.session_store, "save_user_session", boom)
        before = dict(SESSION_TOKENS)

        resp = client.post("/login", json={
            "username": "admin",
            "password": "admin123",
        })
        data = resp.json()
        assert resp.status_code == 200
        assert data["success"] is False
        assert data.get("token") is None
        assert "保存失败" in data.get("message", "")
        assert SESSION_TOKENS == before
        assert "auth_token=" not in (resp.headers.get("set-cookie") or "").lower()
