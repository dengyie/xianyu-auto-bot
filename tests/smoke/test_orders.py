"""Smoke tests for order management."""
import pytest


class TestOrders:
    """Order smoke tests."""

    def test_list_orders_empty(self, client, auth):
        """GET /api/orders returns empty data when no cookies exist."""
        resp = client.get("/api/orders", headers=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert data.get("data") == []

    def test_list_orders_with_cookie_no_orders(self, client, auth):
        """GET /api/orders with a cookie attached returns empty list."""
        from reply_server import db_manager
        db_manager.save_cookie("order_cookie_01", "unb=test", user_id=1)
        resp = client.get("/api/orders", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_order_events_hub_publish_subscribe(self):
        """order_event_hub.publish() reaches subscriber."""
        from order_event_hub import order_event_hub

        sub = order_event_hub.subscribe(999)

        test_event = {"type": "order.updated", "order_id": "test_123", "status": "shipped"}
        order_event_hub.publish(999, test_event)

        received = sub.get(timeout=2)
        assert received["type"] == "order.updated"
        assert received["order_id"] == "test_123"

        order_event_hub.unsubscribe(999, sub)
