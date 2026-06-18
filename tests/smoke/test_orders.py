"""Smoke tests for order management."""
import pytest


def _insert_order(db, *, order_id, cookie_id, item_id="item-1", status="pending_ship"):
    ok = db.insert_or_update_order(
        order_id=order_id,
        cookie_id=cookie_id,
        item_id=item_id,
        buyer_id=f"buyer-{order_id}",
        sid=f"chat-{order_id}@goofish",
        quantity=1,
        order_status=status,
    )
    assert ok is True


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

    def test_order_list_and_delete_are_scoped_to_cookie_owner(self, client, auth, user_auth):
        """Users only list and delete orders attached to their own cookies."""
        from reply_server import db_manager

        assert db_manager.save_cookie("admin_order_cookie", "unb=admin", user_id=1)
        assert db_manager.save_cookie("user_order_cookie", "unb=user", user_id=2)
        _insert_order(db_manager, order_id="admin-visible-order", cookie_id="admin_order_cookie")
        _insert_order(db_manager, order_id="user-visible-order", cookie_id="user_order_cookie")

        user_list = client.get("/api/orders", headers=user_auth)
        foreign_delete = client.delete("/api/orders/admin-visible-order", headers=user_auth)
        order_after_foreign_delete = db_manager.get_order_by_id("admin-visible-order")
        owner_delete = client.delete("/api/orders/admin-visible-order", headers=auth)
        admin_list_after_delete = client.get("/api/orders", headers=auth)
        user_list_after_delete = client.get("/api/orders", headers=user_auth)

        assert user_list.status_code == 200
        assert [order["order_id"] for order in user_list.json()["data"]] == ["user-visible-order"]
        assert foreign_delete.status_code == 403
        assert order_after_foreign_delete is not None
        assert owner_delete.status_code == 200
        assert db_manager.get_order_by_id("admin-visible-order") is None
        assert admin_list_after_delete.json()["data"] == []
        assert [order["order_id"] for order in user_list_after_delete.json()["data"]] == ["user-visible-order"]
