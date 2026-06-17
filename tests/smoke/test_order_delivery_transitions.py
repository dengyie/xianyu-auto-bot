"""Order delivery and refresh transition regressions."""

import reply_server


def _add_cookie(client, headers, cookie_id):
    resp = client.post(
        "/cookies",
        headers=headers,
        json={"id": cookie_id, "value": f"unb={cookie_id}; cookie2=test"},
    )
    assert resp.status_code == 200


def _insert_order(db, *, order_id, cookie_id=None, item_id="item-1", buyer_id="buyer-1", sid="chat-1@goofish", quantity=1, status="pending_ship"):
    ok = db.insert_or_update_order(
        order_id=order_id,
        cookie_id=cookie_id,
        item_id=item_id,
        buyer_id=buyer_id,
        sid=sid,
        quantity=quantity,
        order_status=status,
    )
    assert ok is True


class _FakeRuntime:
    def __init__(self, *, auto_delivery_result=None, mark_sent_result=True, finalize_result=None):
        self.sent_once = []
        self.mark_sent_calls = []
        self.release_calls = []
        self._auto_delivery_result = auto_delivery_result
        self._mark_sent_result = mark_sent_result
        self._finalize_result = finalize_result or {"success": True}

    def _summarize_delivery_progress(self, order_id, expected_quantity):
        return reply_server.db_manager.get_delivery_progress_summary(order_id, expected_quantity)

    def _get_pending_delivery_finalization_meta(self, order_id, unit_index):
        return None

    async def _auto_delivery(self, item_id, item_title=None, order_id=None, send_user_id=None, delivery_unit_index=1, **_kwargs):
        if self._auto_delivery_result is not None:
            result = dict(self._auto_delivery_result)
            result.setdefault("delivery_unit_index", delivery_unit_index)
            return result
        return {
            "success": True,
            "content": f"delivery-content-{delivery_unit_index}",
            "delivery_steps": [f"step-{delivery_unit_index}"],
            "rule_id": 101,
            "rule_keyword": "keyword",
            "card_type": "text",
            "match_mode": "keyword",
            "order_spec_mode": None,
            "rule_spec_mode": None,
            "item_config_mode": None,
            "delivery_unit_index": delivery_unit_index,
        }

    def _build_delivery_send_groups(self, prepared_units, total_units):
        return [
            {
                "mode": "single",
                "units": prepared_units,
                "delivery_steps": [f"step-{i}" for i in range(1, total_units + 1)],
            }
        ]

    async def send_delivery_steps_once(self, buyer_id, item_id, delivery_steps):
        self.sent_once.append((buyer_id, item_id, list(delivery_steps)))

    async def _send_delivery_steps(self, ws, chat_id, buyer_id, delivery_steps, log_prefix=""):
        self.sent_once.append((buyer_id, chat_id, list(delivery_steps), log_prefix))

    def _mark_data_reservation_sent_if_needed(self, rule_meta):
        self.mark_sent_calls.append(dict(rule_meta or {}))
        return self._mark_sent_result

    def _release_data_reservation_if_needed(self, rule_meta, error=None):
        self.release_calls.append({"rule_meta": dict(rule_meta or {}), "error": error})
        return True

    def _persist_delivery_finalization_state(self, order_id, item_id, buyer_id, delivery_meta, channel, status, last_error=None):
        unit_index = int((delivery_meta or {}).get("delivery_unit_index") or 1)
        return reply_server.db_manager.upsert_delivery_finalization_state(
            order_id=order_id,
            unit_index=unit_index,
            cookie_id=(delivery_meta or {}).get("cookie_id"),
            item_id=item_id,
            buyer_id=buyer_id,
            channel=channel,
            status=status,
            delivery_meta=delivery_meta,
            last_error=last_error,
        )

    async def _finalize_delivery_after_send(self, delivery_meta=None, order_id=None, item_id=None):
        return dict(self._finalize_result)

    def _sync_order_delivery_progress(self, order_id, cookie_id, expected_quantity, context=""):
        return reply_server.db_manager.get_delivery_progress_summary(order_id, expected_quantity)

    async def fetch_order_detail_info(self, order_id=None, item_id=None, buyer_id=None, sid=None, force_refresh=False):
        reply_server.db_manager.insert_or_update_order(
            order_id=order_id,
            item_id=item_id,
            buyer_id=buyer_id,
            sid=sid,
            cookie_id="refresh_cookie",
            order_status="shipped",
        )
        return {"success": True}


class _FakeCookieManager:
    def __init__(self, runtime=None):
        self.runtime = runtime

    def get_xianyu_instance(self, cid):
        return self.runtime

    def get_ws_client(self, cid):
        return None


