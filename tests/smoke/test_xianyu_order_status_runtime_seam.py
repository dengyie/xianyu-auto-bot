"""Smoke tests for Xianyu runtime order-status wiring."""

import base64
import json
from unittest import mock

import pytest

from XianyuAutoAsync import XianyuLive


class _FakeWebSocket:
    def __init__(self):
        self.sent_payloads = []

    async def send(self, payload):
        self.sent_payloads.append(json.loads(payload))


def _make_sync_message_data(message):
    encoded = base64.b64encode(json.dumps(message).encode("utf-8")).decode("utf-8")
    return {
        "headers": {},
        "body": {
            "syncPushPackage": {
                "data": [
                    {
                        "data": encoded,
                    }
                ]
            }
        },
    }


def _make_runtime_status_message(*, reminder_content, sender_user_id="self-user", sid="chat-runtime-30@goofish", item_id="item-runtime-30", red_reminder=None):
    message = {
        "1": {
            "2": sid,
            "5": 1_717_171_717_000,
            "7": 1,
            "10": {
                "senderUserId": sender_user_id,
                "senderNick": "runtime self",
                "reminderContent": reminder_content,
                "reminderUrl": f"https://www.goofish.com/order?itemId={item_id}",
                "bizTag": "SECURITY_RUNTIME_TEST",
            },
        },
    }
    if red_reminder is not None:
        message["3"] = {
            "redReminder": red_reminder,
            "userId": sender_user_id,
        }
    return message


def _make_runtime_live(order_status_handler):
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "runtime-seam-cookie"
    live.order_status_handler = order_status_handler
    live._cookie_mgr = None
    live.last_message_received_time = 0
    live._safe_str = lambda exc: str(exc)
    live._extract_order_id = lambda _message, _message_data=None: None
    live._extract_order_message_context = lambda _message, msg_id=None: {}
    live._preload_basic_order_info = lambda *_args, **_kwargs: False
    live.is_chat_message = lambda _message: True
    live.myid = "self-user"

    async def fake_fetch_order_detail_info(*_args, **_kwargs):
        return None

    async def fake_maybe_force_refresh_order_detail_for_signal(*_args, **_kwargs):
        return None

    live.fetch_order_detail_info = fake_fetch_order_detail_info
    live._maybe_force_refresh_order_detail_for_signal = fake_maybe_force_refresh_order_detail_for_signal
    return live


@pytest.mark.asyncio
async def test_handle_message_passes_order_match_context_into_order_status_handler():
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "runtime-seam-cookie"
    live.order_status_handler = mock.Mock()
    live._cookie_mgr = None
    live.last_message_received_time = 0
    live._safe_str = lambda exc: str(exc)
    live._extract_order_id = lambda _message, _message_data=None: "runtime-order-1"
    live._extract_order_message_context = lambda _message, msg_id=None: {
        "sid": "chat-runtime-1@goofish",
        "buyer_id": "buyer-runtime-1",
        "item_id": "item-runtime-1",
        "buyer_nick": "runtime buyer",
        "buyer_id_source": "message",
    }
    live._preload_basic_order_info = lambda *_args, **_kwargs: False
    live.is_chat_message = lambda _message: False

    async def fake_fetch_order_detail_info(*_args, **_kwargs):
        return None

    live.fetch_order_detail_info = fake_fetch_order_detail_info

    decoded_message = {
        "1": {
            "5": 1_717_171_717_000,
        }
    }
    message_data = _make_sync_message_data(decoded_message)
    websocket = _FakeWebSocket()

    with mock.patch.object(live, "is_sync_package", return_value=True):
        await live.handle_message(message_data, websocket, msg_id="phase29")

    live.order_status_handler.on_order_id_extracted.assert_called_once_with(
        "runtime-order-1",
        "runtime-seam-cookie",
        decoded_message,
        match_context={
            "sid": "chat-runtime-1@goofish",
            "buyer_id": "buyer-runtime-1",
            "item_id": "item-runtime-1",
        },
    )
    assert websocket.sent_payloads


@pytest.mark.asyncio
async def test_handle_message_passes_match_context_into_direct_system_status_handler():
    order_status_handler = mock.Mock()
    order_status_handler.handle_system_message.return_value = True
    live = _make_runtime_live(order_status_handler)

    decoded_message = _make_runtime_status_message(
        reminder_content="[你关闭了订单，钱款已原路退还]",
        sender_user_id="self-user",
        sid="chat-runtime-system@goofish",
        item_id="item-runtime-system",
    )
    message_data = _make_sync_message_data(decoded_message)
    websocket = _FakeWebSocket()

    with mock.patch.object(live, "is_sync_package", return_value=True), \
         mock.patch.object(
             live,
             "_classify_message_route",
             return_value={
                 "route": "order_status",
                 "order_status_signal": "cancelled",
                 "should_notify": False,
                 "allow_auto_reply": False,
                 "is_system_message": True,
                 "is_group_message": False,
                 "message_direction": 1,
                 "content_type": 6,
             },
         ):
        await live.handle_message(message_data, websocket, msg_id="phase30-system")

    order_status_handler.handle_system_message.assert_called_once()
    _, kwargs = order_status_handler.handle_system_message.call_args
    assert kwargs["message"] == decoded_message
    assert kwargs["send_message"] == "[你关闭了订单，钱款已原路退还]"
    assert kwargs["cookie_id"] == "runtime-seam-cookie"
    assert kwargs["match_context"] == {
        "sid": "chat-runtime-system@goofish",
        "buyer_id": "self-user",
        "item_id": "item-runtime-system",
    }
    assert websocket.sent_payloads


@pytest.mark.asyncio
async def test_handle_message_passes_match_context_into_red_reminder_runtime_fallback():
    order_status_handler = mock.Mock()
    order_status_handler.handle_system_message.return_value = False
    live = _make_runtime_live(order_status_handler)

    decoded_message = _make_runtime_status_message(
        reminder_content="订单状态提醒",
        sender_user_id="self-user",
        sid="chat-runtime-red@goofish",
        item_id="item-runtime-red",
        red_reminder="等待系统确认",
    )
    message_data = _make_sync_message_data(decoded_message)
    websocket = _FakeWebSocket()

    with mock.patch.object(live, "is_sync_package", return_value=True), \
         mock.patch.object(
             live,
             "_classify_message_route",
             return_value={
                 "route": "order_status",
                 "order_status_signal": "cancelled",
                 "should_notify": False,
                 "allow_auto_reply": False,
                 "is_system_message": True,
                 "is_group_message": False,
                 "message_direction": 1,
                 "content_type": 6,
             },
         ):
        await live.handle_message(message_data, websocket, msg_id="phase30-red")

    order_status_handler.handle_system_message.assert_called_once()
    order_status_handler.handle_red_reminder_message.assert_called_once()
    _, kwargs = order_status_handler.handle_red_reminder_message.call_args
    assert kwargs["message"] == decoded_message
    assert kwargs["red_reminder"] == "等待系统确认"
    assert kwargs["user_id"] == "self-user"
    assert kwargs["cookie_id"] == "runtime-seam-cookie"
    assert kwargs["match_context"] == {
        "sid": "chat-runtime-red@goofish",
        "buyer_id": "self-user",
        "item_id": "item-runtime-red",
    }
    assert websocket.sent_payloads
