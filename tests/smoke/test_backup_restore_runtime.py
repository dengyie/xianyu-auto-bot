"""Transactional database restore and runtime refresh regressions."""

from __future__ import annotations

import time
import sqlite3

import cookie_manager
import db_manager as db_module
import reply_server
from db_manager import DBManager


class RuntimeSpy:
    def __init__(self, fail_resume_once: bool = False):
        self.paused = 0
        self.reloaded = 0
        self.resumed = 0
        self.fail_resume_once = fail_resume_once

    async def pause_for_maintenance(self):
        self.paused += 1

    def reload_from_db(self):
        self.reloaded += 1
        return True

    async def resume_after_maintenance(self):
        self.resumed += 1
        if self.fail_resume_once and self.resumed == 1:
            raise RuntimeError("simulated runtime resume failure")


def _install_database(monkeypatch, path, username: str) -> DBManager:
    database = DBManager(str(path))
    database.create_user(username, f"{username}@test.local", "test-password")
    monkeypatch.setattr(reply_server, "db_manager", database)
    monkeypatch.setattr(db_module, "db_manager", database)
    monkeypatch.setattr(cookie_manager, "db_manager", database)
    return database


def _backup_bytes(path, username: str) -> bytes:
    backup = DBManager(str(path))
    backup.create_user(username, f"{username}@test.local", "test-password")
    backup.close()
    return path.read_bytes()


def _incomplete_sqlite_bytes(path) -> bytes:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    return path.read_bytes()


def _admin_auth(database: DBManager) -> dict[str, str]:
    admin = database.get_user_by_username("admin")
    token = "restore-admin-token"
    reply_server.SESSION_TOKENS[token] = {
        "user_id": admin["id"],
        "username": "admin",
        "is_admin": True,
        "timestamp": time.time(),
    }
    return {"Authorization": f"Bearer {token}"}


def test_restore_rejects_malformed_sqlite_without_touching_live_database(
    client, tmp_path, monkeypatch
):
    live = _install_database(monkeypatch, tmp_path / "live.db", "live_admin")
    auth = _admin_auth(live)

    response = client.post(
        "/admin/backup/upload",
        headers=auth,
        files={
            "backup_file": (
                "broken.db",
                _incomplete_sqlite_bytes(tmp_path / "incomplete.db"),
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 400
    assert live.get_user_by_username("live_admin") is not None
    assert "restore-admin-token" in reply_server.SESSION_TOKENS


def test_successful_restore_refreshes_runtime_and_revokes_tokens(
    client, tmp_path, monkeypatch
):
    live = _install_database(monkeypatch, tmp_path / "live.db", "live_admin")
    auth = _admin_auth(live)
    reply_server.DOWNLOAD_TOKENS["download-token"] = {"user_id": 1}
    runtime = RuntimeSpy()
    monkeypatch.setattr(reply_server.cookie_manager, "manager", runtime)
    payload = _backup_bytes(tmp_path / "backup.db", "restored_user")

    response = client.post(
        "/admin/backup/upload",
        headers=auth,
        files={"backup_file": ("backup.db", payload, "application/octet-stream")},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "backup_file" not in response.json()
    assert live.get_user_by_username("restored_user") is not None
    assert live.get_user_by_username("live_admin") is None
    assert runtime.paused == 1
    assert runtime.reloaded == 1
    assert runtime.resumed == 1
    assert reply_server.SESSION_TOKENS == {}
    assert reply_server.DOWNLOAD_TOKENS == {}


def test_restore_rolls_back_when_database_reinitialization_fails(
    client, tmp_path, monkeypatch
):
    live_path = tmp_path / "live.db"
    live = _install_database(monkeypatch, live_path, "live_admin")
    auth = _admin_auth(live)
    runtime = RuntimeSpy()
    monkeypatch.setattr(reply_server.cookie_manager, "manager", runtime)
    payload = _backup_bytes(tmp_path / "backup.db", "restored_user")
    reinitialize_calls = 0

    def fail_once_then_reinitialize():
        nonlocal reinitialize_calls
        reinitialize_calls += 1
        if reinitialize_calls == 1:
            raise RuntimeError("simulated post-replace failure")
        live.__init__(str(live_path))

    monkeypatch.setattr(live, "reinitialize", fail_once_then_reinitialize, raising=False)

    response = client.post(
        "/admin/backup/upload",
        headers=auth,
        files={"backup_file": ("backup.db", payload, "application/octet-stream")},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "数据库恢复失败，原数据库已恢复"
    assert live.get_user_by_username("live_admin") is not None
    assert live.get_user_by_username("restored_user") is None
    assert runtime.paused == 1
    assert runtime.reloaded == 1
    assert runtime.resumed == 1
    assert "restore-admin-token" in reply_server.SESSION_TOKENS


def test_restore_rolls_back_when_account_tasks_cannot_resume(
    client, tmp_path, monkeypatch
):
    live_path = tmp_path / "live.db"
    live = _install_database(monkeypatch, live_path, "live_admin")
    auth = _admin_auth(live)
    runtime = RuntimeSpy(fail_resume_once=True)
    monkeypatch.setattr(reply_server.cookie_manager, "manager", runtime)
    payload = _backup_bytes(tmp_path / "backup.db", "restored_user")

    response = client.post(
        "/admin/backup/upload",
        headers=auth,
        files={"backup_file": ("backup.db", payload, "application/octet-stream")},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "数据库恢复失败，原数据库已恢复"
    assert live.get_user_by_username("live_admin") is not None
    assert live.get_user_by_username("restored_user") is None
    assert runtime.reloaded == 2
    assert runtime.resumed == 2


def test_requests_are_rejected_while_restore_maintenance_is_active(client):
    reply_server.app.state.maintenance_mode = True
    try:
        response = client.get("/health")
    finally:
        reply_server.app.state.maintenance_mode = False

    assert response.status_code == 503
    assert response.json()["detail"] == "系统正在执行数据库维护"