def test_manual_deliver_rejects_missing_order(client, user_auth):
    resp = client.post("/api/orders/missing-order/deliver", headers=user_auth)

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert resp.json()["delivered"] is False


def test_manual_deliver_rejects_foreign_order(client, auth, user_auth):
    _add_cookie(client, auth, "admin_delivery_cookie")
    _insert_order(reply_server.db_manager, order_id="foreign-order", cookie_id="admin_delivery_cookie")

    resp = client.post("/api/orders/foreign-order/deliver", headers=user_auth)

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert resp.json()["message"] == "无权操作此订单"


def test_manual_deliver_rejects_inactive_account_runtime(client, user_auth):
    _add_cookie(client, user_auth, "inactive_delivery_cookie")
    _insert_order(reply_server.db_manager, order_id="inactive-order", cookie_id="inactive_delivery_cookie")

    resp = client.post("/api/orders/inactive-order/deliver", headers=user_auth)

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert "未运行" in resp.json()["message"]


def test_manual_deliver_success_records_logs_and_progress(client, user_auth, mocker):
    _add_cookie(client, user_auth, "delivery_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="deliver-success",
        cookie_id="delivery_cookie",
        item_id="item-42",
        buyer_id="buyer-42",
        quantity=1,
    )
    mocker.patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "Demo item"})
    mocker.patch.object(reply_server.cookie_manager, "manager", _FakeCookieManager(runtime=_FakeRuntime()))
    mocker.patch.object(reply_server, "publish_order_update_event")

    resp = client.post("/api/orders/deliver-success/deliver", headers=user_auth)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["delivered"] is True
    logs = reply_server.db_manager.get_recent_delivery_logs(user_id=2, limit=5)
    assert len(logs) == 1
    assert logs[0]["order_id"] == "deliver-success"
    assert logs[0]["status"] == "success"
    progress = reply_server.db_manager.get_delivery_progress_summary("deliver-success", expected_quantity=1)
    assert progress["aggregate_status"] == "shipped"
    assert progress["finalized_count"] == 1


def test_manual_deliver_marks_data_reservation_sent_after_success(client, user_auth, mocker):
    _add_cookie(client, user_auth, "data_delivery_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="deliver-data-success",
        cookie_id="data_delivery_cookie",
        item_id="item-data-42",
        buyer_id="buyer-data-42",
        quantity=1,
    )
    mocker.patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "Data Demo item"})
    runtime = _FakeRuntime(
        auto_delivery_result={
            "success": True,
            "content": "DATA-CARD-LINE-42",
            "delivery_steps": ["DATA-CARD-LINE-42"],
            "rule_id": 202,
            "rule_keyword": "data keyword",
            "card_type": "data",
            "card_id": 9202,
            "match_mode": "keyword",
            "order_spec_mode": None,
            "rule_spec_mode": None,
            "item_config_mode": None,
            "data_card_pending_consume": True,
            "data_line": "DATA-CARD-LINE-42",
            "data_reservation_id": 8202,
            "data_reservation_status": "reserved",
        },
    )
    mocker.patch.object(reply_server.cookie_manager, "manager", _FakeCookieManager(runtime=runtime))
    mocker.patch.object(reply_server, "publish_order_update_event")

    resp = client.post("/api/orders/deliver-data-success/deliver", headers=user_auth)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["delivered"] is True
    assert len(runtime.mark_sent_calls) == 1
    assert runtime.mark_sent_calls[0]["data_reservation_id"] == 8202
    assert runtime.mark_sent_calls[0]["data_line"] == "DATA-CARD-LINE-42"
    assert runtime.release_calls == []
    progress = reply_server.db_manager.get_delivery_progress_summary("deliver-data-success", expected_quantity=1)
    assert progress["aggregate_status"] == "shipped"
    assert progress["finalized_count"] == 1


