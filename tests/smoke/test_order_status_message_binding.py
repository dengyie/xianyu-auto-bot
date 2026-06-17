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
    mocker.patch.object(
        handler,
        "_resolve_system_message_status",
        return_value=(
            "shipped",
            {"is_system_message": True},
            [{"source": "send_message", "status": "shipped", "text": "shipped"}],
        ),
    )
    mocker.patch.object(handler, "extract_order_id", return_value=None)

    queued = handler.handle_system_message(
        message=_make_message(1_000, system=True),
        send_message="浣犲凡鍙戣揣",
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
            "send_message": "浣犲凡鍙戣揣",
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
    mocker.patch.object(handler, "extract_order_id", return_value=None)

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


def test_ambiguous_message_hash_keeps_pending_system_queue_unchanged(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["candidate-order"] = {
        "order_id": "candidate-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-4",
        "sid": "chat-4@goofish",
        "buyer_id": "buyer-4",
        "item_id": "item-4",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_system_messages["cookie-4"] = [
        {
            "message": _make_message(4_000, system=True),
            "send_message": "浣犲凡鍙戣揣",
            "cookie_id": "cookie-4",
            "msg_time": "13:00:00",
            "new_status": "shipped",
            "temp_order_id": "temp_hash_1",
            "message_hash": 404,
            "sid": "chat-a@goofish",
            "buyer_id": "buyer-a",
            "item_id": "item-a",
            "message_timestamp_ms": 4_000,
            "timestamp": 10.0,
        },
        {
            "message": _make_message(4_100, system=True),
            "send_message": "浣犲凡鍙戣揣",
            "cookie_id": "cookie-4",
            "msg_time": "13:00:01",
            "new_status": "shipped",
            "temp_order_id": "temp_hash_2",
            "message_hash": 404,
            "sid": "chat-b@goofish",
            "buyer_id": "buyer-b",
            "item_id": "item-b",
            "message_timestamp_ms": 4_100,
            "timestamp": 11.0,
        },
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="candidate-order",
        cookie_id="cookie-4",
        message=_make_message(4_200),
        match_context={
            "message_hash": 404,
            "sid": "chat-4@goofish",
            "buyer_id": "buyer-4",
            "item_id": "item-4",
            "message_timestamp_ms": 4_200,
        },
    )

    assert fake_db.orders["candidate-order"]["order_status"] == "processing"
    assert len(handler._pending_system_messages["cookie-4"]) == 2


def test_ambiguous_strong_key_keeps_pending_system_queue_unchanged(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["candidate-order"] = {
        "order_id": "candidate-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-5",
        "sid": "chat-5@goofish",
        "buyer_id": "buyer-5",
        "item_id": "item-5",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_system_messages["cookie-5"] = [
        {
            "message": _make_message(5_000, system=True),
            "send_message": "浣犲凡鍙戣揣",
            "cookie_id": "cookie-5",
            "msg_time": "14:00:00",
            "new_status": "shipped",
            "temp_order_id": "temp_strong_1",
            "message_hash": 501,
            "sid": "chat-5@goofish",
            "buyer_id": "buyer-5",
            "item_id": "item-5",
            "message_timestamp_ms": 5_000,
            "timestamp": 20.0,
        },
        {
            "message": _make_message(5_100, system=True),
            "send_message": "浣犲凡鍙戣揣",
            "cookie_id": "cookie-5",
            "msg_time": "14:00:01",
            "new_status": "shipped",
            "temp_order_id": "temp_strong_2",
            "message_hash": 502,
            "sid": "chat-5@goofish",
            "buyer_id": "buyer-5",
            "item_id": "item-5",
            "message_timestamp_ms": 5_100,
            "timestamp": 21.0,
        },
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="candidate-order",
        cookie_id="cookie-5",
        message=_make_message(5_200),
        match_context={
            "message_hash": 999,
            "sid": "chat-5@goofish",
            "buyer_id": "buyer-5",
            "item_id": "item-5",
            "message_timestamp_ms": 5_200,
        },
    )

    assert fake_db.orders["candidate-order"]["order_status"] == "processing"
    assert len(handler._pending_system_messages["cookie-5"]) == 2


def test_terminal_recent_fallback_binds_single_matching_system_message(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["recent-order"] = {
        "order_id": "recent-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-6",
        "sid": "chat-6@goofish",
        "buyer_id": "buyer-6",
        "item_id": "item-6",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_system_messages["cookie-6"] = [
        {
            "message": _make_message(6_000, system=True),
            "send_message": "浣犲凡鍙戣揣",
            "cookie_id": "cookie-6",
            "msg_time": "15:00:00",
            "new_status": "shipped",
            "temp_order_id": "temp_recent_single",
            "message_hash": 601,
            "sid": "chat-6@goofish",
            "buyer_id": None,
            "item_id": None,
            "message_timestamp_ms": 6_000,
            "timestamp": 30.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="recent-order",
        cookie_id="cookie-6",
        message=_make_message(6_500),
        match_context={
            "message_hash": 699,
            "sid": "chat-6@goofish",
            "buyer_id": "buyer-6",
            "item_id": "item-6",
            "message_timestamp_ms": 6_500,
        },
    )

    assert fake_db.orders["recent-order"]["order_status"] == "shipped"
    assert "cookie-6" not in handler._pending_system_messages


def test_terminal_recent_fallback_ambiguity_keeps_pending_system_queue(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["recent-order"] = {
        "order_id": "recent-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-7",
        "sid": "chat-7@goofish",
        "buyer_id": "buyer-7",
        "item_id": "item-7",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_system_messages["cookie-7"] = [
        {
            "message": _make_message(7_000, system=True),
            "send_message": "浣犲凡鍙戣揣",
            "cookie_id": "cookie-7",
            "msg_time": "16:00:00",
            "new_status": "shipped",
            "temp_order_id": "temp_recent_1",
            "message_hash": 701,
            "sid": "chat-7@goofish",
            "buyer_id": None,
            "item_id": None,
            "message_timestamp_ms": 7_000,
            "timestamp": 40.0,
        },
        {
            "message": _make_message(7_100, system=True),
            "send_message": "浣犲凡鍙戣揣",
            "cookie_id": "cookie-7",
            "msg_time": "16:00:01",
            "new_status": "shipped",
            "temp_order_id": "temp_recent_2",
            "message_hash": 702,
            "sid": "chat-7@goofish",
            "buyer_id": None,
            "item_id": None,
            "message_timestamp_ms": 7_100,
            "timestamp": 41.0,
        },
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="recent-order",
        cookie_id="cookie-7",
        message=_make_message(7_500),
        match_context={
            "message_hash": 799,
            "sid": "chat-7@goofish",
            "buyer_id": "buyer-7",
            "item_id": "item-7",
            "message_timestamp_ms": 7_500,
        },
    )

    assert fake_db.orders["recent-order"]["order_status"] == "processing"
    assert len(handler._pending_system_messages["cookie-7"]) == 2


def test_cleanup_expired_pending_updates_removes_only_stale_entries(mocker):
    handler = order_status_handler.OrderStatusHandler()
    handler.pending_updates = {
        "expired-order": [
            {"new_status": "pending_ship", "cookie_id": "cookie-a", "context": "expired", "timestamp": 100.0}
        ],
        "fresh-order": [
            {"new_status": "pending_ship", "cookie_id": "cookie-b", "context": "fresh", "timestamp": 198.0}
        ],
    }
    handler._pending_system_messages = {
        "expired-cookie": [
            {"send_message": "expired", "new_status": "shipped", "timestamp": 100.0}
        ],
        "fresh-cookie": [
            {"send_message": "fresh", "new_status": "shipped", "timestamp": 198.0}
        ],
    }
    handler._pending_red_reminder_messages = {
        "expired-red": [
            {"red_reminder": "交易关闭", "new_status": "cancelled", "timestamp": 100.0}
        ],
        "fresh-red": [
            {"red_reminder": "交易关闭", "new_status": "cancelled", "timestamp": 198.0}
        ],
    }
    handler.config["max_pending_age_hours"] = 0.001

    mocker.patch("time.time", return_value=200.0)
    handler.clear_old_pending_updates()

    assert "expired-order" not in handler.pending_updates
    assert "fresh-order" in handler.pending_updates
    assert "expired-cookie" not in handler._pending_system_messages
    assert "fresh-cookie" in handler._pending_system_messages
    assert "expired-red" not in handler._pending_red_reminder_messages
    assert "fresh-red" in handler._pending_red_reminder_messages
