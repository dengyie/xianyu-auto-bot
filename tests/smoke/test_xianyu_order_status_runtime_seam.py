"""Smoke tests for Xianyu runtime order-status wiring."""

import base64
import collections
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


def _make_detail_refresh_live(order_status_handler):
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "runtime-detail-cookie"
    live.cookies_str = "unb=runtime-detail-cookie; cookie2=test"
    live.order_status_handler = order_status_handler
    live.order_detail_retry_tasks = {}
    live._order_detail_locks = collections.defaultdict(mock.AsyncMock)
    live._order_detail_lock_times = {}
    live._safe_str = lambda exc: str(exc)
    live._apply_bargain_amount_override = (
        lambda order_id, item_id, amount, amount_source, existing_order=None, item_config=None: (amount, amount_source)
    )
    live._should_reject_order_detail_status_update = lambda **_kwargs: False
    live._should_accept_order_detail_status_correction = lambda *_args, **_kwargs: False
    live._resolve_external_order_status = lambda current_status, incoming_status, source: incoming_status
    live._select_buyer_identity_for_order_write = (
        lambda order_id, **kwargs: (
            kwargs.get("incoming_buyer_id"),
            kwargs.get("incoming_buyer_nick"),
            False,
        )
    )
    return live


def _make_auto_delivery_live(order_status_handler):
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "runtime-auto-delivery-cookie"
    live.user_id = 2
    live.myid = "seller-self"
    live.order_status_handler = order_status_handler
    live._safe_str = lambda exc: str(exc)
    live.fetch_order_detail_info = mock.AsyncMock(return_value=None)
    live._build_delivery_steps = lambda content, _desc: [{"type": "text", "content": content}]
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


@pytest.mark.asyncio
async def test_handle_message_uses_terminal_red_reminder_runtime_shortcut():
    order_status_handler = mock.Mock()
    live = _make_runtime_live(order_status_handler)

    decoded_message = _make_runtime_status_message(
        reminder_content="交易关闭提醒",
        sender_user_id="self-user",
        sid="chat-runtime-terminal-red@goofish",
        item_id="item-runtime-terminal-red",
        red_reminder="交易关闭",
    )
    message_data = _make_sync_message_data(decoded_message)
    websocket = _FakeWebSocket()

    with mock.patch.object(live, "is_sync_package", return_value=True):
        await live.handle_message(message_data, websocket, msg_id="phase31-terminal-red")

    order_status_handler.handle_red_reminder_order_status.assert_called_once()
    _, kwargs = order_status_handler.handle_red_reminder_order_status.call_args
    assert kwargs["red_reminder"] == "交易关闭"
    assert kwargs["message"] == decoded_message
    assert kwargs["user_id"] == "self-user"
    assert kwargs["cookie_id"] == "runtime-seam-cookie"
    assert kwargs["match_context"] == {
        "sid": None,
        "buyer_id": "self-user",
        "item_id": "item-runtime-terminal-red",
    }
    order_status_handler.handle_system_message.assert_not_called()
    order_status_handler.handle_red_reminder_message.assert_not_called()
    assert websocket.sent_payloads


