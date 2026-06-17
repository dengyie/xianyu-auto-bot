"""Smoke tests for pending order status updates."""

import order_status_handler


class _PendingQueueDB:
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


def test_missing_order_status_update_is_queued(mocker):
    fake_db = _PendingQueueDB()
    handler = order_status_handler.OrderStatusHandler()

    mocker.patch("db_manager.db_manager", fake_db)
    result = handler.update_order_status(
        order_id="queued-order",
        new_status="pending_ship",
        cookie_id="queued-cookie",
        context="unit test pending queue",
    )

    assert result is False
    assert handler.get_pending_updates_count() == 1
    assert "queued-order" in handler.pending_updates
    assert handler.pending_updates["queued-order"][0]["new_status"] == "pending_ship"


def test_on_order_details_fetched_consumes_pending_updates(mocker):
    fake_db = _PendingQueueDB()
    fake_db.orders["queued-order"] = {
        "order_id": "queued-order",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "queued-cookie",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler.pending_updates["queued-order"] = [
        {
            "new_status": "pending_ship",
            "cookie_id": "queued-cookie",
            "context": "queued earlier",
            "timestamp": 1.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)
    handler.on_order_details_fetched("queued-order")

    assert handler.get_pending_updates_count() == 0
    assert fake_db.orders["queued-order"]["order_status"] == "pending_ship"


def test_on_order_details_fetched_consumes_multiple_pending_updates_in_sequence(mocker):
    fake_db = _PendingQueueDB()
    fake_db.orders["queued-order-multi"] = {
        "order_id": "queued-order-multi",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "queued-cookie-multi",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler.pending_updates["queued-order-multi"] = [
        {
            "new_status": "pending_ship",
            "cookie_id": "queued-cookie-multi",
            "context": "queued pending ship",
            "timestamp": 1.0,
        },
        {
            "new_status": "shipped",
            "cookie_id": "queued-cookie-multi",
            "context": "queued shipped",
            "timestamp": 2.0,
        },
    ]

    mocker.patch("db_manager.db_manager", fake_db)
    handler.on_order_details_fetched("queued-order-multi")

    assert handler.get_pending_updates_count() == 0
    assert "queued-order-multi" not in handler.pending_updates
    assert fake_db.orders["queued-order-multi"]["order_status"] == "shipped"


def test_process_all_pending_updates_drains_multiple_order_buckets(mocker):
    fake_db = _PendingQueueDB()
    fake_db.orders["queued-order-a"] = {
        "order_id": "queued-order-a",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "queued-cookie-a",
    }
    fake_db.orders["queued-order-b"] = {
        "order_id": "queued-order-b",
        "order_status": "processing",
        "pre_refund_status": None,
        "cookie_id": "queued-cookie-b",
    }
    handler = order_status_handler.OrderStatusHandler()
    handler.pending_updates["queued-order-a"] = [
        {
            "new_status": "pending_ship",
            "cookie_id": "queued-cookie-a",
            "context": "queued order a pending ship",
            "timestamp": 1.0,
        }
    ]
    handler.pending_updates["queued-order-b"] = [
        {
            "new_status": "shipped",
            "cookie_id": "queued-cookie-b",
            "context": "queued order b shipped",
            "timestamp": 2.0,
        }
    ]

    mocker.patch("db_manager.db_manager", fake_db)
    processed = handler.process_all_pending_updates()

    assert processed == 2
    assert handler.get_pending_updates_count() == 0
    assert fake_db.orders["queued-order-a"]["order_status"] == "pending_ship"
    assert fake_db.orders["queued-order-b"]["order_status"] == "shipped"
