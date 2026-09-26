"""消息管线端到端与门控矩阵覆盖（2026-09-26 拆帧修复的配套加深）。

背景：data[0]-only bug 丢消息一周无人发现，因为覆盖停在"帧被处理"，
没有断言"买家聊天被持久化+进入回复决策"。本文件补齐：
- 端到端：多 item 积压帧（引导+买家聊天+引导）→ 买家消息落库(direction=2)
  + 进入防抖回复调度
- 门控矩阵：auto_reply=False / 过滤规则命中 / 商品非本账号 → 不进入回复调度
- 空同步包干净返回（无 ERROR 噪音）
"""
import asyncio
import base64
import json
from unittest import mock

import pytest
from loguru import logger as loguru_logger

from xianyu_messaging_mixins import MessagePipelineMixin

BUYER_ID = "buyer_001"
BUYER_NICK = "测试买家"
CHAT_TEXT = "在吗，这个还有货吗"


def _make_mixin():
    m = MessagePipelineMixin.__new__(MessagePipelineMixin)
    m.cookie_id = "1926782908"
    m.myid = "self_001"
    m._cookie_mgr = mock.MagicMock()
    m._cookie_mgr.get_cookie_status.return_value = True
    m._safe_str = staticmethod(lambda e: str(e))
    m.is_sync_package = lambda md: True
    m.pause_manager = mock.MagicMock()
    m.order_status_handler = None
    m.yifan_account_lock = asyncio.Lock()   # 生产由 __init__ 提供；聊天处理路径会触碰
    m.yifan_account_waiting = {}            # 同上
    # 兄弟 Mixin 的方法桩：订单/商品提取在本测试里不需要真实结果
    m._extract_order_id = lambda *a, **k: None
    m.extract_item_id_from_message = lambda *a, **k: None
    m._sanitize_buyer_nick = lambda text, **k: (str(text).strip() or None)
    return m


def _guide_item():
    payload = json.dumps(
        {"chatType": 1, "operation": {"content": {"sessionArouse": {"arouseChatScriptInfo": []}}}}
    )
    return {"data": base64.b64encode(payload.encode("utf-8")).decode("ascii")}


def _buyer_chat_item(text=CHAT_TEXT):
    inner = {
        "1": {
            "2": f"{BUYER_ID}@goofish",
            "5": 1727300000000,
            "10": {
                "reminderContent": text,
                "reminderTitle": BUYER_NICK,
                "senderUserId": BUYER_ID,
                "senderNick": BUYER_NICK,
                "bizTag": json.dumps({"sourceId": "S:1", "messageId": "mid-abc-001"}),
            },
        }
    }
    return {"data": base64.b64encode(json.dumps(inner).encode("utf-8")).decode("ascii")}


def _frame(items, mid="mid-1"):
    return {
        "headers": {"mid": mid, "sid": "sid-1", "app-key": "k"},
        "body": {"syncPushPackage": {"data": items}},
    }


def _route(route="user_chat", allow=True, system=False):
    return {
        "route": route,
        "order_status_signal": None,
        "should_notify": False,
        "allow_auto_reply": allow,
        "is_system_message": system,
        "is_group_message": False,
        "message_direction": 2,
        "content_type": 1,
        "card_title": "",
    }