@pytest.mark.asyncio
async def test_fetch_order_detail_info_bridges_successful_refresh_into_handler_followups(mocker):
    order_status_handler = mock.Mock()
    order_status_handler.handle_order_detail_fetched_status.return_value = True
    live = _make_detail_refresh_live(order_status_handler)

    fetched_detail = {
        "title": "detail title",
        "order_status": "shipped",
        "order_status_source": "structured",
        "spec_parse_mode": "no_spec",
        "quantity": "2",
        "amount": "18.50",
        "amount_source": "structured",
        "platform_created_at": "2026-06-17 08:00:00",
        "platform_paid_at": "2026-06-17 08:05:00",
        "platform_completed_at": None,
    }

    fetch_order_detail_simple = mock.AsyncMock(return_value=fetched_detail)
    mocker.patch("utils.order_detail_fetcher.fetch_order_detail_simple", fetch_order_detail_simple)

    fake_db = mock.Mock()
    fake_db.get_item_info.return_value = None
    fake_db.get_order_by_id.return_value = {
        "order_id": "detail-order-1",
        "buyer_id": "buyer-detail-1",
        "buyer_nick": "detail buyer",
        "order_status": "pending_ship",
        "amount": "18.50",
    }
    fake_db._normalize_order_status.side_effect = lambda value: value
    fake_db.get_cookie_by_id.return_value = {"id": "runtime-detail-cookie", "value": "cookie"}
    fake_db.insert_or_update_order.return_value = True
    mocker.patch("db_manager.db_manager", fake_db)

    result = await live.fetch_order_detail_info(
        order_id="detail-order-1",
        item_id="detail-item-1",
        buyer_id="buyer-detail-1",
        sid="detail-chat-1@goofish",
        buyer_nick="detail buyer",
        buyer_id_source="message",
    )

    assert result == fetched_detail
    fetch_order_detail_simple.assert_awaited_once_with(
        "detail-order-1",
        "unb=runtime-detail-cookie; cookie2=test",
        headless=True,
        force_refresh=False,
        cookie_id_for_log="runtime-detail-cookie",
    )

    fake_db.insert_or_update_order.assert_called_once()
    _, kwargs = fake_db.insert_or_update_order.call_args
    assert kwargs["order_id"] == "detail-order-1"
    assert kwargs["item_id"] == "detail-item-1"
    assert kwargs["buyer_id"] == "buyer-detail-1"
    assert kwargs["buyer_nick"] == "detail buyer"
    assert kwargs["sid"] == "detail-chat-1@goofish"
    assert kwargs["cookie_id"] == "runtime-detail-cookie"
    assert kwargs["order_status"] == "shipped"
    assert kwargs["quantity"] == "2"
    assert kwargs["amount"] == "18.50"

    order_status_handler.handle_order_detail_fetched_status.assert_called_once_with(
        order_id="detail-order-1",
        cookie_id="runtime-detail-cookie",
        context="订单详情已拉取",
    )
    order_status_handler.on_order_details_fetched.assert_called_once_with("detail-order-1")
    assert order_status_handler.method_calls == [
        mock.call.handle_order_detail_fetched_status(
            order_id="detail-order-1",
            cookie_id="runtime-detail-cookie",
            context="订单详情已拉取",
        ),
        mock.call.on_order_details_fetched("detail-order-1"),
    ]


@pytest.mark.asyncio
async def test_fetch_order_detail_info_returns_detail_when_handler_followup_raises(mocker):
    order_status_handler = mock.Mock()
    order_status_handler.handle_order_detail_fetched_status.return_value = True
    order_status_handler.on_order_details_fetched.side_effect = RuntimeError("pending queue boom")
    live = _make_detail_refresh_live(order_status_handler)

    fetched_detail = {
        "title": "detail title",
        "order_status": "shipped",
        "order_status_source": "structured",
        "spec_parse_mode": "no_spec",
        "quantity": "1",
        "amount": "8.80",
        "amount_source": "structured",
        "platform_created_at": "2026-06-17 09:00:00",
        "platform_paid_at": "2026-06-17 09:01:00",
        "platform_completed_at": None,
    }

    mocker.patch(
        "utils.order_detail_fetcher.fetch_order_detail_simple",
        mock.AsyncMock(return_value=fetched_detail),
    )

    fake_db = mock.Mock()
    fake_db.get_item_info.return_value = None
    fake_db.get_order_by_id.return_value = {
        "order_id": "detail-order-2",
        "buyer_id": "buyer-detail-2",
        "buyer_nick": "detail buyer 2",
        "order_status": "pending_ship",
        "amount": "8.80",
    }
    fake_db._normalize_order_status.side_effect = lambda value: value
    fake_db.get_cookie_by_id.return_value = {"id": "runtime-detail-cookie", "value": "cookie"}
    fake_db.insert_or_update_order.return_value = True
    mocker.patch("db_manager.db_manager", fake_db)

    result = await live.fetch_order_detail_info(
        order_id="detail-order-2",
        item_id="detail-item-2",
        buyer_id="buyer-detail-2",
        sid="detail-chat-2@goofish",
        buyer_nick="detail buyer 2",
        buyer_id_source="message",
    )

    assert result == fetched_detail
    fake_db.insert_or_update_order.assert_called_once()
    order_status_handler.handle_order_detail_fetched_status.assert_called_once_with(
        order_id="detail-order-2",
        cookie_id="runtime-detail-cookie",
        context="订单详情已拉取",
    )
    order_status_handler.on_order_details_fetched.assert_called_once_with("detail-order-2")


