"""Security hardening regressions."""
import hashlib


def test_new_passwords_use_bcrypt_hashes(client, auth):
    from reply_server import db_manager

    user = db_manager.get_user_by_username("admin")
    assert user["password_hash"].startswith("$2")
    assert user["password_hash"] != hashlib.sha256("admin123".encode()).hexdigest()


def test_legacy_sha256_password_upgrades_on_successful_login(client):
    from reply_server import db_manager

    db_manager.set_system_setting("login_captcha_enabled", "false")
    legacy_hash = hashlib.sha256("legacy-pass".encode()).hexdigest()
    cursor = db_manager.conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, email, password_hash, is_admin) VALUES (?, ?, ?, ?)",
        ("legacy", "legacy@test.local", legacy_hash, 0),
    )
    db_manager.conn.commit()

    resp = client.post("/login", json={"username": "legacy", "password": "legacy-pass"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    upgraded = db_manager.get_user_by_username("legacy")["password_hash"]
    assert upgraded != legacy_hash
    assert upgraded.startswith("$2")


def test_send_message_requires_configured_api_key(client):
    resp = client.post(
        "/send-message",
        json={
            "api_key": "admin-command",
            "cookie_id": "cid",
            "chat_id": "chat",
            "to_user_id": "buyer",
            "message": "hello",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False


def test_admin_table_data_redacts_sensitive_fields(client, auth):
    from reply_server import db_manager

    db_manager.save_cookie("acct1", "raw-cookie-secret", user_id=1)
    resp = client.get("/admin/data/cookies", headers=auth)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data
    first = data[0]
    assert first.get("value") == "***REDACTED***"

    users_resp = client.get("/admin/data/users", headers=auth)
    assert users_resp.status_code == 200
    users = users_resp.json()["data"]
    assert users
    assert users[0].get("password_hash") == "***REDACTED***"


def test_admin_table_data_management_is_admin_only_and_protects_forbidden_tables(
    client, auth, user_auth
):
    denied_read = client.get("/admin/data/users", headers=user_auth)
    denied_export = client.get("/admin/data/users/export", headers=user_auth)
    denied_clear = client.delete("/admin/data/cookies", headers=user_auth)
    disallowed_read = client.get("/admin/data/sqlite_master", headers=auth)
    protected_clear = client.delete("/admin/data/users", headers=auth)

    assert denied_read.status_code == 403
    assert denied_export.status_code == 403
    assert denied_clear.status_code == 403
    assert disallowed_read.status_code == 400
    assert protected_clear.status_code == 400
