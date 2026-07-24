"""Platform confirm pending/retry state machine smoke tests."""

import reply_server


def _add_cookie(client, headers, cookie_id):
    resp = client.post(
        "/cookies",
        headers=headers,
        json={"id": cookie_id, "value": f"unb={cookie_id}; cookie2=test"},
    )
    assert resp.status_code == 200


def _insert_order(
    db,
    *,
    order_id,
    cookie_id,
    item_id="item-1",
    buyer_id="buyer-1",
    quantity=1,
    status="partial_pending_finalize",
):
    ok = db.insert_or_update_order(
        order_id=order_id,
        cookie_id=cookie_id,
        item_id=item_id,
        buyer_id=buyer_id,
        sid=f"chat-{order_id}@goofish",
        quantity=quantity,
        order_status=status,
    )
    assert ok is True


def _seed_pending_confirm(
    db,
    *,
    order_id,
    cookie_id,
    item_id="item-1",
    buyer_id="buyer-1",
    unit_index=1,
    error="自动确认发货失败: FAIL_SYS_SESSION_EXPIRED",
):
    meta = {
        "success": True,
        "delivery_message_status": "sent",
        "platform_confirm_status": "failed",
        "pending_confirm": True,
        "pending_platform_confirm": True,
        "confirm_retry_required": True,
        "confirm_error": error,
        "delivery_unit_index": unit_index,
        "cookie_id": cookie_id,
    }
    ok = db.upsert_delivery_finalization_state(
        order_id=order_id,
        unit_index=unit_index,
        cookie_id=cookie_id,
        item_id=item_id,
        buyer_id=buyer_id,
        channel="auto",
        status="sent",
        delivery_meta=meta,
        last_error=f"卡券已发出，平台确认发货失败，等待补确认: {error}",
    )
    assert ok is True
    return meta


class _FakeRuntime:
    def __init__(self, result=None):
        self.calls = []
        self._result = result or {
            "success": True,
            "processed": 1,
            "confirmed": 1,
            "failed": 0,
            "message": "补确认完成：处理 1 个，成功 1 个，失败 0 个",
            "results": [{"order_id": "pending-confirm-1", "unit_index": 1, "success": True}],
        }

    async def retry_pending_platform_confirms(self, order_id=None, source="manual", limit=50):
        self.calls.append({"order_id": order_id, "source": source, "limit": limit})
        return dict(self._result)


class _FakeCookieManager:
    def __init__(self, runtime=None):
        self.runtime = runtime

    def get_xianyu_instance(self, cid):
        return self.runtime


def test_get_pending_platform_confirm_states_filters_stopped_and_success(_db):
    db = reply_server.db_manager
    _seed_pending_confirm(db, order_id="pending-a", cookie_id="c1")
    db.upsert_delivery_finalization_state(
        order_id="stopped-b",
        unit_index=1,
        cookie_id="c1",
        item_id="item-1",
        buyer_id="buyer-1",
        channel="auto",
        status="sent",
        delivery_meta={
            "pending_platform_confirm": False,
            "confirm_retry_required": False,
            "confirm_retry_stopped": True,
            "platform_confirm_status": "not_required_terminal",
        },
        last_error="订单已处于平台终态，无需继续补确认",
    )
    db.upsert_delivery_finalization_state(
        order_id="success-c",
        unit_index=1,
        cookie_id="c1",
        item_id="item-1",
        buyer_id="buyer-1",
        channel="auto",
        status="sent",
        delivery_meta={
            "pending_platform_confirm": False,
            "confirm_retry_required": False,
            "platform_confirm_status": "success",
        },
        last_error=None,
    )

    states = db.get_pending_platform_confirm_states(cookie_id="c1", limit=20)
    order_ids = [s["order_id"] for s in states]
    assert order_ids == ["pending-a"]


def test_orders_list_enriches_pending_platform_confirm(client, user_auth):
    _add_cookie(client, user_auth, "confirm_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="pending-confirm-list",
        cookie_id="confirm_cookie",
        status="partial_pending_finalize",
    )
    _seed_pending_confirm(
        reply_server.db_manager,
        order_id="pending-confirm-list",
        cookie_id="confirm_cookie",
        error="自动确认发货失败: Session过期",
    )

    resp = client.get("/api/orders", headers=user_auth)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    order = data[0]
    assert order["order_id"] == "pending-confirm-list"
    assert order["pending_platform_confirm"] is True
    assert order["pending_confirm_units"] == 1
    assert "Session过期" in order["pending_confirm_error"]