@pytest.mark.asyncio
async def test_fetch_order_detail_info_skips_handler_followups_when_persistence_fails(mocker):
    order_status_handler = mock.Mock()
    live = _make_detail_refresh_live(order_status_handler)

    fetched_detail = {
        "title": "detail title",
        "order_status": "shipped",
        "order_status_source": "structured",
        "spec_parse_mode": "no_spec",
        "quantity": "1",
        "amount": "12.30",
        "amount_source": "structured",
        "platform_created_at": "2026-06-17 10:00:00",
        "platform_paid_at": "2026-06-17 10:02:00",
        "platform_completed_at": None,
    }

    mocker.patch(
        "utils.order_detail_fetcher.fetch_order_detail_simple",
        mock.AsyncMock(return_value=fetched_detail),
    )

    fake_db = mock.Mock()
    fake_db.get_item_info.return_value = None
    fake_db.get_order_by_id.return_value = {
        "order_id": "detail-order-3",
        "buyer_id": "buyer-detail-3",
        "buyer_nick": "detail buyer 3",
        "order_status": "pending_ship",
        "amount": "12.30",
    }
    fake_db._normalize_order_status.side_effect = lambda value: value
    fake_db.get_cookie_by_id.return_value = {"id": "runtime-detail-cookie", "value": "cookie"}
    fake_db.insert_or_update_order.return_value = False
    mocker.patch("db_manager.db_manager", fake_db)

    result = await live.fetch_order_detail_info(
        order_id="detail-order-3",
        item_id="detail-item-3",
        buyer_id="buyer-detail-3",
        sid="detail-chat-3@goofish",
        buyer_nick="detail buyer 3",
        buyer_id_source="message",
    )

    assert result == fetched_detail
    fake_db.insert_or_update_order.assert_called_once()
    order_status_handler.handle_order_detail_fetched_status.assert_not_called()
    order_status_handler.on_order_details_fetched.assert_not_called()


@pytest.mark.asyncio
async def test_auto_delivery_returns_content_when_basic_info_handler_raises(mocker):
    order_status_handler = mock.Mock()
    order_status_handler.handle_order_basic_info_status.side_effect = RuntimeError("basic info boom")
    live = _make_auto_delivery_live(order_status_handler)

    fake_db = mock.Mock()
    fake_db.get_item_info.return_value = {
        "item_title": "Demo item",
        "item_detail": "detail body",
        "is_multi_spec": False,
    }
    fake_db.get_item_multi_spec_status.return_value = False
    fake_db.get_delivery_rules_by_keyword.return_value = [
        {
            "id": 301,
            "keyword": "Demo item",
            "card_name": "Text Card",
            "card_type": "text",
            "text_content": "delivery body",
            "card_description": "",
            "card_id": 9001,
            "card_delay_seconds": 0,
            "spec_name": "",
            "spec_value": "",
            "spec_name_2": "",
            "spec_value_2": "",
        }
    ]
    fake_db.get_cookie_by_id.return_value = {"id": "runtime-auto-delivery-cookie", "value": "cookie"}
    fake_db.get_order_by_id.return_value = None
    fake_db.insert_or_update_order.return_value = True
    mocker.patch("db_manager.db_manager", fake_db)

    result = await live._auto_delivery(
        item_id="item-auto-1",
        item_title="Demo item",
        order_id="order-auto-1",
        send_user_id="buyer-auto-1",
        send_user_name="buyer name",
        include_meta=True,
    )

    assert result["success"] is True
    assert result["content"] == "delivery body"
    assert result["rule_id"] == 301
    assert result["card_type"] == "text"
    fake_db.insert_or_update_order.assert_called_once_with(
        order_id="order-auto-1",
        item_id="item-auto-1",
        buyer_id="buyer-auto-1",
        buyer_nick="buyer name",
        cookie_id="runtime-auto-delivery-cookie",
    )
    order_status_handler.handle_order_basic_info_status.assert_called_once_with(
        order_id="order-auto-1",
        cookie_id="runtime-auto-delivery-cookie",
        context="自动发货-基本信息",
    )


