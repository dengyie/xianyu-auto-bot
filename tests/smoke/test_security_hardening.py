"""Security hardening regressions."""
import glob
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


def test_admin_backup_management_is_admin_only_and_validates_inputs(
    client, auth, user_auth, monkeypatch
):
    monkeypatch.setattr(glob, "glob", lambda pattern: [])

    denied_download = client.get("/admin/backup/download", headers=user_auth)
    denied_list = client.get("/admin/backup/list", headers=user_auth)
    denied_upload = client.post(
        "/admin/backup/upload",
        headers=user_auth,
        files={"backup_file": ("backup.txt", b"not-db", "text/plain")},
    )
    admin_download_missing_db = client.get("/admin/backup/download", headers=auth)
    admin_list = client.get("/admin/backup/list", headers=auth)
    admin_invalid_upload = client.post(
        "/admin/backup/upload",
        headers=auth,
        files={"backup_file": ("backup.txt", b"not-db", "text/plain")},
    )

    assert denied_download.status_code == 403
    assert denied_list.status_code == 403
    assert denied_upload.status_code == 403
    assert admin_download_missing_db.status_code == 404
    assert admin_list.status_code == 200
    assert admin_list.json() == {"backups": [], "total": 0}
    assert admin_invalid_upload.status_code == 400


def test_admin_security_management_is_admin_only_and_mutates_security_state(
    client, auth, user_auth
):
    import reply_server

    original_config = dict(reply_server.BRUTE_FORCE_CONFIG)
    try:
        reply_server.login_ip_tracker.clear()
        reply_server.login_user_tracker.clear()
        reply_server.ip_blacklist.clear()
        future_time = reply_server.time.time() + 3600
        reply_server.login_ip_tracker["203.0.113.10"] = {
            "attempts": 7,
            "last_attempt": 12345,
            "blocked_until": future_time,
        }
        reply_server.login_user_tracker["locked-user"] = {
            "attempts": 6,
            "last_attempt": 12346,
            "locked_until": future_time,
        }
        reply_server.ip_blacklist.add("203.0.113.20")

        denied_stats = client.get("/admin/security/login-stats", headers=user_auth)
        denied_unblock = client.post(
            "/admin/security/unblock-ip/203.0.113.10", headers=user_auth
        )
        denied_unlock = client.post(
            "/admin/security/unlock-user/locked-user", headers=user_auth
        )
        denied_blacklist = client.post(
            "/admin/security/blacklist-ip/203.0.113.30", headers=user_auth
        )
        denied_config = client.post(
            "/admin/security/update-config",
            headers=user_auth,
            json={"ip_max_attempts": 3},
        )

        stats = client.get("/admin/security/login-stats", headers=auth)
        unblock = client.post("/admin/security/unblock-ip/203.0.113.20", headers=auth)
        unlock = client.post("/admin/security/unlock-user/locked-user", headers=auth)
        blacklist = client.post(
            "/admin/security/blacklist-ip/203.0.113.30", headers=auth
        )
        config = client.post(
            "/admin/security/update-config",
            headers=auth,
            json={
                "ip_max_attempts": 3,
                "ignored": 1,
                "user_lock_seconds": "bad",
            },
        )

        assert denied_stats.status_code == 403
        assert denied_unblock.status_code == 403
        assert denied_unlock.status_code == 403
        assert denied_blacklist.status_code == 403
        assert denied_config.status_code == 403
        assert stats.status_code == 200
        stats_data = stats.json()["data"]
        assert stats_data["blocked_ip_count"] == 1
        assert stats_data["locked_user_count"] == 1
        assert "203.0.113.20" in stats_data["blacklisted_ips"]
        assert unblock.status_code == 200
        assert unblock.json()["success"] is True
        assert "203.0.113.20" not in reply_server.ip_blacklist
        assert unlock.status_code == 200
        assert unlock.json()["success"] is True
        assert reply_server.login_user_tracker["locked-user"]["attempts"] == 0
        assert blacklist.status_code == 200
        assert blacklist.json()["success"] is True
        assert "203.0.113.30" in reply_server.ip_blacklist
        assert config.status_code == 200
        assert config.json()["success"] is True
        assert reply_server.BRUTE_FORCE_CONFIG["ip_max_attempts"] == 3
        assert "ignored" not in reply_server.BRUTE_FORCE_CONFIG
        assert reply_server.BRUTE_FORCE_CONFIG["user_lock_seconds"] == original_config[
            "user_lock_seconds"
        ]
    finally:
        reply_server.login_ip_tracker.clear()
        reply_server.login_user_tracker.clear()
        reply_server.ip_blacklist.clear()
        reply_server.BRUTE_FORCE_CONFIG.clear()
        reply_server.BRUTE_FORCE_CONFIG.update(original_config)
