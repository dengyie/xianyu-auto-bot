"""Smoke coverage for unified audit logging."""

import json

import reply_server


def test_login_failure_creates_redacted_audit_log(client):
    reply_server.db_manager.set_system_setting("login_captcha_enabled", "false")

    resp = client.post(
        "/login",
        json={"username": "admin", "password": "wrong-secret"},
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is False

    logs = reply_server.db_manager.get_audit_logs(
        category="auth",
        action="login",
        status="failed",
        limit=10,
    )
    assert logs
    details_text = json.dumps(logs[0]["details"], ensure_ascii=False)
    assert "wrong-secret" not in details_text
    assert "<redacted>" in details_text


def test_admin_can_query_audit_logs_and_regular_user_is_denied(client, auth, user_auth):
    reply_server.audit_event(
        category="admin",
        action="test_event",
        status="success",
        actor={"user_id": 1, "username": "admin", "is_admin": True},
        resource_type="test",
        resource_id="1",
        details={"token": "raw-token", "result": {"rows": 1}},
    )

    denied = client.get("/admin/audit-logs", headers=user_auth)
    allowed = client.get("/admin/audit-logs?category=admin&limit=20", headers=auth)

    assert denied.status_code == 403
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["success"] is True
    test_log = next(log for log in payload["logs"] if log["action"] == "test_event")
    assert test_log["details"]["token"] == "<redacted>"
    assert test_log["details"]["result"]["rows"] == 1


def test_request_middleware_records_user_request_outcome(client, auth):
    resp = client.get("/admin/users", headers=auth)

    assert resp.status_code == 200
    logs = reply_server.db_manager.get_audit_logs(
        category="request",
        action="http_request",
        actor_user_id=1,
        limit=20,
    )
    admin_users_log = next(log for log in logs if log["request_path"] == "/admin/users")
    assert admin_users_log["status"] == "success"
    assert admin_users_log["details"]["status_code"] == 200


def test_admin_audit_log_query_failure_is_not_reported_as_empty_success(
    client, auth, monkeypatch
):
    def fail_query(**kwargs):
        raise RuntimeError("audit store offline")

    monkeypatch.setattr(reply_server.db_manager, "get_audit_logs", fail_query)

    resp = client.get("/admin/audit-logs", headers=auth)

    assert resp.status_code == 500
    assert "审计日志查询失败" in resp.text


def test_audit_log_retention_prunes_old_rows():
    db = reply_server.db_manager
    recent_id = db.add_audit_log(
        category="admin",
        action="retention_recent",
        status="success",
        details_json=json.dumps({"case": "recent"}),
    )
    old_id = db.add_audit_log(
        category="admin",
        action="retention_old",
        status="success",
        details_json=json.dumps({"case": "old"}),
    )
    cursor = db.conn.cursor()
    cursor.execute(
        "UPDATE audit_logs SET created_at = datetime('now', '-120 days') WHERE id = ?",
        (old_id,),
    )
    db.conn.commit()

    deleted = db.cleanup_audit_logs(90)

    assert deleted >= 1
    remaining_ids = {log["id"] for log in db.get_audit_logs(limit=500)}
    assert recent_id in remaining_ids
    assert old_id not in remaining_ids
