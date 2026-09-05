"""Order recover by ID: API + source contract."""

import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

import reply_server


def _primary_user_id(db) -> int:
    with db.lock:
        cur = db.conn.cursor()
        cur.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        row = cur.fetchone()
    if not row:
        db.create_user("order_recover_owner", "order-recover@example.com", "pass12345")
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


def _auth_headers(user_id: int, username: str = "order_recover_user") -> dict:
    token = f"order-recover-token-{user_id}"
    reply_server.SESSION_TOKENS[token] = {
        "user_id": user_id,
        "username": username,
        "is_admin": True,
        "timestamp": time.time(),
    }
    return {"Authorization": f"Bearer {token}"}


def test_order_recover_rejects_invalid_order_id(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "recover-cookie-invalid"
    _seed_cookie(db, cookie_id, user_id)
    client = TestClient(reply_server.app)

    resp = client.post(
        "/api/orders/recover",
        headers=_auth_headers(user_id),
        json={"cookie_id": cookie_id, "order_id": "abc"},
    )
    assert resp.status_code == 400
    assert "订单ID" in (resp.json().get("detail") or "")


def test_order_recover_requires_running_instance(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "recover-cookie-stopped"
    _seed_cookie(db, cookie_id, user_id)
    client = TestClient(reply_server.app)

    with mock.patch.object(reply_server.cookie_manager, "manager", mock.Mock(get_xianyu_instance=mock.Mock(return_value=None))):
        resp = client.post(
            "/api/orders/recover",
            headers=_auth_headers(user_id),
            json={"cookie_id": cookie_id, "order_id": "1234567890123"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["recovered"] is False
    assert "未运行" in body["message"]


def test_order_recover_fetches_detail_and_auto_delivers(_db):
    db = reply_server.db_manager
    user_id = _primary_user_id(db)
    cookie_id = "recover-cookie-live"
    order_id = "1234567890123456"
    _seed_cookie(db, cookie_id, user_id)
    client = TestClient(reply_server.app)

    async def _fake_fetch(**kwargs):
        ok = db.insert_or_update_order(
            order_id=order_id,
            cookie_id=cookie_id,
            item_id="item-recover-1",
            buyer_id="buyer-recover-1",
            sid="chat-recover-1@goofish",
            quantity=1,
            order_status="pending_ship",
        )
        assert ok is True
        return {"order_id": order_id, "order_status": "pending_ship"}

    async def _fake_auto_deliver(order, fallback_order=None, source="order_recovery"):
        assert order["order_id"] == order_id
        assert source == "manual_order_recover"
        return True

    fake_instance = SimpleNamespace(
        fetch_order_detail_info=_fake_fetch,
        _auto_deliver_recovered_pending_order=_fake_auto_deliver,
    )
    fake_manager = mock.Mock()
    fake_manager.get_xianyu_instance.return_value = fake_instance

    with mock.patch.object(reply_server.cookie_manager, "manager", fake_manager), \
         mock.patch("reply_server.publish_order_update_event") as publish_mock:
        resp = client.post(
            "/api/orders/recover",
            headers=_auth_headers(user_id),
            json={
                "cookie_id": cookie_id,
                "order_id": order_id,
                "auto_deliver": True,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["recovered"] is True
    assert body["delivered"] is True
    assert body["new_status"] == "pending_ship"
    assert "自动发货" in body["message"]
    publish_mock.assert_called_once()
    stored = db.get_order_by_id(order_id)
    assert stored is not None
    assert stored["cookie_id"] == cookie_id


def test_order_recover_source_contract(api_source):
    # @router./@app. and ctx.-indirection are equivalent for this contract: the
    # split moved handlers onto domain APIRouters; normalize before asserting.
    runtime = api_source.replace("@router.", "@app.").replace("ctx.", "")
    assert "class OrderRecoverRequest" in runtime
    assert "@app.post('/api/orders/recover')" in runtime
    assert "_auto_deliver_recovered_pending_order" in runtime

    live = "\n".join([
        Path("XianyuAutoAsync.py").read_text(encoding="utf-8"),
        Path("xianyu_trading_mixins.py").read_text(encoding="utf-8"),
        Path("xianyu_messaging_mixins.py").read_text(encoding="utf-8"),
        Path("xianyu_auth_recovery.py").read_text(encoding="utf-8"),
    ])
    assert "async def _auto_deliver_recovered_pending_order" in live
    assert "async def _send_recovered_delivery_without_sid" in live

    handler = Path("order_status_handler.py").read_text(encoding="utf-8")
    assert "'pending_payment': ['pending_ship', 'cancelled']" in handler
    assert "'pending_payment': '待付款'" in handler

    html = Path("static/index.html").read_text(encoding="utf-8")
    assert "orderRecoverModal" in html
    assert "openOrderRecoverModal()" in html

    orders_js = Path("static/js/app-orders.js").read_text(encoding="utf-8")
    assert "async function openOrderRecoverModal" in orders_js
    assert "async function recoverOrderById" in orders_js
    assert "/api/orders/recover" in orders_js
