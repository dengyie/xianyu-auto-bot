"""Authorization matrix regressions for high-risk API boundaries."""


def test_admin_only_file_upload_rejects_anonymous(client):
    resp = client.post(
        "/api/files",
        data={"description": "anonymous upload"},
        files={"file": ("anon.txt", b"nope", "text/plain")},
    )

    assert resp.status_code in (401, 403)


def test_admin_only_file_upload_rejects_regular_user(client, user_auth):
    resp = client.post(
        "/api/files",
        headers=user_auth,
        data={"description": "regular user upload"},
        files={"file": ("user.txt", b"nope", "text/plain")},
    )

    assert resp.status_code == 403


def test_admin_only_group_management_rejects_regular_user(client, user_auth):
    resp = client.get("/api/groups", headers=user_auth)

    assert resp.status_code == 403


def test_admin_only_system_settings_rejects_regular_user(client, user_auth):
    resp = client.get("/system-settings", headers=user_auth)

    assert resp.status_code == 403


def test_admin_only_system_settings_update_rejects_regular_user(client, user_auth):
    resp = client.put(
        "/system-settings/registration_enabled",
        headers=user_auth,
        json={"value": "false", "description": "should not be allowed"},
    )

    assert resp.status_code == 403
