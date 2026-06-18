"""Authorization matrix regressions for high-risk API boundaries."""

import time

import reply_server


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


def test_realtime_log_endpoints_are_admin_only(client, auth, user_auth):
    denied = [
        client.get("/logs", headers=user_auth),
        client.get("/logs/stats", headers=user_auth),
        client.post("/logs/clear", headers=user_auth),
    ]
    allowed = [
        client.get("/logs", headers=auth),
        client.get("/logs/stats", headers=auth),
        client.post("/logs/clear", headers=auth),
    ]

    assert [resp.status_code for resp in denied] == [403, 403, 403]
    assert [resp.status_code for resp in allowed] == [200, 200, 200]
    assert all(resp.json()["success"] is True for resp in allowed)


def test_cookie_check_counts_are_scoped_to_current_user(client, auth, user_auth):
    db_manager = reply_server.db_manager
    assert db_manager.save_cookie("admin_valid_cookie", "x" * 64, user_id=1)
    assert db_manager.save_cookie("user_valid_cookie", "y" * 64, user_id=2)
    assert db_manager.save_cookie("user_short_cookie", "short", user_id=2)
    reply_server.cookie_manager.manager.cookie_status["admin_valid_cookie"] = True
    reply_server.cookie_manager.manager.cookie_status["user_valid_cookie"] = True
    reply_server.cookie_manager.manager.cookie_status["user_short_cookie"] = False

    anonymous = client.get("/cookies/check")
    admin_resp = client.get("/cookies/check", headers=auth)
    user_resp = client.get("/cookies/check", headers=user_auth)

    assert anonymous.status_code == 200
    assert anonymous.json() == {
        "success": True,
        "hasValidCookies": False,
        "validCount": 0,
        "enabledCount": 0,
        "totalCount": 0,
    }
    assert admin_resp.status_code == 200
    assert admin_resp.json()["totalCount"] == 1
    assert admin_resp.json()["enabledCount"] == 1
    assert admin_resp.json()["validCount"] == 1
    assert admin_resp.json()["hasValidCookies"] is True
    assert user_resp.status_code == 200
    assert user_resp.json()["totalCount"] == 2
    assert user_resp.json()["enabledCount"] == 1
    assert user_resp.json()["validCount"] == 1
    assert user_resp.json()["hasValidCookies"] is True


def test_system_reload_cache_is_admin_only(client, auth, user_auth):
    class _ReloadManager:
        def __init__(self):
            self.calls = 0

        def reload_from_db(self):
            self.calls += 1
            return True

    original_manager = reply_server.cookie_manager.manager
    manager = _ReloadManager()
    reply_server.cookie_manager.manager = manager
    try:
        denied = client.post("/system/reload-cache", headers=user_auth)
        allowed = client.post("/system/reload-cache", headers=auth)
    finally:
        reply_server.cookie_manager.manager = original_manager

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["success"] is True
    assert manager.calls == 1


def test_keywords_table_debug_metadata_is_admin_only(client, auth, user_auth):
    denied = client.get("/debug/keywords-table-info", headers=user_auth)
    allowed = client.get("/debug/keywords-table-info", headers=auth)

    assert denied.status_code == 403
    assert allowed.status_code == 200
    body = allowed.json()
    assert "db_version" in body
    assert any(column["name"] == "cookie_id" for column in body["table_columns"])


class _FakeUpdateProgress:
    status = "idle"
    current_file = ""
    current_index = 0
    total_files = 0
    downloaded_bytes = 0
    total_bytes = 0
    message = ""
    error = ""


class _FakeUpdater:
    current_version = "1.2.3"

    def __init__(self):
        self.progress = _FakeUpdateProgress()
        self.cleaned_days = None
        self.saved_version = None

    async def perform_update(self):
        return {
            "success": True,
            "message": "updated",
            "updated_files": [],
            "deleted_files": [],
            "needs_restart": False,
            "new_version": self.current_version,
        }

    def get_local_file_hashes(self):
        return {"reply_server.py": "hash"}

    def cleanup_old_backups(self, keep_days=7):
        self.cleaned_days = keep_days

    def compare_file_hashes(self):
        return {"modified": [], "added": [], "deleted": []}

    def save_file_hashes(self, version):
        self.saved_version = version

    def load_file_hashes(self):
        return {
            "version": self.current_version,
            "updated_at": "2026-06-18T00:00:00",
            "total_files": 1,
            "last_updated_files": [],
            "last_updated_count": 0,
        }


def _make_auth_header(user_id, username, is_admin):
    token = f"phase76-{username}-{user_id}"
    reply_server.SESSION_TOKENS[token] = {
        "user_id": user_id,
        "username": username,
        "is_admin": is_admin,
        "timestamp": time.time(),
    }
    return {"Authorization": f"Bearer {token}"}


def test_update_management_endpoints_reject_regular_users(client, user_auth):
    endpoints = [
        ("post", "/api/update/apply"),
        ("get", "/api/update/local-hashes"),
        ("post", "/api/update/cleanup-backups"),
        ("get", "/api/update/file-changes"),
        ("post", "/api/update/save-hashes"),
        ("get", "/api/update/saved-hashes"),
        ("post", "/api/update/restart"),
    ]

    for method, path in endpoints:
        resp = getattr(client, method)(path, headers=user_auth)
        assert resp.status_code == 403, path


