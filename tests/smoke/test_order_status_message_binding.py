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


def test_direct_system_message_ignores_lower_priority_status_rollback(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["stable-order"] = {
        "order_id": "stable-order",
        "order_status": "shipped",
        "pre_refund_status": None,
        "cookie_id": "cookie-3a",
        "sid": "chat-3a@goofish",
        "buyer_id": "buyer-3a",
        "item_id": "item-3a",
    }
    handler = order_status_handler.OrderStatusHandler()

    mocker.patch("db_manager.db_manager", fake_db)
    mocker.patch.object(
        handler,
        "_resolve_system_message_status",
        return_value=(
            "pending_ship",
            {"is_system_message": True},
            [{"source": "send_message", "status": "pending_ship", "text": "pending_ship"}],
        ),
    )
    mocker.patch.object(handler, "extract_order_id", return_value="stable-order")

    handled = handler.handle_system_message(
        message=_make_message(3_500, system=True),
        send_message="待发货回退信号",
        cookie_id="cookie-3a",
        msg_time="12:30:00",
        match_context={
            "message_hash": 331,
            "sid": "chat-3a@goofish",
            "buyer_id": "buyer-3a",
            "item_id": "item-3a",
            "message_timestamp_ms": 3_500,
        },
    )

    assert handled is True
    assert fake_db.orders["stable-order"]["order_status"] == "shipped"
    assert handler.get_pending_updates_count() == 0


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


def test_message_hash_plus_strong_key_binds_unique_system_message_candidate(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["hash-strong-order"] = {
        "order_id": "hash-strong-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-5a",
        "sid": "chat-5a@goofish",
        "buyer_id": "buyer-5a",
        "item_id": "item-5a",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_system_messages["cookie-5a"] = [
        {
            "message": _make_message(5_300, system=True),
            "send_message": "浣犲凡鍙戣揣",
            "cookie_id": "cookie-5a",
            "msg_time": "14:10:00",
            "new_status": "shipped",
            "temp_order_id": "temp_hash_strong_keep",
            "message_hash": 511,
            "sid": "chat-other@goofish",
            "buyer_id": "buyer-other",
            "item_id": "item-other",
            "message_timestamp_ms": 5_300,
            "timestamp": 22.0,
        },
        {
            "message": _make_message(5_301, system=True),
            "send_message": "浣犲凡鍙戣揣",
            "cookie_id": "cookie-5a",
            "msg_time": "14:10:01",
            "new_status": "shipped",
            "temp_order_id": "temp_hash_strong_bind",
            "message_hash": 511,
            "sid": "chat-5a@goofish",
            "buyer_id": "buyer-5a",
            "item_id": "item-5a",
            "message_timestamp_ms": 5_301,
            "timestamp": 23.0,
        },
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="hash-strong-order",
        cookie_id="cookie-5a",
        message=_make_message(5_400),
        match_context={
            "message_hash": 511,
            "sid": "chat-5a@goofish",
            "buyer_id": "buyer-5a",
            "item_id": "item-5a",
            "message_timestamp_ms": 5_400,
        },
    )

    assert fake_db.orders["hash-strong-order"]["order_status"] == "shipped"
    assert len(handler._pending_system_messages["cookie-5a"]) == 1
    assert handler._pending_system_messages["cookie-5a"][0]["temp_order_id"] == "temp_hash_strong_keep"


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


def test_terminal_recent_fallback_binds_single_matching_red_reminder(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["recent-red-order"] = {
        "order_id": "recent-red-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-8",
        "sid": "chat-8@goofish",
        "buyer_id": "buyer-8",
        "item_id": "item-8",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_red_reminder_messages["cookie-8"] = [
        {
            "message": _make_message(8_000),
            "red_reminder": "交易关闭",
            "user_id": "user-8",
            "cookie_id": "cookie-8",
            "msg_time": "17:00:00",
            "new_status": "cancelled",
            "temp_order_id": "temp_red_recent_single",
            "message_hash": 801,
            "sid": "chat-8@goofish",
            "buyer_id": None,
            "item_id": None,
            "message_timestamp_ms": 8_000,
            "timestamp": 50.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="recent-red-order",
        cookie_id="cookie-8",
        message=_make_message(8_400),
        match_context={
            "message_hash": 899,
            "sid": "chat-8@goofish",
            "buyer_id": "buyer-8",
            "item_id": "item-8",
            "message_timestamp_ms": 8_400,
        },
    )

    assert fake_db.orders["recent-red-order"]["order_status"] == "cancelled"
    assert "cookie-8" not in handler._pending_red_reminder_messages


def test_message_hash_plus_strong_key_binds_unique_red_reminder_candidate(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["hash-strong-red-order"] = {
        "order_id": "hash-strong-red-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-8a",
        "sid": "chat-8a@goofish",
        "buyer_id": "buyer-8a",
        "item_id": "item-8a",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_red_reminder_messages["cookie-8a"] = [
        {
            "message": _make_message(8_500),
            "red_reminder": "交易关闭",
            "user_id": "user-8a",
            "cookie_id": "cookie-8a",
            "msg_time": "17:10:00",
            "new_status": "cancelled",
            "temp_order_id": "temp_red_hash_keep",
            "message_hash": 811,
            "sid": "chat-other@goofish",
            "buyer_id": "buyer-other",
            "item_id": "item-other",
            "message_timestamp_ms": 8_500,
            "timestamp": 52.0,
        },
        {
            "message": _make_message(8_501),
            "red_reminder": "交易关闭",
            "user_id": "user-8a",
            "cookie_id": "cookie-8a",
            "msg_time": "17:10:01",
            "new_status": "cancelled",
            "temp_order_id": "temp_red_hash_bind",
            "message_hash": 811,
            "sid": "chat-8a@goofish",
            "buyer_id": "buyer-8a",
            "item_id": "item-8a",
            "message_timestamp_ms": 8_501,
            "timestamp": 53.0,
        },
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="hash-strong-red-order",
        cookie_id="cookie-8a",
        message=_make_message(8_600),
        match_context={
            "message_hash": 811,
            "sid": "chat-8a@goofish",
            "buyer_id": "buyer-8a",
            "item_id": "item-8a",
            "message_timestamp_ms": 8_600,
        },
    )

    assert fake_db.orders["hash-strong-red-order"]["order_status"] == "cancelled"
    assert len(handler._pending_red_reminder_messages["cookie-8a"]) == 1
    assert handler._pending_red_reminder_messages["cookie-8a"][0]["temp_order_id"] == "temp_red_hash_keep"


def test_terminal_recent_fallback_ambiguity_keeps_pending_red_reminder_queue(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["recent-red-order"] = {
        "order_id": "recent-red-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-9",
        "sid": "chat-9@goofish",
        "buyer_id": "buyer-9",
        "item_id": "item-9",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_red_reminder_messages["cookie-9"] = [
        {
            "message": _make_message(9_000),
            "red_reminder": "交易关闭",
            "user_id": "user-9",
            "cookie_id": "cookie-9",
            "msg_time": "18:00:00",
            "new_status": "cancelled",
            "temp_order_id": "temp_red_recent_1",
            "message_hash": 901,
            "sid": "chat-9@goofish",
            "buyer_id": None,
            "item_id": None,
            "message_timestamp_ms": 9_000,
            "timestamp": 60.0,
        },
        {
            "message": _make_message(9_100),
            "red_reminder": "交易关闭",
            "user_id": "user-9",
            "cookie_id": "cookie-9",
            "msg_time": "18:00:01",
            "new_status": "cancelled",
            "temp_order_id": "temp_red_recent_2",
            "message_hash": 902,
            "sid": "chat-9@goofish",
            "buyer_id": None,
            "item_id": None,
            "message_timestamp_ms": 9_100,
            "timestamp": 61.0,
        },
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="recent-red-order",
        cookie_id="cookie-9",
        message=_make_message(9_400),
        match_context={
            "message_hash": 999,
            "sid": "chat-9@goofish",
            "buyer_id": "buyer-9",
            "item_id": "item-9",
            "message_timestamp_ms": 9_400,
        },
    )

    assert fake_db.orders["recent-red-order"]["order_status"] == "processing"
    assert len(handler._pending_red_reminder_messages["cookie-9"]) == 2


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


def test_system_message_enqueue_cleans_stale_pending_entries_before_appending(mocker):
    fake_db = _MessageBindingDB()
    handler = order_status_handler.OrderStatusHandler()
    handler.config["max_pending_age_hours"] = 0.001
    handler.pending_updates = {
        "expired-order": [
            {"new_status": "shipped", "cookie_id": "cookie-clean-system", "context": "expired", "timestamp": 100.0}
        ],
        "fresh-order": [
            {"new_status": "shipped", "cookie_id": "cookie-clean-system", "context": "fresh", "timestamp": 198.0}
        ],
    }
    handler._pending_system_messages = {
        "cookie-clean-system": [
            {
                "message": _make_message(9_000, system=True),
                "send_message": "expired",
                "cookie_id": "cookie-clean-system",
                "msg_time": "18:00:00",
                "new_status": "shipped",
                "temp_order_id": "expired-order",
                "message_hash": 901,
                "sid": "chat-expired@goofish",
                "buyer_id": "buyer-expired",
                "item_id": "item-expired",
                "message_timestamp_ms": 9_000,
                "timestamp": 100.0,
            },
            {
                "message": _make_message(9_100, system=True),
                "send_message": "fresh",
                "cookie_id": "cookie-clean-system",
                "msg_time": "18:00:01",
                "new_status": "shipped",
                "temp_order_id": "fresh-order",
                "message_hash": 902,
                "sid": "chat-fresh@goofish",
                "buyer_id": "buyer-fresh",
                "item_id": "item-fresh",
                "message_timestamp_ms": 9_100,
                "timestamp": 198.0,
            },
        ]
    }

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
    mocker.patch("time.time", return_value=200.0)
    mocker.patch("uuid.uuid4", return_value=type("FakeUuid", (), {"hex": "abcdef1234567890"})())

    queued = handler.handle_system_message(
        message=_make_message(9_200, system=True),
        send_message="娴ｇ姴鍑￠崣鎴ｆ彛",
        cookie_id="cookie-clean-system",
        msg_time="18:00:02",
        match_context={
            "message_hash": 903,
            "sid": "chat-new@goofish",
            "buyer_id": "buyer-new",
            "item_id": "item-new",
            "message_timestamp_ms": 9_200,
        },
    )

    assert queued is True
    assert "expired-order" not in handler.pending_updates
    assert "fresh-order" in handler.pending_updates
    assert "temp_200000_abcdef12" in handler.pending_updates
    assert [msg["temp_order_id"] for msg in handler._pending_system_messages["cookie-clean-system"]] == [
        "fresh-order",
        "temp_200000_abcdef12",
    ]


def test_red_reminder_enqueue_cleans_stale_pending_entries_before_appending(mocker):
    fake_db = _MessageBindingDB()
    handler = order_status_handler.OrderStatusHandler()
    handler.config["max_pending_age_hours"] = 0.001
    handler.pending_updates = {
        "expired-red-order": [
            {"new_status": "cancelled", "cookie_id": "cookie-clean-red", "context": "expired", "timestamp": 100.0}
        ],
        "fresh-red-order": [
            {"new_status": "cancelled", "cookie_id": "cookie-clean-red", "context": "fresh", "timestamp": 198.0}
        ],
    }
    handler._pending_red_reminder_messages = {
        "cookie-clean-red": [
            {
                "message": _make_message(9_300),
                "red_reminder": "浜ゆ槗鍏抽棴",
                "user_id": "user-expired",
                "cookie_id": "cookie-clean-red",
                "msg_time": "18:10:00",
                "new_status": "cancelled",
                "temp_order_id": "expired-red-order",
                "message_hash": 911,
                "sid": "chat-expired@goofish",
                "buyer_id": "buyer-expired",
                "item_id": "item-expired",
                "message_timestamp_ms": 9_300,
                "timestamp": 100.0,
            },
            {
                "message": _make_message(9_400),
                "red_reminder": "浜ゆ槗鍏抽棴",
                "user_id": "user-fresh",
                "cookie_id": "cookie-clean-red",
                "msg_time": "18:10:01",
                "new_status": "cancelled",
                "temp_order_id": "fresh-red-order",
                "message_hash": 912,
                "sid": "chat-fresh@goofish",
                "buyer_id": "buyer-fresh",
                "item_id": "item-fresh",
                "message_timestamp_ms": 9_400,
                "timestamp": 198.0,
            },
        ]
    }

    mocker.patch("db_manager.db_manager", fake_db)
    mocker.patch.object(handler, "extract_order_id", return_value=None)
    mocker.patch("time.time", return_value=200.0)
    mocker.patch("uuid.uuid4", return_value=type("FakeUuid", (), {"hex": "12345678abcdef90"})())

    queued = handler.handle_red_reminder_message(
        message=_make_message(9_500),
        red_reminder="交易关闭",
        user_id="user-new",
        cookie_id="cookie-clean-red",
        msg_time="18:10:02",
        match_context={
            "message_hash": 913,
            "sid": "chat-new@goofish",
            "buyer_id": "buyer-new",
            "item_id": "item-new",
            "message_timestamp_ms": 9_500,
        },
    )

    assert queued is True
    assert "expired-red-order" not in handler.pending_updates
    assert "fresh-red-order" in handler.pending_updates
    assert "temp_200000_12345678" in handler.pending_updates
    assert [msg["temp_order_id"] for msg in handler._pending_red_reminder_messages["cookie-clean-red"]] == [
        "fresh-red-order",
        "temp_200000_12345678",
    ]


def test_terminal_system_message_remains_queued_when_bind_gap_is_too_large(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["gap-system-order"] = {
        "order_id": "gap-system-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-gap-system",
        "sid": "chat-gap-system@goofish",
        "buyer_id": "buyer-gap-system",
        "item_id": "item-gap-system",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_system_messages["cookie-gap-system"] = [
        {
            "message": _make_message(10_000, system=True),
            "send_message": "娴ｇ姴鍑￠崣鎴ｆ彛",
            "cookie_id": "cookie-gap-system",
            "msg_time": "19:00:00",
            "new_status": "shipped",
            "temp_order_id": "temp_gap_system",
            "message_hash": 1001,
            "sid": "chat-gap-system@goofish",
            "buyer_id": "buyer-gap-system",
            "item_id": "item-gap-system",
            "message_timestamp_ms": 10_000,
            "timestamp": 70.0,
        }
    ]
    handler.pending_updates["temp_gap_system"] = [
        {
            "new_status": "shipped",
            "cookie_id": "cookie-gap-system",
            "context": "queued shipped system message",
            "timestamp": 70.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="gap-system-order",
        cookie_id="cookie-gap-system",
        message=_make_message(200_000),
        match_context={
            "message_hash": 1001,
            "sid": "chat-gap-system@goofish",
            "buyer_id": "buyer-gap-system",
            "item_id": "item-gap-system",
            "message_timestamp_ms": 200_000,
        },
    )

    assert fake_db.orders["gap-system-order"]["order_status"] == "processing"
    assert "cookie-gap-system" in handler._pending_system_messages
    assert len(handler._pending_system_messages["cookie-gap-system"]) == 1
    assert handler._pending_system_messages["cookie-gap-system"][0]["temp_order_id"] == "temp_gap_system"
    assert "temp_gap_system" in handler.pending_updates


def test_terminal_red_reminder_remains_queued_when_bind_gap_is_too_large(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["gap-red-order"] = {
        "order_id": "gap-red-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-gap-red",
        "sid": "chat-gap-red@goofish",
        "buyer_id": "buyer-gap-red",
        "item_id": "item-gap-red",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_red_reminder_messages["cookie-gap-red"] = [
        {
            "message": _make_message(11_000),
            "red_reminder": "浜ゆ槗鍏抽棴",
            "user_id": "user-gap-red",
            "cookie_id": "cookie-gap-red",
            "msg_time": "19:10:00",
            "new_status": "cancelled",
            "temp_order_id": "temp_gap_red",
            "message_hash": 1101,
            "sid": "chat-gap-red@goofish",
            "buyer_id": "buyer-gap-red",
            "item_id": "item-gap-red",
            "message_timestamp_ms": 11_000,
            "timestamp": 71.0,
        }
    ]
    handler.pending_updates["temp_gap_red"] = [
        {
            "new_status": "cancelled",
            "cookie_id": "cookie-gap-red",
            "context": "queued cancelled red reminder",
            "timestamp": 71.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="gap-red-order",
        cookie_id="cookie-gap-red",
        message=_make_message(250_000),
        match_context={
            "message_hash": 1101,
            "sid": "chat-gap-red@goofish",
            "buyer_id": "buyer-gap-red",
            "item_id": "item-gap-red",
            "message_timestamp_ms": 250_000,
        },
    )

    assert fake_db.orders["gap-red-order"]["order_status"] == "processing"
    assert "cookie-gap-red" in handler._pending_red_reminder_messages
    assert len(handler._pending_red_reminder_messages["cookie-gap-red"]) == 1
    assert handler._pending_red_reminder_messages["cookie-gap-red"][0]["temp_order_id"] == "temp_gap_red"
    assert "temp_gap_red" in handler.pending_updates


def test_refund_cancelled_system_message_is_discarded_when_another_order_already_resolved(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["new-system-order"] = {
        "order_id": "new-system-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-system-resolved",
        "sid": "chat-system-resolved@goofish",
        "buyer_id": "buyer-system-resolved",
        "item_id": "item-system-resolved",
    }
    fake_db.orders["resolved-system-order"] = {
        "order_id": "resolved-system-order",
        "order_status": "refund_cancelled",
        "pre_refund_status": None,
        "cookie_id": "cookie-system-resolved",
        "sid": "chat-system-resolved@goofish",
        "buyer_id": "buyer-system-resolved",
        "item_id": "item-system-resolved",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_system_messages["cookie-system-resolved"] = [
        {
            "message": _make_message(12_000, system=True),
            "send_message": "refund-cancelled",
            "cookie_id": "cookie-system-resolved",
            "msg_time": "20:00:00",
            "new_status": "refund_cancelled",
            "temp_order_id": "temp_refund_cancelled_system",
            "message_hash": 1201,
            "sid": "chat-system-resolved@goofish",
            "buyer_id": "buyer-system-resolved",
            "item_id": "item-system-resolved",
            "message_timestamp_ms": 12_000,
            "timestamp": 80.0,
        }
    ]
    handler.pending_updates["temp_refund_cancelled_system"] = [
        {
            "new_status": "refund_cancelled",
            "cookie_id": "cookie-system-resolved",
            "context": "queued refund-cancelled system message",
            "timestamp": 80.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="new-system-order",
        cookie_id="cookie-system-resolved",
        message=_make_message(300_000),
        match_context={
            "message_hash": 1201,
            "sid": "chat-system-resolved@goofish",
            "buyer_id": "buyer-system-resolved",
            "item_id": "item-system-resolved",
            "message_timestamp_ms": 300_000,
        },
    )

    assert fake_db.orders["new-system-order"]["order_status"] == "processing"
    assert "cookie-system-resolved" not in handler._pending_system_messages
    assert "temp_refund_cancelled_system" not in handler.pending_updates


def test_cancelled_red_reminder_is_discarded_when_another_order_already_resolved(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["new-red-order"] = {
        "order_id": "new-red-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-red-resolved",
        "sid": "chat-red-resolved@goofish",
        "buyer_id": "buyer-red-resolved",
        "item_id": "item-red-resolved",
    }
    fake_db.orders["resolved-red-order"] = {
        "order_id": "resolved-red-order",
        "order_status": "cancelled",
        "pre_refund_status": None,
        "cookie_id": "cookie-red-resolved",
        "sid": "chat-red-resolved@goofish",
        "buyer_id": "buyer-red-resolved",
        "item_id": "item-red-resolved",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_red_reminder_messages["cookie-red-resolved"] = [
        {
            "message": _make_message(13_000),
            "red_reminder": "浜ゆ槗鍏抽棴",
            "user_id": "user-red-resolved",
            "cookie_id": "cookie-red-resolved",
            "msg_time": "20:10:00",
            "new_status": "cancelled",
            "temp_order_id": "temp_cancelled_red",
            "message_hash": 1301,
            "sid": "chat-red-resolved@goofish",
            "buyer_id": "buyer-red-resolved",
            "item_id": "item-red-resolved",
            "message_timestamp_ms": 13_000,
            "timestamp": 81.0,
        }
    ]
    handler.pending_updates["temp_cancelled_red"] = [
        {
            "new_status": "cancelled",
            "cookie_id": "cookie-red-resolved",
            "context": "queued cancelled red reminder",
            "timestamp": 81.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="new-red-order",
        cookie_id="cookie-red-resolved",
        message=_make_message(320_000),
        match_context={
            "message_hash": 1301,
            "sid": "chat-red-resolved@goofish",
            "buyer_id": "buyer-red-resolved",
            "item_id": "item-red-resolved",
            "message_timestamp_ms": 320_000,
        },
    )

    assert fake_db.orders["new-red-order"]["order_status"] == "processing"
    assert "cookie-red-resolved" not in handler._pending_red_reminder_messages
    assert "temp_cancelled_red" not in handler.pending_updates


def test_completed_system_message_is_discarded_when_another_order_already_resolved(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["new-completed-order"] = {
        "order_id": "new-completed-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-completed-resolved",
        "sid": "chat-completed-resolved@goofish",
        "buyer_id": "buyer-completed-resolved",
        "item_id": "item-completed-resolved",
    }
    fake_db.orders["resolved-completed-order"] = {
        "order_id": "resolved-completed-order",
        "order_status": "completed",
        "pre_refund_status": None,
        "cookie_id": "cookie-completed-resolved",
        "sid": "chat-completed-resolved@goofish",
        "buyer_id": "buyer-completed-resolved",
        "item_id": "item-completed-resolved",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_system_messages["cookie-completed-resolved"] = [
        {
            "message": _make_message(14_000, system=True),
            "send_message": "completed-system-message",
            "cookie_id": "cookie-completed-resolved",
            "msg_time": "20:20:00",
            "new_status": "completed",
            "temp_order_id": "temp_completed_system",
            "message_hash": 1401,
            "sid": "chat-completed-resolved@goofish",
            "buyer_id": "buyer-completed-resolved",
            "item_id": "item-completed-resolved",
            "message_timestamp_ms": 14_000,
            "timestamp": 82.0,
        }
    ]
    handler.pending_updates["temp_completed_system"] = [
        {
            "new_status": "completed",
            "cookie_id": "cookie-completed-resolved",
            "context": "queued completed system message",
            "timestamp": 82.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="new-completed-order",
        cookie_id="cookie-completed-resolved",
        message=_make_message(320_000),
        match_context={
            "message_hash": 1401,
            "sid": "chat-completed-resolved@goofish",
            "buyer_id": "buyer-completed-resolved",
            "item_id": "item-completed-resolved",
            "message_timestamp_ms": 320_000,
        },
    )

    assert fake_db.orders["new-completed-order"]["order_status"] == "processing"
    assert "cookie-completed-resolved" not in handler._pending_system_messages
    assert "temp_completed_system" not in handler.pending_updates


def test_shipped_system_message_is_discarded_when_another_order_already_resolved(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["new-shipped-order"] = {
        "order_id": "new-shipped-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "cookie-shipped-resolved",
        "sid": "chat-shipped-resolved@goofish",
        "buyer_id": "buyer-shipped-resolved",
        "item_id": "item-shipped-resolved",
    }
    fake_db.orders["resolved-shipped-order"] = {
        "order_id": "resolved-shipped-order",
        "order_status": "completed",
        "pre_refund_status": None,
        "cookie_id": "cookie-shipped-resolved",
        "sid": "chat-shipped-resolved@goofish",
        "buyer_id": "buyer-shipped-resolved",
        "item_id": "item-shipped-resolved",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler._pending_system_messages["cookie-shipped-resolved"] = [
        {
            "message": _make_message(15_000, system=True),
            "send_message": "shipped-system-message",
            "cookie_id": "cookie-shipped-resolved",
            "msg_time": "20:30:00",
            "new_status": "shipped",
            "temp_order_id": "temp_shipped_system",
            "message_hash": 1501,
            "sid": "chat-shipped-resolved@goofish",
            "buyer_id": "buyer-shipped-resolved",
            "item_id": "item-shipped-resolved",
            "message_timestamp_ms": 15_000,
            "timestamp": 83.0,
        }
    ]
    handler.pending_updates["temp_shipped_system"] = [
        {
            "new_status": "shipped",
            "cookie_id": "cookie-shipped-resolved",
            "context": "queued shipped system message",
            "timestamp": 83.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)

    handler.on_order_id_extracted(
        order_id="new-shipped-order",
        cookie_id="cookie-shipped-resolved",
        message=_make_message(330_000),
        match_context={
            "message_hash": 1501,
            "sid": "chat-shipped-resolved@goofish",
            "buyer_id": "buyer-shipped-resolved",
            "item_id": "item-shipped-resolved",
            "message_timestamp_ms": 330_000,
        },
    )

    assert fake_db.orders["new-shipped-order"]["order_status"] == "processing"
    assert "cookie-shipped-resolved" not in handler._pending_system_messages
    assert "temp_shipped_system" not in handler.pending_updates


def test_cancelled_red_reminder_queues_when_direct_backfill_update_fails(mocker):
    fake_db = _MessageBindingDB()
    fake_db.orders["resolved-fallback"] = {
        "order_id": "resolved-fallback",
        "order_status": "pending_ship",
        "pre_refund_status": None,
        "cookie_id": "cookie-fallback-red",
        "sid": "chat-fallback-red@goofish",
        "buyer_id": "buyer-fallback-red",
        "item_id": "item-fallback-red",
    }
    handler = order_status_handler.OrderStatusHandler()

    mocker.patch("db_manager.db_manager", fake_db)
    mocker.patch.object(handler, "extract_order_id", return_value=None)
    mocker.patch.object(handler, "update_order_status", return_value=False)
    mocker.patch("time.time", return_value=210.0)
    mocker.patch("uuid.uuid4", return_value=type("FakeUuid", (), {"hex": "abcdef1234567890"})())

    handled = handler.handle_red_reminder_message(
        message=_make_message(16_000),
        red_reminder="交易关闭",
        user_id="user-fallback-red",
        cookie_id="cookie-fallback-red",
        msg_time="20:40:00",
        match_context={
            "message_hash": 1601,
            "sid": "chat-fallback-red@goofish",
            "buyer_id": "buyer-fallback-red",
            "item_id": "item-fallback-red",
            "message_timestamp_ms": 16_000,
        },
    )

    assert handled is True
    assert fake_db.orders["resolved-fallback"]["order_status"] == "pending_ship"
    assert "temp_210000_abcdef12" in handler.pending_updates
    assert handler.pending_updates["temp_210000_abcdef12"][0]["new_status"] == "cancelled"
    assert [msg["temp_order_id"] for msg in handler._pending_red_reminder_messages["cookie-fallback-red"]] == [
        "temp_210000_abcdef12"
    ]