def test_confirm_retry_rejects_missing_order(client, user_auth):
    resp = client.post("/api/orders/missing-confirm/confirm-retry", headers=user_auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["confirmed"] is False
    assert body["message"] == "订单不存在"


def test_confirm_retry_rejects_foreign_order(client, auth, user_auth):
    _add_cookie(client, auth, "admin_confirm_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="foreign-confirm",
        cookie_id="admin_confirm_cookie",
    )
    _seed_pending_confirm(
        reply_server.db_manager,
        order_id="foreign-confirm",
        cookie_id="admin_confirm_cookie",
    )

    resp = client.post("/api/orders/foreign-confirm/confirm-retry", headers=user_auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["message"] == "无权操作此订单"


def test_confirm_retry_rejects_inactive_runtime(client, user_auth):
    _add_cookie(client, user_auth, "inactive_confirm_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="inactive-confirm",
        cookie_id="inactive_confirm_cookie",
    )
    _seed_pending_confirm(
        reply_server.db_manager,
        order_id="inactive-confirm",
        cookie_id="inactive_confirm_cookie",
    )

    resp = client.post("/api/orders/inactive-confirm/confirm-retry", headers=user_auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "未运行" in body["message"]


def test_confirm_retry_no_pending_record(client, user_auth, mocker):
    _add_cookie(client, user_auth, "empty_confirm_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="no-pending-confirm",
        cookie_id="empty_confirm_cookie",
        status="shipped",
    )
    runtime = _FakeRuntime()
    mocker.patch.object(reply_server.cookie_manager, "manager", _FakeCookieManager(runtime=runtime))
    mocker.patch.object(reply_server, "publish_order_update_event")

    resp = client.post("/api/orders/no-pending-confirm/confirm-retry", headers=user_auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["confirmed"] is False
    assert "没有待补确认" in body["message"]
    assert runtime.calls == []


def test_confirm_retry_success_calls_runtime(client, user_auth, mocker):
    _add_cookie(client, user_auth, "retry_confirm_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="pending-confirm-1",
        cookie_id="retry_confirm_cookie",
        status="partial_pending_finalize",
    )
    _seed_pending_confirm(
        reply_server.db_manager,
        order_id="pending-confirm-1",
        cookie_id="retry_confirm_cookie",
    )
    runtime = _FakeRuntime()
    mocker.patch.object(reply_server.cookie_manager, "manager", _FakeCookieManager(runtime=runtime))
    mocker.patch.object(reply_server, "publish_order_update_event")

    resp = client.post("/api/orders/pending-confirm-1/confirm-retry", headers=user_auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["confirmed"] is True
    assert body["data"]["confirmed"] == 1
    assert runtime.calls == [
        {
            "order_id": "pending-confirm-1",
            "source": "manual_confirm_retry",
            "limit": 50,
        }
    ]


def test_secure_confirm_flags_session_expired_and_order_status_error():
    """Source-level contract: consign errors must short-circuit with retry flags."""
    from pathlib import Path

    src = Path("secure_confirm_decrypted.py").read_text(encoding="utf-8")
    assert 'session_expired": True' in src or '"session_expired": True' in src
    assert "need_relogin" in src
    assert "ORDER_STATUS_ERROR" in src
    assert "stop_confirm_retry" in src
    assert "confirm_retry_required" in src


def test_xianyu_async_has_platform_confirm_sm_helpers():
    from pathlib import Path

    src = Path("XianyuAutoAsync.py").read_text(encoding="utf-8")
    for symbol in (
        "retry_pending_platform_confirms",
        "_mark_delivery_pending_platform_confirm",
        "_is_platform_confirm_failure_error",
        "_is_platform_confirm_auth_error",
        "_is_non_retryable_platform_confirm_error",
        "force_confirm",
        "pending_platform_confirm_retry_cooldown",
    ):
        assert symbol in src, symbol
