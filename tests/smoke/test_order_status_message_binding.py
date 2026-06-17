"""Smoke tests for delayed order-status message binding."""

import order_status_handler


def _make_message(timestamp_ms, *, system=False):
    message = {"1": {"5": timestamp_ms}}
    if system:
        message["1"]["7"] = 1
    return message


class _MessageBindingDB:
    def __init__(self):
        self.orders = {}

    def get_order_by_id(self, order_id):
        order = self.orders.get(order_id)
        return dict(order) if order else None

    def insert_or_update_order(
        self,
        order_id,
        order_status=None,
        cookie_id=None,
        pre_refund_status=...,
        clear_pre_refund_status=False,
        **_kwargs,
    ):
        if order_id not in self.orders:
            return False

        if order_status is not None:
            self.orders[order_id]["order_status"] = order_status
        if cookie_id is not None:
            self.orders[order_id]["cookie_id"] = cookie_id

        if clear_pre_refund_status:
            self.orders[order_id]["pre_refund_status"] = None
        elif pre_refund_status is not ...:
            self.orders[order_id]["pre_refund_status"] = pre_refund_status

        return True

    def get_order_pre_refund_status(self, order_id):
        order = self.orders.get(order_id)
        return None if not order else order.get("pre_refund_status")

    def find_recent_orders_by_match_context(
        self,
        sid=None,
        buyer_id=None,
        item_id=None,
        cookie_id=None,
        statuses=None,
        exclude_order_id=None,
        **_kwargs,
    ):
        matched_orders = []
        allowed_statuses = set(statuses or [])
        for order in self.orders.values():
            if cookie_id is not None and order.get("cookie_id") != cookie_id:
                continue
            if exclude_order_id and order.get("order_id") == exclude_order_id:
                continue
            if sid is not None and order.get("sid") != sid:
                continue
            if buyer_id is not None and order.get("buyer_id") != buyer_id:
                continue
            if item_id is not None and order.get("item_id") != item_id:
                continue
            if allowed_statuses and order.get("order_status") not in allowed_statuses:
                continue
            matched_orders.append(dict(order))
        return matched_orders


def test_system_message_without_order_id_is_bound_after_order_id_extraction(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["bound-order"] = {
        "order_id": "bound-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-1",
        "sid": "chat-1@goofish",
        "buyer_id": "buyer-1",
        "item_id": "item-1",
    }
    handler = order_status_handler.OrderStatusHandler()

    mocker.patch("db_manager.db_manager", fake_db)

    queued = handler.handle_system_message(
        message=_make_message(1_000, system=True),
        send_message="你已发货",
        cookie_id="cookie-1",
        msg_time="10:00:00",
        match_context={
            "message_hash": 101,
            "sid": "chat-1@goofish",
            "buyer_id": "buyer-1",
            "item_id": "item-1",
            "message_timestamp_ms": 1_000,
        },
    )

    assert queued is True
    assert handler.get_pending_updates_count() == 1
    assert len(handler._pending_system_messages["cookie-1"]) == 1

    handler.on_order_id_extracted(
        order_id="bound-order",
        cookie_id="cookie-1",
        message=_make_message(1_005),
        match_context={
            "message_hash": 101,
            "sid": "chat-1@goofish",
            "buyer_id": "buyer-1",
            "item_id": "item-1",
            "message_timestamp_ms": 1_005,
        },
    )

    assert handler.get_pending_updates_count() == 0
    assert fake_db.orders["bound-order"]["order_status"] == "shipped"
    assert "cookie-1" not in handler._pending_system_messages


def test_pending_system_message_is_discarded_when_another_order_already_resolved(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["new-order"] = {
        "order_id": "new-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-2",
        "sid": "chat-2@goofish",
        "buyer_id": "buyer-2",
        "item_id": "item-2",
    }
    fake_db.orders["resolved-order"] = {
        "order_id": "resolved-order",
        "order_status": "shipped",
        "pre_refund_status": None,
        "cookie_id": "cookie-2",
        "sid": "chat-2@goofish",
        "buyer_id": "buyer-2",
        "item_id": "item-2",
    }
    handler = order_status_handler.OrderStatusHandler()
    temp_order_id = "temp_pending_cancelled"
    handler.pending_updates[temp_order_id] = [
        {
            "new_status": "shipped",
            "cookie_id": "cookie-2",
            "context": "queued shipped system message",
            "timestamp": 1.0,
        }
    ]
    handler._pending_system_messages["cookie-2"] = [
        {
            "message": _make_message(2_000, system=True),
            "send_message": "你已发货",
            "cookie_id": "cookie-2",
            "msg_time": "11:00:00",
            "new_status": "shipped",
            "temp_order_id": temp_order_id,
            "message_hash": 999,
            "sid": "chat-2@goofish",
            "buyer_id": "buyer-2",
            "item_id": "item-2",
            "message_timestamp_ms": 2_000,
            "timestamp": 1.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="new-order",
        cookie_id="cookie-2",
        message=_make_message(200_000),
        match_context={
            "message_hash": 202,
            "sid": "chat-2@goofish",
            "buyer_id": "buyer-2",
            "item_id": "item-2",
            "message_timestamp_ms": 200_000,
        },
    )

    assert fake_db.orders["new-order"]["order_status"] == "processing"
    assert handler.get_pending_updates_count() == 0
    assert "cookie-2" not in handler._pending_system_messages


def test_cancelled_red_reminder_without_order_id_directly_resolves_single_matching_order(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["resolved-directly"] = {
        "order_id": "resolved-directly",
        "order_status": "pending_ship",
        "pre_refund_status": None,
        "cookie_id": "cookie-3",
        "sid": "chat-3@goofish",
        "buyer_id": "buyer-3",
        "item_id": "item-3",
    }
    handler = order_status_handler.OrderStatusHandler()

    mocker.patch("db_manager.db_manager", fake_db)

    handled = handler.handle_red_reminder_message(
        message=_make_message(3_000),
        red_reminder="交易关闭",
        user_id="user-3",
        cookie_id="cookie-3",
        msg_time="12:00:00",
        match_context={
            "message_hash": 303,
            "sid": "chat-3@goofish",
            "buyer_id": "buyer-3",
            "item_id": "item-3",
            "message_timestamp_ms": 3_000,
        },
    )

    assert handled is True
    assert fake_db.orders["resolved-directly"]["order_status"] == "cancelled"
    assert handler.get_pending_updates_count() == 0
    assert "cookie-3" not in handler._pending_red_reminder_messages
