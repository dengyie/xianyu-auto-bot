"""Unified task log center: schema, write/read, and API merge contract."""

import time
from pathlib import Path

from fastapi.testclient import TestClient

import reply_server


def _primary_user_id(db) -> int:
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
    if not row:
        db.create_user("tasklog_owner", "tasklog@example.com", "pass12345")
        with db.lock:
            cur = db.conn.cursor()
            cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
    return int(row[0])


def _seed_cookie(db, cookie_id: str, user_id: int):
    ok = db.save_cookie(cookie_id, f"unb={cookie_id}; _m_h5_tk=token_1; cookie2=x", user_id=user_id)
    assert ok is True


def _auth_headers(user_id: int, username: str = "tasklog_user", is_admin: bool = True) -> dict:
    token = f"tasklog-token-{user_id}-{username}"
    reply_server.SESSION_TOKENS[token] = {
        "user_id": user_id,
        "username": username,
        "is_admin": is_admin,
        "timestamp": time.time(),
    }
    return {"Authorization": f"Bearer {token}"}


def test_scheduled_task_logs_schema_and_crud(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "tasklog-cookie-1"
    _seed_cookie(db, cookie_id, user_id)

    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_task_logs'")
        assert cur.fetchone() is not None
        cur.execute("PRAGMA table_info(scheduled_task_logs)")
        cols = {row[1] for row in cur.fetchall()}
    assert {"batch_id", "task_type", "cookie_id", "status", "message"}.issubset(cols)

    log_id = db.add_scheduled_task_log(
        batch_id="batch-tl-1",
        task_type="item_polish",
        cookie_id=cookie_id,
        object_id="item-99",
        item_id="item-99",
        status="success",
        message="擦亮成功",
        raw_response={"ok": True},
    )
    assert log_id is not None

    logs = db.get_scheduled_task_logs(user_id=user_id, cookie_id=cookie_id, limit=20)
    assert any(row["task_type"] == "item_polish" and row["status"] == "success" for row in logs)

    filtered = db.get_scheduled_task_logs(user_id=user_id, task_type="item_polish", limit=20)
    assert all(row["task_type"] == "item_polish" for row in filtered)
    assert any(row["cookie_id"] == cookie_id for row in filtered)


def test_task_logs_api_merges_rate_and_generic(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "tasklog-cookie-2"
    _seed_cookie(db, cookie_id, user_id)

    db.add_scheduled_rate_log(
        batch_id="rate-b1",
        cookie_id=cookie_id,
        order_id="order-r1",
        status="success",
        message="好评完成",
        comment="很愉快",
    )
    db.add_scheduled_task_log(
        batch_id="gen-b1",
        task_type="cookie_refresh",
        cookie_id=cookie_id,
        object_id="sess-1",
        status="failed",
        message="刷新失败",
    )

    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)

    resp = client.get("/api/task-logs", headers=headers, params={"cookie_id": cookie_id, "limit": 50})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    types = {row["task_type"] for row in body["data"]}
    assert "auto_comment" in types
    assert "cookie_refresh" in types
    assert all("task_label" in row for row in body["data"])

    only_rate = client.get(
        "/api/task-logs",
        headers=headers,
        params={"cookie_id": cookie_id, "task_type": "auto_comment", "limit": 50},
    )
    assert only_rate.status_code == 200
    rate_body = only_rate.json()
    assert rate_body["success"] is True
    assert all(row["task_type"] == "auto_comment" for row in rate_body["data"])

    auto_logs = client.get(
        "/api/auto-comment/logs",
        headers=headers,
        params={"cookie_id": cookie_id, "limit": 20},
    )
    assert auto_logs.status_code == 200
    auto_body = auto_logs.json()
    assert auto_body["success"] is True
    assert any(row["order_id"] == "order-r1" for row in auto_body["data"])


def test_task_logs_api_rejects_foreign_cookie(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    assert db.create_user("tasklog_other", "other-tl@example.com", "pass12345") is True
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = ?", ("tasklog_other",))
        row = cur.fetchone()
    other_id = int(row[0])
    _seed_cookie(db, "foreign-cookie", other_id)
    _seed_cookie(db, "mine-cookie", user_id)

    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)
    resp = client.get("/api/task-logs", headers=headers, params={"cookie_id": "foreign-cookie"})
    assert resp.status_code in {403, 400, 404}


def test_source_has_task_log_contract(api_source):
    src = api_source.replace("@router.", "@app.").replace("ctx.", "")
    assert "TASK_LOG_TYPE_LABELS" in src
    assert "@app.get('/api/task-logs')" in src or '@app.get("/api/task-logs")' in src
    assert "@app.get('/api/auto-comment/logs')" in src or '@app.get("/api/auto-comment/logs")' in src
    assert "get_scheduled_task_logs" in src
    assert "未接入求小红花" in src or "跳过 auto_red_flower" in src

    base = Path("db_manager/base.py").read_text(encoding="utf-8")
    orders = Path("db_manager/orders.py").read_text(encoding="utf-8")
    assert "_ensure_scheduled_task_logs_table" in base
    assert "def add_scheduled_task_log" in orders
    assert "def get_scheduled_task_logs" in orders
