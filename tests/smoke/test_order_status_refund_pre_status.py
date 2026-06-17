"""Smoke tests for order status refund pre-status handling."""
import pytest
import order_status_handler


class _FakeDBManager:
    def __init__(self, order):
        self.order = dict(order)

    def get_order_by_id(self, order_id):
        if self.order.get("order_id") != order_id:
            return None
        return dict(self.order)

    def insert_or_update_order(
        self,
        order_id,
        order_status=None,
        cookie_id=None,
        pre_refund_status=...,
        clear_pre_refund_status=False,
        **_kwargs,
    ):
        if self.order.get("order_id") != order_id:
            return False

        if order_status is not None:
            self.order["order_status"] = order_status
        if cookie_id is not None:
            self.order["cookie_id"] = cookie_id

        if clear_pre_refund_status:
            self.order["pre_refund_status"] = None
        elif pre_refund_status is not ...:
            self.order["pre_refund_status"] = pre_refund_status

        return True

    def get_order_pre_refund_status(self, order_id):
        if self.order.get("order_id") != order_id:
            return None
        return self.order.get("pre_refund_status")

    def get_cookie_details(self, cookie_id):
        if self.order.get("cookie_id") != cookie_id:
            return None
        return {"user_id": 1}


class TestOrderStatusRefundPreStatus:
    """Order status refund pre-status smoke tests."""

    def test_entering_refunding_saves_previous_status(self, mocker):
        fake_db = _FakeDBManager(
            {
                "order_id": "order_enter_refunding",
                "order_status": "shipped",
                "pre_refund_status": None,
                "cookie_id": "cookie_enter_refunding",
            }
        )
        handler = order_status_handler.OrderStatusHandler()

        mocker.patch("db_manager.db_manager", fake_db)
        result = handler.update_order_status(
            order_id="order_enter_refunding",
            new_status="refunding",
            cookie_id="cookie_enter_refunding",
            context="unit test enter refunding",
        )

        assert result
        assert fake_db.order["order_status"] == "refunding"
        assert fake_db.order["pre_refund_status"] == "shipped"

    def test_regular_status_update_does_not_clear_existing_pre_refund_status(self, mocker):
        fake_db = _FakeDBManager(
            {
                "order_id": "order_keep_pre_refund_status",
                "order_status": "pending_ship",
                "pre_refund_status": "processing",
                "cookie_id": "cookie_keep_pre_refund_status",
            }
        )
        handler = order_status_handler.OrderStatusHandler()

        mocker.patch("db_manager.db_manager", fake_db)
        result = handler.update_order_status(
            order_id="order_keep_pre_refund_status",
            new_status="shipped",
            cookie_id="cookie_keep_pre_refund_status",
            context="unit test regular transition",
        )

        assert result
        assert fake_db.order["order_status"] == "shipped"
        assert fake_db.order["pre_refund_status"] == "processing"

    def test_leaving_refunding_clears_pre_refund_status(self, mocker):
        fake_db = _FakeDBManager(
            {
                "order_id": "order_clear_pre_refund_status",
                "order_status": "refunding",
                "pre_refund_status": "pending_ship",
                "cookie_id": "cookie_clear_pre_refund_status",
            }
        )
        handler = order_status_handler.OrderStatusHandler()

        mocker.patch("db_manager.db_manager", fake_db)
        result = handler.update_order_status(
            order_id="order_clear_pre_refund_status",
            new_status="completed",
            cookie_id="cookie_clear_pre_refund_status",
            context="unit test refund exit",
        )

        assert result
        assert fake_db.order["order_status"] == "completed"
        assert fake_db.order["pre_refund_status"] is None

    def test_refund_cancelled_restores_previous_status(self, mocker):
        fake_db = _FakeDBManager(
            {
                "order_id": "order_refund_cancelled_restore",
                "order_status": "refunding",
                "pre_refund_status": "pending_ship",
                "cookie_id": "cookie_refund_cancelled_restore",
            }
        )
        handler = order_status_handler.OrderStatusHandler()
        handler._order_status_history["order_refund_cancelled_restore"] = [
            {
                "from_status": "pending_ship",
                "to_status": "refunding",
                "context": "entered refunding",
                "timestamp": 1.0,
            }
        ]

        mocker.patch("db_manager.db_manager", fake_db)
        result = handler.update_order_status(
            order_id="order_refund_cancelled_restore",
            new_status="refund_cancelled",
            cookie_id="cookie_refund_cancelled_restore",
            context="unit test refund cancelled",
        )

        assert result
        assert fake_db.order["order_status"] == "pending_ship"
        assert fake_db.order["pre_refund_status"] is None