def test_update_management_accepts_is_admin_user_without_admin_username(client, mocker):
    fake_updater = _FakeUpdater()
    mocker.patch.object(reply_server, "get_updater", return_value=fake_updater)
    scheduled = []
    mocker.patch.object(reply_server.asyncio, "create_task", side_effect=lambda task: scheduled.append(task))
    owner_auth = _make_auth_header(77, "ops_manager", True)

    apply_resp = client.post("/api/update/apply", headers=owner_auth)
    local_hashes = client.get("/api/update/local-hashes", headers=owner_auth)
    cleanup = client.post("/api/update/cleanup-backups?days=3", headers=owner_auth)
    file_changes = client.get("/api/update/file-changes", headers=owner_auth)
    save_hashes = client.post("/api/update/save-hashes", headers=owner_auth)
    saved_hashes = client.get("/api/update/saved-hashes", headers=owner_auth)
    restart = client.post("/api/update/restart", headers=owner_auth)
    legacy_admin = client.get(
        "/api/update/local-hashes",
        headers=_make_auth_header(78, "admin", False),
    )

    assert apply_resp.status_code == 200
    assert apply_resp.json()["success"] is True
    assert local_hashes.status_code == 200
    assert local_hashes.json()["data"]["version"] == "1.2.3"
    assert cleanup.status_code == 200
    assert fake_updater.cleaned_days == 3
    assert file_changes.status_code == 200
    assert file_changes.json()["data"]["modified"] == []
    assert save_hashes.status_code == 200
    assert fake_updater.saved_version == "1.2.3"
    assert saved_hashes.status_code == 200
    assert saved_hashes.json()["data"]["version"] == "1.2.3"
    assert restart.status_code == 200
    assert restart.json()["success"] is True
    assert len(scheduled) == 1
    assert legacy_admin.status_code == 200
    scheduled[0].close()


def test_slider_verification_stats_are_scoped_to_admin_owned_cookies(client, user_auth):
    from db_manager import db_manager

    db_manager.create_user("ops_admin", "ops-admin@test.local", "ops123")
    assert db_manager.update_user_admin_status(3, True)
    assert db_manager.save_cookie("ops_slider_cookie", "unb=ops", user_id=3)
    assert db_manager.save_cookie("foreign_slider_cookie", "unb=foreign", user_id=2)
    db_manager.add_risk_control_log(
        "ops_slider_cookie",
        event_type="slider_captcha",
        processing_status="success",
        session_id="ops-success",
    )
    db_manager.add_risk_control_log(
        "foreign_slider_cookie",
        event_type="slider_captcha",
        processing_status="failed",
        session_id="foreign-failure",
    )

    ops_auth = _make_auth_header(3, "ops_admin", True)

    regular = client.get("/admin/slider-verification-stats", headers=user_auth)
    aggregate = client.get("/admin/slider-verification-stats", headers=ops_auth)
    own_cookie = client.get(
        "/admin/slider-verification-stats?cookie_id=ops_slider_cookie",
        headers=ops_auth,
    )
    foreign_cookie = client.get(
        "/admin/slider-verification-stats?cookie_id=foreign_slider_cookie",
        headers=ops_auth,
    )

    assert regular.status_code == 403
    assert aggregate.status_code == 200
    assert aggregate.json()["success"] is True
    aggregate_data = aggregate.json()["data"]
    assert aggregate_data["total_sessions"] == 1
    assert aggregate_data["success_count"] == 1
    assert aggregate_data["failure_count"] == 0
    assert aggregate_data["accounts_with_sessions"] == 1
    assert own_cookie.status_code == 200
    assert own_cookie.json()["data"]["total_sessions"] == 1
    assert foreign_cookie.status_code == 200
    foreign_data = foreign_cookie.json()["data"]
    assert foreign_data["total_sessions"] == 0
    assert foreign_data["success_count"] == 0
    assert foreign_data["failure_count"] == 0
    assert foreign_data["selected_cookie_id"] == "foreign_slider_cookie"


def test_ai_config_presets_are_scoped_to_user(client, auth, user_auth):
    admin_create = client.post(
        "/ai-config-presets",
        headers=auth,
        json={
            "preset_name": "shared-preset",
            "model_name": "admin-model",
            "api_key": "admin-secret-key",
            "base_url": "https://admin.example/v1",
            "api_type": "openai",
        },
    )
    user_create = client.post(
        "/ai-config-presets",
        headers=user_auth,
        json={
            "preset_name": "shared-preset",
            "model_name": "user-model",
            "api_key": "user-secret-key",
            "base_url": "https://user.example/v1",
            "api_type": "compatible",
        },
    )

    assert admin_create.status_code == 200
    assert user_create.status_code == 200

    admin_preset_id = admin_create.json()["preset_id"]
    user_preset_id = user_create.json()["preset_id"]
    foreign_delete = client.delete(f"/ai-config-presets/{admin_preset_id}", headers=user_auth)
    admin_list = client.get("/ai-config-presets", headers=auth)
    user_list = client.get("/ai-config-presets", headers=user_auth)
    owner_delete = client.delete(f"/ai-config-presets/{admin_preset_id}", headers=auth)
    admin_after_delete = client.get("/ai-config-presets", headers=auth)
    user_after_admin_delete = client.get("/ai-config-presets", headers=user_auth)

    assert foreign_delete.status_code == 404
    assert admin_list.status_code == 200
    assert user_list.status_code == 200
    assert [preset["id"] for preset in admin_list.json()] == [admin_preset_id]
    assert admin_list.json()[0]["api_key"] == "admin-secret-key"
    assert [preset["id"] for preset in user_list.json()] == [user_preset_id]
    assert user_list.json()[0]["api_key"] == "user-secret-key"
    assert owner_delete.status_code == 200
    assert admin_after_delete.json() == []
    assert [preset["id"] for preset in user_after_admin_delete.json()] == [user_preset_id]
