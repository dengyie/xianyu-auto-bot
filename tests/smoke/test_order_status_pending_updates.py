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
