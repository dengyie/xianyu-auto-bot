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