def test_manual_deliver_releases_data_reservation_when_mark_sent_fails(client, user_auth, mocker):
    _add_cookie(client, user_auth, "data_delivery_mark_fail_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="deliver-data-mark-fail",
        cookie_id="data_delivery_mark_fail_cookie",
        item_id="item-data-77",
        buyer_id="buyer-data-77",
        quantity=1,
    )
    mocker.patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "Data Mark Fail item"})
    runtime = _FakeRuntime(
        auto_delivery_result={
            "success": True,
            "content": "DATA-CARD-LINE-77",
            "delivery_steps": ["DATA-CARD-LINE-77"],
            "rule_id": 203,
            "rule_keyword": "data fail keyword",
            "card_type": "data",
            "card_id": 9203,
            "match_mode": "keyword",
            "order_spec_mode": None,
            "rule_spec_mode": None,
            "item_config_mode": None,
            "data_card_pending_consume": True,
            "data_line": "DATA-CARD-LINE-77",
            "data_reservation_id": 8203,
            "data_reservation_status": "reserved",
        },
        mark_sent_result=False,
    )
    mocker.patch.object(reply_server.cookie_manager, "manager", _FakeCookieManager(runtime=runtime))
    mocker.patch.object(reply_server, "publish_order_update_event")

    resp = client.post("/api/orders/deliver-data-mark-fail/deliver", headers=user_auth)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["delivered"] is False
    assert len(runtime.mark_sent_calls) == 1
    assert len(runtime.release_calls) == 1
    assert runtime.release_calls[0]["rule_meta"]["data_reservation_id"] == 8203
    assert "标记预占已发送失败" in (runtime.release_calls[0]["error"] or "")
    logs = reply_server.db_manager.get_recent_delivery_logs(user_id=2, limit=5)
    assert logs[0]["order_id"] == "deliver-data-mark-fail"
    assert logs[0]["status"] == "failed"
    progress = reply_server.db_manager.get_delivery_progress_summary("deliver-data-mark-fail", expected_quantity=1)
    assert progress["aggregate_status"] == "pending_ship"
    assert progress["finalized_count"] == 0


def test_manual_deliver_keeps_data_reservation_unit_pending_finalize_when_finalize_fails(client, user_auth, mocker):
    _add_cookie(client, user_auth, "data_delivery_finalize_fail_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="deliver-data-finalize-fail",
        cookie_id="data_delivery_finalize_fail_cookie",
        item_id="item-data-88",
        buyer_id="buyer-data-88",
        quantity=1,
    )
    mocker.patch.object(reply_server.db_manager, "get_item_info", return_value={"item_title": "Data Finalize Fail item"})
    runtime = _FakeRuntime(
        auto_delivery_result={
            "success": True,
            "content": "DATA-CARD-LINE-88",
            "delivery_steps": ["DATA-CARD-LINE-88"],
            "rule_id": 204,
            "rule_keyword": "data finalize keyword",
            "card_type": "data",
            "card_id": 9204,
            "match_mode": "keyword",
            "order_spec_mode": None,
            "rule_spec_mode": None,
            "item_config_mode": None,
            "data_card_pending_consume": True,
            "data_line": "DATA-CARD-LINE-88",
            "data_reservation_id": 8204,
            "data_reservation_status": "reserved",
        },
        finalize_result={"success": False, "error": "finalize hook failed"},
    )
    mocker.patch.object(reply_server.cookie_manager, "manager", _FakeCookieManager(runtime=runtime))
    mocker.patch.object(reply_server, "publish_order_update_event")

    resp = client.post("/api/orders/deliver-data-finalize-fail/deliver", headers=user_auth)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["delivered"] is False
    assert len(runtime.mark_sent_calls) == 1
    assert runtime.mark_sent_calls[0]["data_reservation_id"] == 8204
    assert runtime.release_calls == []
    logs = reply_server.db_manager.get_recent_delivery_logs(user_id=2, limit=5)
    assert logs[0]["order_id"] == "deliver-data-finalize-fail"
    assert logs[0]["status"] == "failed"
    assert "finalize hook failed" in (logs[0]["reason"] or "")
    progress = reply_server.db_manager.get_delivery_progress_summary("deliver-data-finalize-fail", expected_quantity=1)
    assert progress["aggregate_status"] == "partial_pending_finalize"
    assert progress["pending_finalize_count"] == 1
    assert progress["finalized_count"] == 0


def test_refresh_rejects_foreign_order(client, auth, user_auth):
    _add_cookie(client, auth, "admin_refresh_cookie")
    _insert_order(reply_server.db_manager, order_id="foreign-refresh", cookie_id="admin_refresh_cookie")

    resp = client.post("/api/orders/foreign-refresh/refresh", headers=user_auth)

    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert resp.json()["updated"] is False
    assert resp.json()["message"] == "无权操作此订单"


def test_refresh_success_updates_order_status(client, user_auth, mocker):
    _add_cookie(client, user_auth, "refresh_cookie")
    _insert_order(
        reply_server.db_manager,
        order_id="refresh-success",
        cookie_id="refresh_cookie",
        item_id="item-99",
        buyer_id="buyer-99",
        sid="chat-99@goofish",
        status="pending_ship",
    )
    runtime = _FakeRuntime()
    mocker.patch.object(reply_server.cookie_manager, "manager", _FakeCookieManager(runtime=runtime))

    resp = client.post("/api/orders/refresh-success/refresh", headers=user_auth)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["updated"] is True
    order = reply_server.db_manager.get_order_by_id("refresh-success")
    assert order["order_status"] == "shipped"
