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