@pytest.mark.asyncio
async def test_auto_delivery_returns_content_when_basic_info_persistence_fails(mocker):
    order_status_handler = mock.Mock()
    live = _make_auto_delivery_live(order_status_handler)

    fake_db = mock.Mock()
    fake_db.get_item_info.return_value = {
        "item_title": "Demo item",
        "item_detail": "detail body",
        "is_multi_spec": False,
    }
    fake_db.get_item_multi_spec_status.return_value = False
    fake_db.get_delivery_rules_by_keyword.return_value = [
        {
            "id": 302,
            "keyword": "Demo item",
            "card_name": "Text Card 2",
            "card_type": "text",
            "text_content": "delivery body 2",
            "card_description": "",
            "card_id": 9002,
            "card_delay_seconds": 0,
            "spec_name": "",
            "spec_value": "",
            "spec_name_2": "",
            "spec_value_2": "",
        }
    ]
    fake_db.get_cookie_by_id.return_value = {"id": "runtime-auto-delivery-cookie", "value": "cookie"}
    fake_db.get_order_by_id.return_value = None
    fake_db.insert_or_update_order.return_value = False
    mocker.patch("db_manager.db_manager", fake_db)

    result = await live._auto_delivery(
        item_id="item-auto-2",
        item_title="Demo item",
        order_id="order-auto-2",
        send_user_id="buyer-auto-2",
        send_user_name="buyer two",
        include_meta=True,
    )

    assert result["success"] is True
    assert result["content"] == "delivery body 2"
    assert result["rule_id"] == 302
    assert result["card_type"] == "text"
    fake_db.insert_or_update_order.assert_called_once_with(
        order_id="order-auto-2",
        item_id="item-auto-2",
        buyer_id="buyer-auto-2",
        buyer_nick="buyer two",
        cookie_id="runtime-auto-delivery-cookie",
    )
    order_status_handler.handle_order_basic_info_status.assert_not_called()


@pytest.mark.asyncio
async def test_auto_delivery_skips_basic_info_prewrite_when_order_exists(mocker):
    order_status_handler = mock.Mock()
    live = _make_auto_delivery_live(order_status_handler)

    fake_db = mock.Mock()
    fake_db.get_item_info.return_value = {
        "item_title": "Demo item",
        "item_detail": "detail body",
        "is_multi_spec": False,
    }
    fake_db.get_item_multi_spec_status.return_value = False
    fake_db.get_delivery_rules_by_keyword.return_value = [
        {
            "id": 303,
            "keyword": "Demo item",
            "card_name": "Text Card Existing",
            "card_type": "text",
            "text_content": "delivery body existing",
            "card_description": "",
            "card_id": 9003,
            "card_delay_seconds": 0,
            "spec_name": "",
            "spec_value": "",
            "spec_name_2": "",
            "spec_value_2": "",
        }
    ]
    fake_db.get_cookie_by_id.return_value = {"id": "runtime-auto-delivery-cookie", "value": "cookie"}
    fake_db.get_order_by_id.return_value = {
        "order_id": "order-auto-existing",
        "item_id": "item-auto-existing",
        "buyer_id": "buyer-auto-existing",
        "order_status": "pending_ship",
    }
    mocker.patch("db_manager.db_manager", fake_db)

    result = await live._auto_delivery(
        item_id="item-auto-existing",
        item_title="Demo item",
        order_id="order-auto-existing",
        send_user_id="buyer-auto-existing",
        send_user_name="buyer existing",
        include_meta=True,
    )

    assert result["success"] is True
    assert result["content"] == "delivery body existing"
    assert result["rule_id"] == 303
    assert result["card_type"] == "text"
    fake_db.insert_or_update_order.assert_not_called()
    order_status_handler.handle_order_basic_info_status.assert_not_called()
