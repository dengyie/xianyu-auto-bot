"""Personal blacklist: scope match, CRUD API, message-path intercept contract."""

from pathlib import Path
from unittest import mock

import reply_server
from utils.blacklist_service import BlacklistService


def _add_cookie(client, headers, cookie_id):
    resp = client.post(
        "/cookies",
        headers=headers,
        json={"id": cookie_id, "value": f"unb={cookie_id}; cookie2=test"},
    )
    assert resp.status_code == 200


def _primary_user_id(db) -> int:
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
    if not row:
        db.create_user("bl_owner", "bl@example.com", "pass12345")
        with db.lock:
            cur = db.conn.cursor()
            cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
    return int(row[0])


def _seed_cookie(db, cookie_id: str, user_id: int):
    ok = db.save_cookie(cookie_id, f"unb={cookie_id}; cookie2=x", user_id=user_id)
    assert ok is True
    details = db.get_cookie_details(cookie_id)
    assert details and int(details.get("user_id")) == int(user_id)


def test_scope_priority_item_over_account_over_user(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    _seed_cookie(db, "bl-cookie", user_id)

    r1 = db.create_personal_blacklist(user_id=user_id, buyer_ids=["buyer-1"], reason="user-level")
    assert r1["created"] == 1
    r2 = db.create_personal_blacklist(
        user_id=user_id, buyer_ids=["buyer-1"], cookie_id="bl-cookie", reason="account-level"
    )
    assert r2["created"] == 1
    r3 = db.create_personal_blacklist(
        user_id=user_id,
        buyer_ids=["buyer-1"],
        cookie_id="bl-cookie",
        item_id="item-9",
        reason="item-level",
    )
    assert r3["created"] == 1

    hit_item = db.is_buyer_blacklisted(
        user_id=user_id, buyer_id="buyer-1", cookie_id="bl-cookie", item_id="item-9"
    )
    assert hit_item is not None
    assert hit_item["scope"] == "item"
    assert hit_item["reason"] == "item-level"

    hit_account = db.is_buyer_blacklisted(
        user_id=user_id, buyer_id="buyer-1", cookie_id="bl-cookie", item_id="item-other"
    )
    assert hit_account is not None
    assert hit_account["scope"] == "account"

    hit_user = db.is_buyer_blacklisted(
        user_id=user_id, buyer_id="buyer-1", cookie_id="other-cookie", item_id="x"
    )
    assert hit_user is not None
    assert hit_user["scope"] == "user"

    db.create_personal_blacklist(user_id=user_id, buyer_ids=["buyer-disabled"], reason="off")
    listed = db.list_personal_blacklist(user_id=user_id, buyer_id="buyer-disabled")
    rid = listed["data"][0]["id"]
    db.toggle_personal_blacklist(rid, user_id, False)
    assert db.is_buyer_blacklisted(user_id=user_id, buyer_id="buyer-disabled") is None


def test_blacklist_service_by_cookie(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    _seed_cookie(db, "svc-cookie", user_id)

    service = BlacklistService(db=db)
    created = service.create_personal(
        user_id=user_id,
        buyer_ids=["buyer-svc"],
        cookie_id="svc-cookie",
        reason="svc",
    )
    assert created["created"] == 1
    hit = service.is_buyer_blacklisted_by_cookie("svc-cookie", "buyer-svc")
    assert hit is not None
    assert hit["scope"] == "account"


def test_personal_blacklist_api_crud(client, user_auth, _db):
    _add_cookie(client, user_auth, "api-bl-cookie")

    create = client.post(
        "/api/blacklist/personal",
        headers=user_auth,
        json={
            "buyer_ids": ["api-buyer-1", "api-buyer-2"],
            "cookie_id": "api-bl-cookie",
            "reason": "spam",
            "is_enabled": True,
        },
    )
    assert create.status_code == 200, create.text
    body = create.json()
    assert body["success"] is True
    assert body["data"]["count"] == 2

    listed = client.get("/api/blacklist/personal", headers=user_auth)
    assert listed.status_code == 200
    data = listed.json()
    assert data["success"] is True
    assert data["total"] >= 2
    record_id = data["data"][0]["id"]

    toggled = client.patch(
        f"/api/blacklist/personal/{record_id}/toggle",
        headers=user_auth,
        json={"is_enabled": False},
    )
    assert toggled.status_code == 200
    assert toggled.json()["success"] is True

    deleted = client.delete(f"/api/blacklist/personal/{record_id}", headers=user_auth)
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True

    batch = client.post(
        "/api/blacklist/personal/batch-delete",
        headers=user_auth,
        json={"ids": [row["id"] for row in data["data"][1:3]]},
    )
    assert batch.status_code == 200
    assert batch.json()["success"] is True

    platform = client.get("/api/blacklist/platform", headers=user_auth)
    assert platform.status_code == 200
    assert platform.json()["success"] is True


def test_check_buyer_blacklist_helper_blocks_and_logs():
    from XianyuAutoAsync import XianyuLive

    live = object.__new__(XianyuLive)
    live.cookie_id = "c1"
    live.user_id = 7
    live._safe_str = lambda e: str(e)
    live._record_delivery_log = mock.Mock()

    hit = {
        "buyer_id": "b1",
        "scope": "account",
        "reason": "bad",
        "cookie_id": "c1",
    }
    with mock.patch("XianyuAutoAsync.db_manager") as db:
        db.is_buyer_blacklisted.return_value = hit
        result = XianyuLive._check_buyer_blacklist_for_action(
            live,
            buyer_id="b1",
            item_id="item-1",
            order_id="o1",
            buyer_nick="nick",
            action="自动发货",
            log_delivery=True,
        )
    assert result == hit
    live._record_delivery_log.assert_called_once()
    kwargs = live._record_delivery_log.call_args.kwargs
    assert kwargs["status"] == "skipped"
    assert "个人黑名单" in kwargs["reason"]
    assert kwargs["rule_meta"]["match_mode"] == "blacklist"


def test_xianyu_async_has_blacklist_intercepts():
    src = Path("XianyuAutoAsync.py").read_text(encoding="utf-8")
    assert "def _check_buyer_blacklist_for_action" in src
    assert "def _resolve_blacklist_user_id" in src
    # key call sites
    assert src.count("_check_buyer_blacklist_for_action(") >= 6
    assert "action='自动回复'" in src
    assert "action='AI回复'" in src
    assert "action='自动发货'" in src
    assert "亦凡账号确认自动发货" in src
