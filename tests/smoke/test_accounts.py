"""Smoke tests for 闲鱼 account management (Cookie CRUD)."""
import asyncio
import concurrent.futures
import time
from pathlib import Path
from types import SimpleNamespace

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

    def test_account_info_cookie_update_surfaces_future_runtime_handoff_failure(self, client, auth, monkeypatch):
        cookie_id = "account_info_runtime_fail_cookie"
        reply_server.db_manager.save_cookie(cookie_id, "unb=old; token=old", user_id=1)

        class FailingManager:
            def update_cookie(self, *args, **kwargs):
                future = concurrent.futures.Future()
                future.set_exception(RuntimeError("runtime switch failed"))
                return future

        monkeypatch.setattr(reply_server.cookie_manager, "manager", FailingManager())

        resp = client.post(
            f"/cookie/{cookie_id}/account-info",
            json={"value": "unb=new; token=new"},
            headers=auth,
        )

        assert resp.status_code == 400
        assert resp.json()["detail"]

    def test_cookie_update_surfaces_future_runtime_handoff_failure(self, client, auth, monkeypatch):
        cookie_id = "cookie_runtime_update_fail"
        reply_server.db_manager.save_cookie(cookie_id, "unb=old; token=old", user_id=1)

        class FailingManager:
            def update_cookie(self, *args, **kwargs):
                future = concurrent.futures.Future()
                future.set_exception(RuntimeError("runtime switch failed"))
                return future

        monkeypatch.setattr(reply_server.cookie_manager, "manager", FailingManager())

        resp = client.put(
            f"/cookies/{cookie_id}",
            json={"id": cookie_id, "value": "unb=new; token=new"},
            headers=auth,
        )

        assert resp.status_code == 400
        assert resp.json()["detail"]

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

    def test_password_login_marks_failed_when_runtime_handoff_future_fails(self, mocker):
        session_id = "password_login_runtime_handoff_failed_session"
        account_id = "password_login_runtime_handoff_failed_account"
        reply_server.password_login_sessions[session_id] = {
            "session_id": session_id,
            "account_id": account_id,
            "account": "login-account",
            "show_browser": False,
            "refresh_mode": False,
            "risk_control_log_id": None,
            "risk_session_id": session_id,
            "status": "processing",
            "verification_url": None,
            "screenshot_path": None,
            "qr_code_url": None,
            "verification_type": None,
            "slider_instance": None,
            "task": None,
            "timestamp": time.time(),
            "completed_at": None,
            "user_id": 1,
        }

        class FakeSlider:
            last_login_error = None

            def login_with_password_playwright(self, *args, **kwargs):
                return {
                    "unb": "login-account",
                    "_m_h5_tk": "new-token",
                    "cookie2": "new-cookie2",
                }

        class FakeXianyuLive:
            def __init__(self, *args, **kwargs):
                self.cookies_str = kwargs.get("cookies_str", "")

            @classmethod
            def begin_auth_recovery_session(cls, *args, **kwargs):
                return {"started": True}

            @classmethod
            def end_auth_recovery_session(cls, *args, **kwargs):
                return None

            @classmethod
            def protected_merge_cookie_dicts(cls, existing, incoming):
                return {
                    "incoming_missing_protected_fields": [],
                    "preserved_protected_fields": [],
                    "account_switched": False,
                    "merged_cookies_dict": dict(incoming),
                    "incoming_count": len(incoming),
                    "existing_count": len(existing),
                    "merged_count": len(incoming),
                    "would_remove_fields": [],
                    "missing_required_fields": [],
                }

            def reset_qr_cookie_refresh_flag(self):
                return None

            async def _refresh_cookies_via_browser(self, *args, **kwargs):
                return False

        def failed_handoff(*args, **kwargs):
            future = concurrent.futures.Future()
            future.set_exception(RuntimeError("runtime handoff failed"))
            return future

        fake_manager = SimpleNamespace(
            cookies={},
            add_cookie=failed_handoff,
            update_cookie=failed_handoff,
        )

        mocker.patch.object(reply_server, "_load_slider_runtime", return_value=(
            lambda **kwargs: FakeSlider(),
            None,
            lambda **kwargs: SimpleNamespace(),
            SimpleNamespace(unregister_instance=lambda *args, **kwargs: True),
        ))
        import XianyuAutoAsync

        mocker.patch.object(XianyuAutoAsync, "XianyuLive", FakeXianyuLive)
        mocker.patch.object(reply_server.cookie_manager, "manager", fake_manager)
        mocker.patch.object(reply_server.db_manager, "get_cookie_details", return_value={})
        mocker.patch.object(reply_server.db_manager, "get_all_cookies", return_value={})
        mocker.patch.object(reply_server.db_manager, "update_cookie_account_info", return_value=True)
        mocker.patch.object(reply_server, "dispatch_account_notifications_sync", return_value=False)
        mocker.patch.object(reply_server, "log_with_user")

        async def invoke():
            await reply_server._execute_password_login(
                session_id=session_id,
                account_id=account_id,
                account="login-account",
                password="secret",
                show_browser=False,
                user_id=1,
                current_user={"user_id": 1, "username": "admin"},
            )

        asyncio.run(invoke())

        deadline = time.time() + 2
        while (
            reply_server.password_login_sessions[session_id]["status"] == "processing"
            and time.time() < deadline
        ):
            time.sleep(0.01)

        session = reply_server.password_login_sessions[session_id]
        assert session["status"] == "failed"
        assert "runtime handoff failed" in session["error"]

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

    def test_qr_login_refresh_cookies_surfaces_runtime_handoff_failure(
        self,
        client,
        user_auth,
        monkeypatch,
    ):
        class FakeXianyuLive:
            def __init__(self, *args, **kwargs):
                pass

            async def refresh_cookies_from_qr_login(self, qr_cookies_str, cookie_id=None, user_id=None):
                return True

        class FailingManager:
            def update_cookie(self, *args, **kwargs):
                future = concurrent.futures.Future()
                future.set_exception(RuntimeError("runtime handoff failed"))
                return future

        import XianyuAutoAsync

        monkeypatch.setattr(XianyuAutoAsync, "XianyuLive", FakeXianyuLive)
        monkeypatch.setattr(reply_server.cookie_manager, "manager", FailingManager())

        cookie_id = "qr_refresh_runtime_handoff_failed_cookie"
        reply_server.db_manager.save_cookie(cookie_id, "unb=owner; token=value", user_id=2)

        resp = client.post(
            "/qr-login/refresh-cookies",
            json={"cookie_id": cookie_id, "qr_cookies": "unb=owner-new; token=value"},
            headers=user_auth,
        )

        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "runtime handoff failed" in resp.json()["message"]

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

    def test_qr_login_processed_runtime_handoff_failure_is_error(self, client, user_auth):
        session_id = "qr_login_runtime_handoff_failed"
        reply_server.qr_check_processed[session_id] = {
            "processed": True,
            "processing": False,
            "timestamp": 9999999999,
            "account_info": {
                "account_id": "qr-runtime-failed",
                "real_cookie_refreshed": False,
                "task_restarted": False,
                "warning_message": "真实Cookie已获取，但任务管理器未初始化，未启动账号任务",
            },
        }

        resp = client.get(f"/qr-login/check/{session_id}", headers=user_auth)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "任务" in data["message"]

    @pytest.mark.asyncio
    async def test_qr_login_lite_runtime_handoff_failure_is_error(self, user_auth, monkeypatch):
        session_id = "qr_lite_runtime_handoff_failed"
        reply_server.qr_lite_sessions[session_id] = {
            "state": "confirmed",
            "qr_data_url": "data:image/png;base64,stub",
            "error_message": None,
            "account_info": None,
            "started_at": 9999999999,
            "finished": False,
            "user_id": 2,
        }

        def fake_qrcode_login_lite(**kwargs):
            return {"unb": "qr-lite-failed", "cookie2": "v"}, {"unb": "qr-lite-failed"}

        async def fake_process_qr_login_cookies(cookies, unb, current_user):
            return {
                "account_id": unb,
                "real_cookie_refreshed": False,
                "task_restarted": False,
                "warning_message": "真实Cookie已获取，但任务管理器未初始化，未启动账号任务",
            }

        monkeypatch.setattr(reply_server, "qrcode_login_lite", fake_qrcode_login_lite)
        monkeypatch.setattr(reply_server, "process_qr_login_cookies", fake_process_qr_login_cookies)

        await reply_server._run_qr_login_lite(session_id, {"user_id": 2, "username": "user"})

        state = reply_server.qr_lite_sessions[session_id]
        assert state["state"] == "error"
        assert "任务" in state["error_message"]

    @pytest.mark.asyncio
    async def test_qr_login_process_waits_for_async_runtime_handoff(self, user_auth, monkeypatch):
        class FakeXianyuLive:
            def __init__(self, *args, **kwargs):
                self.cookies_str = "unb=async-fail; token=real"

            async def refresh_cookies_from_qr_login(self, qr_cookies_str, cookie_id=None, user_id=None):
                reply_server.db_manager.update_cookie_account_info(
                    cookie_id,
                    cookie_value=self.cookies_str,
                    user_id=user_id,
                )
                return True

            @classmethod
            def mark_qr_login_grace(cls, *args, **kwargs):
                return None

            @classmethod
            def clear_qr_login_grace(cls, *args, **kwargs):
                return None

            @classmethod
            def clear_password_login_failure_backoff(cls, *args, **kwargs):
                return None

        class AsyncFailingManager:
            async def _fail_later(self):
                raise RuntimeError("runtime handoff exploded")

            def add_cookie(self, *args, **kwargs):
                return asyncio.create_task(self._fail_later())

        import XianyuAutoAsync

        monkeypatch.setattr(XianyuAutoAsync, "XianyuLive", FakeXianyuLive)
        monkeypatch.setattr(reply_server.cookie_manager, "manager", AsyncFailingManager())

        result = await reply_server.process_qr_login_cookies(
            "unb=async-fail; token=qr",
            "async-fail",
            {"user_id": 2, "username": "user"},
        )

        assert result["task_restarted"] is False
        assert "runtime handoff exploded" in result["warning_message"]

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
