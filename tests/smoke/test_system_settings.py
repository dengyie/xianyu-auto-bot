"""System setting access and validation regressions."""


def test_admin_can_read_system_settings_without_password_hash(client, auth):
    from reply_server import db_manager

    db_manager.set_system_setting("admin_password_hash", "raw-secret-hash")
    db_manager.set_system_setting("registration_enabled", "true")

    resp = client.get("/system-settings", headers=auth)

    assert resp.status_code == 200
    data = resp.json()
    assert data["registration_enabled"] == "true"
    assert "admin_password_hash" not in data


def test_regular_user_cannot_read_system_settings(client, user_auth):
    resp = client.get("/system-settings", headers=user_auth)

    assert resp.status_code == 403


def test_admin_can_update_system_setting(client, auth):
    resp = client.put(
        "/system-settings/registration_enabled",
        headers=auth,
        json={"value": "false", "description": "registration gate"},
    )

    assert resp.status_code == 200
    assert resp.json()["msg"] == "system setting updated"


def test_regular_user_cannot_update_system_setting(client, user_auth):
    resp = client.put(
        "/system-settings/registration_enabled",
        headers=user_auth,
        json={"value": "false", "description": "registration gate"},
    )

    assert resp.status_code == 403


def test_admin_password_hash_cannot_be_updated_through_system_settings(client, auth):
    resp = client.put(
        "/system-settings/admin_password_hash",
        headers=auth,
        json={"value": "new-secret-hash", "description": "forbidden"},
    )

    assert resp.status_code == 400


def test_night_mode_hour_validation_rejects_invalid_values(client, auth):
    resp = client.put(
        "/system-settings/risk_control_night_start_hour",
        headers=auth,
        json={"value": "24", "description": "invalid hour"},
    )

    assert resp.status_code == 400
