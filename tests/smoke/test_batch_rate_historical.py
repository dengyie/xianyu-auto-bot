"""Historical batch-rate API: cookie scope, enable gates, merchant list + rate loop."""

import time
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

import reply_server


def _primary_user_id(db) -> int:
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
    if not row:
        db.create_user("batch_rate_owner", "batch-rate@example.com", "pass12345")
        with db.lock:
            cur = db.conn.cursor()
            cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
    return int(row[0])


def _seed_cookie(db, cookie_id: str, user_id: int):
    ok = db.save_cookie(
        cookie_id,
        f"unb={cookie_id}; _m_h5_tk=token_1; cookie2=x",
        user_id=user_id,
    )
    assert ok is True


def _auth_headers(user_id: int, username: str = "batch_rate_user") -> dict:
    token = f"batch-rate-token-{user_id}"
    reply_server.SESSION_TOKENS[token] = {
        "user_id": user_id,
        "username": username,
        "is_admin": True,
        "timestamp": time.time(),
    }
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_batch_rate_skips_disabled_and_rates_enabled(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    enabled = "br-enabled"
    disabled = "br-disabled"
    _seed_cookie(db, enabled, user_id)
    _seed_cookie(db, disabled, user_id)
    db.update_auto_comment(enabled, True)
    db.update_auto_comment(disabled, False)
    db.add_comment_template(enabled, "tpl", "很愉快的一次交易", is_active=True)

    async def _fake_list(*, cookie_string, account_id=None, page=1, page_size=20, max_retries=3):
        return {
            "success": True,
            "items": [
                {
                    "orderId": f"ord-{account_id}",
                    "itemId": "item-1",
                    "buyerId": "buyer-1",
                    "buyerNick": "nick",
                }
            ],
            "cookies_str": cookie_string,
            "message": "ok",
        }

    called = []

    async def _fake_rate_buyer(self, trade_id, feedback="不错的买家", is_retry=False):
        called.append((self.account_id, trade_id, feedback))
        return {"success": True, "message": "ok", "raw": {"ret": ["SUCCESS::调用成功"]}}

    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)

    with mock.patch("utils.rate_service.fetch_merchant_rate_list", _fake_list), mock.patch(
        "utils.rate_service.RateService.rate_buyer", _fake_rate_buyer
    ):
        resp = client.post(
            "/api/auto-comment/batch-rate",
            headers=headers,
            json={"cookie_ids": [enabled, disabled], "page_size": 10},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total_accounts"] == 2
    assert data["total_rated"] >= 1
    assert data["total_skipped"] >= 1
    assert any(item[0] == enabled and item[1] == f"ord-{enabled}" for item in called)
    assert not any(item[0] == disabled for item in called)

    order = db.get_order_by_id(f"ord-{enabled}")
    # mark_order_rated may create no order row if order not in DB; logs must exist
    logs = db.get_scheduled_rate_logs(cookie_id=enabled, limit=20)
    assert any(row["order_id"] == f"ord-{enabled}" and row["status"] == "success" for row in logs)

    disabled_logs = db.get_scheduled_rate_logs(cookie_id=disabled, limit=20)
    assert any(row["status"] == "skipped" for row in disabled_logs)


def test_batch_rate_rejects_empty_accounts(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)
    resp = client.post(
        "/api/auto-comment/batch-rate",
        headers=headers,
        json={"cookie_ids": []},
    )
    assert resp.status_code == 400


def test_batch_rate_rejects_foreign_cookie(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    assert db.create_user("br_other", "br-other@example.com", "pass12345") is True
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = ?", ("br_other",))
        other_id = int(cur.fetchone()[0])
    _seed_cookie(db, "br-foreign", other_id)
    _seed_cookie(db, "br-mine", user_id)
    db.update_auto_comment("br-mine", True)
    db.add_comment_template("br-mine", "tpl", "好评", is_active=True)

    client = TestClient(reply_server.app)
    headers = _auth_headers(user_id)
    with mock.patch(
        "utils.rate_service.fetch_merchant_rate_list",
        mock.AsyncMock(return_value={"success": True, "items": []}),
    ):
        resp = client.post(
            "/api/auto-comment/batch-rate",
            headers=headers,
            json={"cookie_ids": ["br-foreign"]},
        )
    assert resp.status_code == 200
    body = resp.json()
    # foreign cookie becomes account-level failure in details, not hard 403 for whole batch
    assert body["success"] is True
    details = body["data"]["details"]
    assert len(details) == 1
    assert details[0]["success"] is False


def test_source_has_batch_rate_contract():
    src = Path("reply_server.py").read_text(encoding="utf-8")
    assert "AutoCommentBatchRateRequest" in src
    assert "/api/auto-comment/batch-rate" in src
    assert "fetch_merchant_rate_list" in src
    assert "_extract_merchant_rate_order_id" in src
    assert "manual_history_rate_" in src
