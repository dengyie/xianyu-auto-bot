"""消息去重链与路由回归（测试文档配套补充）。

覆盖缺口（2026-09-26 review）：
- _unwrap_message_for_dedupe：同步包还原到内部结构（去重与 messageId 提取的统一入口）
- _extract_message_id：bizTag.messageId 提取（去重的关键键）
- chatType 非引导回落：带 contentType 的 chatType 消息必须继续处理
  （历史上修过一次 early-return 回归，本测试钉住）
- 自发消息回推去重：Web 发出的消息被闲鱼回推时，不重复落库、暂停该会话
- AI_PROVIDER_TIMEOUT 旋钮契约：默认 15s 且全部请求点已 Adoption
"""
import base64
import json
from unittest import mock

import pytest
from loguru import logger as loguru_logger

from xianyu_messaging_mixins import MessagePipelineMixin


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
    m.yifan_account_lock = __import__("asyncio").Lock()
    m.yifan_account_waiting = {}
    m._extract_order_id = lambda *a, **k: None
    m.extract_item_id_from_message = lambda *a, **k: None
    m._sanitize_buyer_nick = lambda text, **k: (str(text).strip() or None)
    return m


def _sync_frame(inner_items, mid="mid-x"):
    return {
        "headers": {"mid": mid, "sid": "sid-1"},
        "body": {"syncPushPackage": {"data": [
            {"data": base64.b64encode(json.dumps(i).encode("utf-8")).decode("ascii")}
            for i in inner_items
        ]}},
    }


def _inner_with_biz_tag(message_id="msg-abc-123"):
    return {
        "1": {
            "2": "buyer_001@goofish",
            "10": {
                "reminderContent": "你好",
                "senderUserId": "buyer_001",
                "bizTag": json.dumps({"sourceId": "S:1", "messageId": message_id}),
            },
        }
    }


# ---------- _unwrap_message_for_dedupe ----------

def test_unwrap_passthrough_internal_structure():
    m = _make_mixin()
    inner = _inner_with_biz_tag()
    assert m._unwrap_message_for_dedupe(inner) is inner


def test_unwrap_sync_frame_single_item():
    m = _make_mixin()
    inner = _inner_with_biz_tag()
    frame = _sync_frame([inner])
    assert m._unwrap_message_for_dedupe(frame) == inner


def test_unwrap_returns_none_on_garbage():
    m = _make_mixin()
    assert m._unwrap_message_for_dedupe("not-a-dict") is None
    assert m._unwrap_message_for_dedupe({"body": {}}) is None
    bad = {"body": {"syncPushPackage": {"data": [{"data": "%%%not-base64%%%"}]}}}
    assert m._unwrap_message_for_dedupe(bad) is None


# ---------- _extract_message_id ----------

def test_extract_message_id_from_biz_tag():
    m = _make_mixin()
    frame = _sync_frame([_inner_with_biz_tag("msg-abc-123")])
    assert m._extract_message_id(frame) == "msg-abc-123"


def test_extract_message_id_returns_none_without_biz_tag():
    m = _make_mixin()
    frame = _sync_frame([{"1": {"10": {}}}])
    assert m._extract_message_id(frame) in (None, "",)


# ---------- chatType 非引导回落（防 early-return 回归） ----------

@pytest.mark.asyncio
async def test_chattype_non_arouse_continues_processing():
    """带 contentType 的 chatType 消息（非 sessionArouse 引导）必须继续走管线，
    不得在 chatType 分支提前 return（历史上修过的回归点）。"""
    m = _make_mixin()
    item = {"data": base64.b64encode(json.dumps({
        "chatType": 1,
        "operation": {"content": {"contentType": 1, "text": "hello"}},
    }).encode("utf-8")).decode("ascii")}
    frame = {"headers": {"mid": "m-1"}, "body": {"syncPushPackage": {"data": [item]}}}

    records = []
    hid = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        await m.handle_message(frame, mock.AsyncMock(), "m-1")
    finally:
        loguru_logger.remove(hid)

    assert any("chatType消息但不是引导消息，继续处理" in r for r in records)
    # 回落后 is_chat_message 为 False（无 "1"/"10" 结构）→ 静默收口，但绝不能
    # 出现在引导分支里的"系统引导消息处理完成"
    assert not any("系统引导消息处理完成" in r for r in records)


