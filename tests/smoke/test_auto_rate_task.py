"""Scheduled auto-rate batch: DB schema, pending query, task loop contract."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

import reply_server
from utils.auto_rate_task import rate_order_once, run_auto_rate_batch


def _recent_completed_at() -> str:
    """platform_completed_at within the query lookback window (relative to now)."""
    return (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")


def _primary_user_id(db) -> int:
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
    if not row:
        db.create_user("rate_owner", "rate@example.com", "pass12345")
        with db.lock:
            cur = db.conn.cursor()
            cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
    return int(row[0])


def _seed_cookie(db, cookie_id: str, user_id: int):
    ok = db.save_cookie(cookie_id, f"unb={cookie_id}; _m_h5_tk=token_1; cookie2=x", user_id=user_id)
    assert ok is True


def test_orders_auto_comment_schema_and_pending_query(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "rate-cookie-1"
    _seed_cookie(db, cookie_id, user_id)

    with db.lock:
        cur = db.conn.cursor()
        cur.execute("PRAGMA table_info(orders)")
        order_cols = {row[1] for row in cur.fetchall()}
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_rate_logs'")
        has_logs = cur.fetchone() is not None
    assert "is_rated" in order_cols
    assert "rated_at" in order_cols
    assert "rate_error" in order_cols
    assert has_logs is True

    assert db.insert_or_update_order(
        order_id="o-pending-1",
        item_id="item-1",
        buyer_id="buyer-1",
        buyer_nick="nick1",
        order_status="completed",
        cookie_id=cookie_id,
        platform_completed_at=_recent_completed_at(),
    ) is True

    assert db.insert_or_update_order(
        order_id="o-rated-1",
        item_id="item-2",
        buyer_id="buyer-2",
        order_status="completed",
        cookie_id=cookie_id,
        platform_completed_at=_recent_completed_at(),
    ) is True
    db.mark_order_rated("o-rated-1", True)

    pending = db.get_pending_auto_comment_orders(cookie_id, limit=10, days=10, cooldown_minutes=30)
    pending_ids = {row["order_id"] for row in pending}
    assert "o-pending-1" in pending_ids
    assert "o-rated-1" not in pending_ids

    order = db.get_order_by_id("o-rated-1")
    assert order is not None
    assert order["is_rated"] is True

    log_id = db.add_scheduled_rate_log(
        batch_id="batch-1",
        cookie_id=cookie_id,
        order_id="o-pending-1",
        status="failed",
        message="temp",
        raw_response={"ok": False},
    )
    assert log_id is not None
    logs = db.get_scheduled_rate_logs(cookie_id=cookie_id, limit=10)
    assert any(row["order_id"] == "o-pending-1" and row["status"] == "failed" for row in logs)

    # cooldown: recent failed log should hide the order
    pending_after_fail = db.get_pending_auto_comment_orders(
        cookie_id, limit=10, days=10, cooldown_minutes=30
    )
    assert "o-pending-1" not in {row["order_id"] for row in pending_after_fail}


@pytest.mark.asyncio
async def test_rate_order_once_marks_success(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "rate-cookie-2"
    _seed_cookie(db, cookie_id, user_id)
    db.update_auto_comment(cookie_id, True)
    db.add_comment_template(cookie_id, "default", "好评内容", is_active=True)

    assert db.insert_or_update_order(
        order_id="o-once-1",
        item_id="item-x",
        buyer_id="buyer-x",
        buyer_nick="buyer",
        order_status="completed",
        cookie_id=cookie_id,
    ) is True

    async def _fake_rate_buyer(self, order_id, comment):
        return {
            "success": True,
            "message": "ok",
            "already_rated": False,
            "raw": {"ret": ["SUCCESS::调用成功"]},
        }

    with mock.patch("utils.auto_rate_task.RateService.rate_buyer", _fake_rate_buyer):
        result = await rate_order_once(cookie_id, "o-once-1", batch_id="manual-batch", source="test")

    assert result["success"] is True
    assert result["status"] == "success"
    order = db.get_order_by_id("o-once-1")
    assert order["is_rated"] is True
    logs = db.get_scheduled_rate_logs(cookie_id=cookie_id)
    assert any(row["order_id"] == "o-once-1" and row["status"] == "success" for row in logs)


@pytest.mark.asyncio
async def test_run_auto_rate_batch_skips_disabled_and_rates_enabled(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)

    enabled = "rate-cookie-enabled"
    disabled = "rate-cookie-disabled"
    _seed_cookie(db, enabled, user_id)
    _seed_cookie(db, disabled, user_id)
    db.update_auto_comment(enabled, True)
    db.update_auto_comment(disabled, False)
    db.add_comment_template(enabled, "tpl", "很愉快的一次交易", is_active=True)

    for oid, cid in (("o-en-1", enabled), ("o-dis-1", disabled)):
        assert db.insert_or_update_order(
            order_id=oid,
            item_id="item",
            buyer_id="buyer",
            order_status="completed",
            cookie_id=cid,
            platform_completed_at=_recent_completed_at(),
        ) is True

    called = []

    async def _fake_rate_buyer(self, order_id, comment):
        called.append((self.account_id, order_id, comment))
        return {"success": True, "message": "ok", "raw": {}}

    with mock.patch("utils.auto_rate_task.RateService.rate_buyer", _fake_rate_buyer):
        stats = await run_auto_rate_batch(batch_limit=5, lookback_days=10, cooldown_minutes=30)

    assert stats["orders"] >= 1
    assert stats["success"] >= 1
    assert any(item[0] == enabled and item[1] == "o-en-1" for item in called)
    assert not any(item[1] == "o-dis-1" for item in called)
    assert db.get_order_by_id("o-en-1")["is_rated"] is True
    assert db.get_order_by_id("o-dis-1")["is_rated"] is False


def test_lifespan_wires_auto_rate_loop():
    src = Path("reply_server.py").read_text(encoding="utf-8")
    assert "from utils.auto_rate_task import auto_rate_task_loop" in src
    assert "auto_rate_task_loop()" in src
    assert "name=\"auto-rate-task-loop\"" in src or "name='auto-rate-task-loop'" in src
    assert "app.state.auto_rate_task" in src

    task_src = Path("utils/auto_rate_task.py").read_text(encoding="utf-8")
    assert "async def run_auto_rate_batch" in task_src
    assert "async def auto_rate_task_loop" in task_src
    assert "RateService" in task_src
    assert "get_pending_auto_comment_orders" in task_src