class _Hub:
    """patch 点容器：db 单例方法与 chat_event_hub.publish_chat_message。"""

    def __init__(self):
        self.saved = []
        self.debounced = []

    def install(self, monkeypatch):
        from db_manager import db_manager as _db_instance
        import chat_event_hub

        def _save(**kwargs):
            self.saved.append(kwargs)
            return len(self.saved)

        monkeypatch.setattr(_db_instance, "save_chat_message", _save)
        monkeypatch.setattr(_db_instance, "update_buyer_nick_by_buyer_id", lambda *a, **k: None)
        monkeypatch.setattr(chat_event_hub, "publish_chat_message", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_multi_item_frame_with_buyer_chat_persists_and_schedules_reply(monkeypatch):
    """端到端回归（本 bug 的直接验收）：积压帧=引导+买家聊天+引导，
    买家聊天必须被持久化(direction=2)并进入防抖回复调度。"""
    hub = _Hub()
    hub.install(monkeypatch)
    m = _make_mixin()
    m._classify_message_route = lambda **k: _route()
    m._apply_message_filters = mock.AsyncMock(return_value={"skip_auto_reply": False, "rules": []})
    m._is_item_owned_by_self = mock.AsyncMock(return_value=True)
    m._schedule_debounced_reply = mock.AsyncMock()

    records = []
    hid = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        await m.handle_message(_frame([_guide_item(), _buyer_chat_item(), _guide_item()]), mock.AsyncMock(), "mid-e2e")
    finally:
        loguru_logger.remove(hid)

    incoming = [s for s in hub.saved if s.get("direction") == 2]
    assert len(incoming) == 1, f"买家聊天未落库: {hub.saved}"
    assert incoming[0]["content"] == CHAT_TEXT
    assert incoming[0]["sender_id"] == BUYER_ID
    assert m._schedule_debounced_reply.await_count == 1
    assert any("拆帧逐条处理" in r for r in records)


@pytest.mark.asyncio
async def test_gate_auto_reply_disabled_skips_debounce(monkeypatch):
    hub = _Hub()
    hub.install(monkeypatch)
    m = _make_mixin()
    m._classify_message_route = lambda **k: _route(allow=False)
    m._apply_message_filters = mock.AsyncMock(return_value={"skip_auto_reply": False, "rules": []})
    m._is_item_owned_by_self = mock.AsyncMock(return_value=True)
    m._schedule_debounced_reply = mock.AsyncMock()

    await m.handle_message(_frame([_buyer_chat_item()]), mock.AsyncMock(), "mid-g1")

    assert len(hub.saved) == 1          # 消息仍落库
    assert m._schedule_debounced_reply.await_count == 0  # 但不进入回复调度


@pytest.mark.asyncio
async def test_gate_filter_rule_skips_debounce(monkeypatch):
    hub = _Hub()
    hub.install(monkeypatch)
    m = _make_mixin()
    m._classify_message_route = lambda **k: _route()
    m._apply_message_filters = mock.AsyncMock(
        return_value={"skip_auto_reply": True, "rules": [{"id": 7, "name": "测试规则"}]}
    )
    m._is_item_owned_by_self = mock.AsyncMock(return_value=True)
    m._schedule_debounced_reply = mock.AsyncMock()

    records = []
    hid = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        await m.handle_message(_frame([_buyer_chat_item()]), mock.AsyncMock(), "mid-g2")
    finally:
        loguru_logger.remove(hid)

    assert m._schedule_debounced_reply.await_count == 0
    assert any("测试规则" in r and "跳过自动回复" in r for r in records)


@pytest.mark.asyncio
async def test_gate_item_not_owned_skips_debounce(monkeypatch):
    hub = _Hub()
    hub.install(monkeypatch)
    m = _make_mixin()
    m._classify_message_route = lambda **k: _route()
    m._apply_message_filters = mock.AsyncMock(return_value={"skip_auto_reply": False, "rules": []})
    m._is_item_owned_by_self = mock.AsyncMock(return_value=False)
    m._schedule_debounced_reply = mock.AsyncMock()

    records = []
    hid = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        await m.handle_message(_frame([_buyer_chat_item()]), mock.AsyncMock(), "mid-g3")
    finally:
        loguru_logger.remove(hid)

    assert m._schedule_debounced_reply.await_count == 0
    assert any("非本账号所有" in r for r in records)


@pytest.mark.asyncio
async def test_empty_sync_data_returns_cleanly(monkeypatch):
    m = _make_mixin()
    records = []
    hid = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        await m.handle_message(_frame([]), mock.AsyncMock(), "mid-empty")
    finally:
        loguru_logger.remove(hid)

    assert not [r for r in records if " ERROR " in r]
    assert any("空同步包" in r for r in records)


@pytest.mark.asyncio
async def test_priority_scan_covers_all_items(monkeypatch):
    """积压帧里聊天在第二位时，优先级也应被判为聊天档（旧实现只看 data[0]）。"""
    m = _make_mixin()
    frame = _frame([_guide_item(), _buyer_chat_item("这个能便宜点吗")])
    priority = m._get_message_priority(frame)
    assert priority == 2