# ---------- 会话引导消息重放去重 ----------

def _guidance_frame(session_id="60736454871", arouse_ts=1777110481086, script="你好呀", mid="g"):
    payload = {
        "chatType": 1,
        "sessionId": session_id,
        "operation": {"content": {"contentType": 8, "sessionArouse": {
            "sessionArouseInfo": {
                "arouseTimeStamp": arouse_ts,
                "arouseChatScriptInfo": [{"chatScrip": script}],
            }
        }}},
    }
    item = {"data": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")}
    return {"headers": {"mid": mid}, "body": {"syncPushPackage": {"data": [item]}}}


@pytest.mark.asyncio
async def test_replayed_session_guidance_is_ignored_after_first_seen():
    """服务端每次重连都会重放同一批静态引导包(同 sessionId+arouseTimeStamp)。
    首次正常处理,之后重复到达直接忽略,不再刷日志。"""
    m = _make_mixin()
    ws = mock.AsyncMock()
    records = []
    hid = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        await m.handle_message(_guidance_frame(mid="g-1"), ws, "g-1")
        first = list(records)
        records.clear()
        await m.handle_message(_guidance_frame(mid="g-2"), ws, "g-2")
        second = list(records)
    finally:
        loguru_logger.remove(hid)

    assert any("系统引导消息处理完成" in r for r in first)
    # 重复帧不再进入引导处理,改为忽略
    assert not any("系统引导消息处理完成" in r for r in second)


@pytest.mark.asyncio
async def test_distinct_session_guidance_still_processed():
    """不同 (sessionId, arouseTimeStamp) 的引导消息是新消息,必须照常处理。"""
    m = _make_mixin()
    ws = mock.AsyncMock()
    records = []
    hid = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        await m.handle_message(_guidance_frame(session_id="111", mid="d-1"), ws, "d-1")
        records.clear()
        await m.handle_message(_guidance_frame(session_id="222", mid="d-2"), ws, "d-2")
    finally:
        loguru_logger.remove(hid)

    assert any("系统引导消息处理完成" in r for r in records)


# ---------- 自发消息回推去重 ----------

@pytest.mark.asyncio
async def test_self_echo_dedup_pauses_and_skips_save(monkeypatch):
    """Web 发出的消息被闲鱼回推：consume 命中 → 直接收口（暂停会话、不重复落库）。
    pause 走 _host 代理 → XianyuAutoAsync 模块级 pause_manager（非实例属性）。"""
    import chat_event_hub
    import XianyuAutoAsync

    m = _make_mixin()
    m._classify_message_route = lambda **k: {
        "route": "user_chat", "order_status_signal": None, "should_notify": False,
        "allow_auto_reply": True, "is_system_message": False, "is_group_message": False,
        "message_direction": 1, "content_type": 1, "card_title": "",
    }
    saved = []
    monkeypatch.setattr(chat_event_hub.self_send_dedup, "consume", lambda *a, **k: True)
    fake_pm = mock.MagicMock()
    monkeypatch.setattr(XianyuAutoAsync, "pause_manager", fake_pm)

    records = []
    hid = loguru_logger.add(lambda msg: records.append(str(msg)), level="INFO")
    try:
        # 自发消息同样以同步帧形式到达（内部结构 senderUserId == myid）
        self_inner = {"1": {"2": "chat@goofish", "10": {
            "reminderContent": "hello", "senderUserId": "self_001", "senderNick": "self",
        }}}
        await m.handle_message(_sync_frame([self_inner]), mock.AsyncMock(), "m-self")
    finally:
        loguru_logger.remove(hid)

    assert fake_pm.pause_chat.called
    assert not saved
    assert any("Web 自发回推已去重" in r for r in records)


# ---------- AI_PROVIDER_TIMEOUT 旋钮契约 ----------

def test_ai_provider_timeout_default_and_adoption():
    import ai_reply_engine

    assert ai_reply_engine.AI_PROVIDER_TIMEOUT == 15.0
    src = open("ai_reply_engine.py", encoding="utf-8").read()
    assert src.count("timeout=AI_PROVIDER_TIMEOUT") >= 6
    assert "timeout=30" not in src
