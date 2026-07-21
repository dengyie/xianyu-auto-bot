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
