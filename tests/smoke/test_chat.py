"""Smoke tests for chat event hub."""
import pytest


class TestChatEvents:
    """Chat event hub smoke tests."""

    def test_chat_hub_publish_and_subscribe(self):
        """chat_event_hub.publish() reaches subscriber."""
        from chat_event_hub import chat_event_hub

        sub = chat_event_hub.subscribe(42)

        test_event = {
            "type": "chat.message",
            "chat_id": "chat_abc",
            "content": "hello from test",
            "timestamp": 1000,
        }
        chat_event_hub.publish(42, test_event)

        received = sub.get(timeout=2)
        assert received["type"] == "chat.message"
        assert received["content"] == "hello from test"

        chat_event_hub.unsubscribe(42, sub)

    def test_chat_hub_unsubscribe_stops_events(self):
        """After unsubscribe, events no longer arrive."""
        from chat_event_hub import chat_event_hub
        import queue

        sub = chat_event_hub.subscribe(55)
        chat_event_hub.unsubscribe(55, sub)

        chat_event_hub.publish(55, {"type": "chat.message", "content": "orphan"})

        with pytest.raises(queue.Empty):
            sub.get_nowait()

    def test_order_hub_publish_and_subscribe(self):
        """order_event_hub.publish() reaches subscriber."""
        from order_event_hub import order_event_hub

        sub = order_event_hub.subscribe(99)
        test_event = {"type": "order.updated", "order_id": "test_123"}
        order_event_hub.publish(99, test_event)

        received = sub.get(timeout=2)
        assert received["type"] == "order.updated"
        order_event_hub.unsubscribe(99, sub)

    def test_order_hub_unsubscribe_stops_events(self):
        """order_event_hub unsubscribe stops delivery."""
        from order_event_hub import order_event_hub
        import queue

        sub = order_event_hub.subscribe(77)
        order_event_hub.unsubscribe(77, sub)
        order_event_hub.publish(77, {"type": "order.updated"})

        with pytest.raises(queue.Empty):
            sub.get_nowait()
